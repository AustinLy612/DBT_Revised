"""LLM client with DeepSeek-first, Doubao-on-overload routing.

DeepSeek V4 Flash is the default provider. Doubao Seed is used only after a
DeepSeek load/rate-limit error, and callers may persist that fallback for the
rest of the teaching session through ``on_provider_fallback``.
"""

import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger("dbt_platform.knowledge_base.rag")

# Thread-local session pool — one requests.Session per thread for connection reuse.
# gthread workers share the same process, so a module-level global is not safe.
_local = threading.local()

DEFAULT_PROVIDER = "deepseek"
FALLBACK_PROVIDER = "doubao"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_ARK_MODEL = "doubao-seed-2.1-turbo"
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 4096
API_TIMEOUT_SECONDS = 120
DEEPSEEK_CHAT_ENDPOINT = "/v1/chat/completions"
ARK_CHAT_ENDPOINT = "/chat/completions"
MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.5  # seconds, multiplied by 2^attempt
TRANSIENT_STATUS_CODES = {429, 502, 503, 529}
OVERLOAD_STATUS_CODES = {429, 503, 529}
OVERLOAD_MARKERS = (
    "overload",
    "overloaded",
    "server is busy",
    "service busy",
    "high traffic",
    "rate limit",
    "rate_limit",
    "too many requests",
    "负载",
    "繁忙",
    "限流",
)
FallbackCallback = Callable[[str, Exception], None]


def _get_session() -> requests.Session:
    """Return a thread-local requests.Session with connection pooling."""
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=0,
        )
        _local.session.mount("https://", adapter)
        _local.session.mount("http://", adapter)
    return _local.session


class ConfigurationError(RuntimeError):
    """Raised when an LLM provider is not properly configured."""


class APIError(RuntimeError):
    """Raised when an LLM provider returns an error."""


class ProviderOverloadedError(APIError):
    """DeepSeek is overloaded/rate-limited and may safely fall back."""


def _get_ark_api_key() -> str:
    key = settings.ARK_AGENT_PLAN_API_KEY or settings.ARK_API_KEY
    if not key:
        raise ConfigurationError(
            "ARK_AGENT_PLAN_API_KEY is not set. Configure it to use LLM capabilities."
        )
    return key


def _get_ark_base_url() -> str:
    return settings.ARK_LLM_BASE_URL.rstrip("/")


def _get_ark_model(model: str | None) -> str:
    return model or settings.ARK_LLM_MODEL or DEFAULT_ARK_MODEL


def _get_deepseek_api_key() -> str:
    key = settings.DEEPSEEK_API_KEY
    if not key:
        raise ConfigurationError(
            "DEEPSEEK_API_KEY is not set. Configure it to use LLM capabilities."
        )
    return key


def _get_deepseek_base_url() -> str:
    return settings.DEEPSEEK_BASE_URL.rstrip("/")


def _get_deepseek_model(model: str | None) -> str:
    return model or getattr(settings, "DEEPSEEK_MODEL", "") or DEFAULT_DEEPSEEK_MODEL


def _normalize_provider(provider: str | None) -> str:
    normalized = (provider or DEFAULT_PROVIDER).strip().lower()
    if normalized not in (DEFAULT_PROVIDER, FALLBACK_PROVIDER):
        raise ConfigurationError(f"Unsupported LLM provider: {provider}")
    return normalized


def _is_overload_error(status_code: int, detail: str) -> bool:
    detail_lower = (detail or "").lower()
    return status_code in OVERLOAD_STATUS_CODES or any(
        marker in detail_lower for marker in OVERLOAD_MARKERS
    )


def _ark_chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    response_format: dict[str, str] | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call Ark Agent Plan ChatCompletion and return the first choice message.

    Args:
        messages: List of {"role": "...", "content": "..."} dicts.
        model: Ark model ID; defaults to ``ARK_LLM_MODEL``.
        temperature: Sampling temperature (0-1, lower = more deterministic).
        max_tokens: Maximum output tokens.
        response_format: Optional {"type": "json_object"} for JSON mode.
        extra_body: Optional extra fields to merge into the request body.

    Returns:
        Dict with keys: "role", "content", "finish_reason", "usage".

    Raises:
        ConfigurationError: If DEEPSEEK_API_KEY is not set.
        APIError: If the API returns an error or non-200 status.
    """
    api_key = _get_ark_api_key()
    base_url = _get_ark_base_url()
    url = f"{base_url}{ARK_CHAT_ENDPOINT}"
    resolved_model = _get_ark_model(model)

    body: dict[str, Any] = {
        "model": resolved_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "thinking": {"type": settings.ARK_LLM_THINKING},
    }

    if response_format is not None:
        body["response_format"] = response_format

    if extra_body is not None:
        body.update(extra_body)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    logger.debug(
        "Ark Agent Plan LLM call: model=%s, msg_count=%d",
        resolved_model,
        len(messages),
    )

    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = _get_session().post(url, json=body, headers=headers, timeout=API_TIMEOUT_SECONDS)
        except requests.Timeout:
            last_error = APIError(f"Ark LLM request timed out after {API_TIMEOUT_SECONDS}s")
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("Ark LLM timeout, retrying in %.1fs (attempt %d/%d)", delay, attempt + 1, MAX_RETRIES)
                time.sleep(delay)
                continue
            raise last_error
        except requests.ConnectionError as exc:
            last_error = APIError(f"Ark LLM connection failed: {exc}")
            last_error.__cause__ = exc
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("Ark LLM connection error, retrying in %.1fs (attempt %d/%d)", delay, attempt + 1, MAX_RETRIES)
                time.sleep(delay)
                continue
            raise last_error

        # Retry on transient HTTP errors (502/503 server errors, 529 overload)
        if resp.status_code in (429, 502, 503, 529):
            error_detail = _extract_error(resp)
            last_error = APIError(f"Ark LLM API returned {resp.status_code}: {error_detail}")
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Ark LLM transient error %s, retrying in %.1fs (attempt %d/%d)",
                    resp.status_code, delay, attempt + 1, MAX_RETRIES,
                )
                time.sleep(delay)
                continue
            raise last_error

        if resp.status_code != 200:
            raise APIError(
                f"Ark LLM API returned {resp.status_code}: {_extract_error(resp)}"
            )

        data = resp.json()
        return _parse_response(data)

    raise last_error  # type: ignore[misc]


def _ark_chat_completion_stream(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    extra_body: dict[str, Any] | None = None,
):
    """Stream Ark Agent Plan ChatCompletion and yield content deltas via SSE.

    Yields each incremental content chunk as a plain string. The final
    yield is the full accumulated text (so callers can parse it for
    structured fields after the stream ends).

    Args:
        messages: List of {"role": "...", "content": "..."} dicts.
        model: Ark model ID; defaults to ``ARK_LLM_MODEL``.
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
        extra_body: Optional extra fields to merge into the request body.

    Yields:
        str — incremental content deltas followed by sentinel "[STREAM_DONE]"
        followed by the full accumulated text.

    Raises:
        ConfigurationError: If DEEPSEEK_API_KEY is not set.
        APIError: If the API returns an error.
    """
    api_key = _get_ark_api_key()
    base_url = _get_ark_base_url()
    url = f"{base_url}{ARK_CHAT_ENDPOINT}"
    resolved_model = _get_ark_model(model)

    body: dict[str, Any] = {
        "model": resolved_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "thinking": {"type": settings.ARK_LLM_THINKING},
    }

    if extra_body is not None:
        body.update(extra_body)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    logger.debug(
        "Ark Agent Plan streaming call: model=%s, msg_count=%d",
        resolved_model,
        len(messages),
    )

    resp: requests.Response | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = _get_session().post(
                url,
                json=body,
                headers=headers,
                timeout=API_TIMEOUT_SECONDS,
                stream=True,
            )
        except requests.Timeout:
            if attempt >= MAX_RETRIES:
                raise APIError(
                    f"Ark LLM streaming request timed out after {API_TIMEOUT_SECONDS}s"
                )
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "Ark LLM stream timeout, retrying in %.1fs (attempt %d/%d)",
                delay,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(delay)
            continue
        except requests.ConnectionError as exc:
            if attempt >= MAX_RETRIES:
                raise APIError(f"Ark LLM streaming connection failed: {exc}") from exc
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "Ark LLM stream connection error, retrying in %.1fs (attempt %d/%d)",
                delay,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(delay)
            continue

        if resp.status_code == 200:
            break
        if resp.status_code not in (429, 502, 503, 529) or attempt >= MAX_RETRIES:
            raise APIError(
                f"Ark LLM streaming API returned {resp.status_code}: {_extract_error(resp)}"
            )

        error_detail = _extract_error(resp)
        resp.close()
        delay = RETRY_BASE_DELAY * (2 ** attempt)
        logger.warning(
            "Ark LLM stream transient error %s (%s), retrying in %.1fs "
            "(attempt %d/%d)",
            resp.status_code,
            error_detail,
            delay,
            attempt + 1,
            MAX_RETRIES,
        )
        time.sleep(delay)

    if resp is None:
        raise APIError("Ark LLM streaming request failed before receiving a response")

    accumulated: list[str] = []
    # Ark currently returns ``text/event-stream`` without a charset. Requests
    # therefore defaults to ISO-8859-1, which turns UTF-8 Chinese into
    # mojibake. Decode the raw SSE bytes explicitly.
    for raw_line in resp.iter_lines(decode_unicode=False):
        if not raw_line:
            continue
        line = (
            raw_line.decode("utf-8", errors="replace")
            if isinstance(raw_line, bytes)
            else raw_line
        )
        if not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            break
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        choices = data.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        content = delta.get("content", "")
        if content:
            accumulated.append(content)
            yield content

    full_text = "".join(accumulated)
    logger.debug("Ark LLM stream complete: %d chars", len(full_text))
    yield "[STREAM_DONE]"
    yield full_text


def _deepseek_chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    response_format: dict[str, str] | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call DeepSeek, raising a distinct error only for load-related failures."""
    api_key = _get_deepseek_api_key()
    url = f"{_get_deepseek_base_url()}{DEEPSEEK_CHAT_ENDPOINT}"
    resolved_model = _get_deepseek_model(model)
    body: dict[str, Any] = {
        "model": resolved_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        body["response_format"] = response_format
    if extra_body is not None:
        body.update(extra_body)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    logger.debug(
        "DeepSeek LLM call: model=%s, msg_count=%d",
        resolved_model,
        len(messages),
    )

    last_error: APIError | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = _get_session().post(
                url,
                json=body,
                headers=headers,
                timeout=API_TIMEOUT_SECONDS,
            )
        except requests.Timeout:
            last_error = APIError(
                f"DeepSeek LLM request timed out after {API_TIMEOUT_SECONDS}s"
            )
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "DeepSeek timeout, retrying in %.1fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                time.sleep(delay)
                continue
            raise last_error
        except requests.ConnectionError as exc:
            last_error = APIError(f"DeepSeek LLM connection failed: {exc}")
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "DeepSeek connection error, retrying in %.1fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    MAX_RETRIES,
                )
                time.sleep(delay)
                continue
            raise last_error from exc

        if resp.status_code == 200:
            return _parse_response(resp.json())

        error_detail = _extract_error(resp)
        error_cls = (
            ProviderOverloadedError
            if _is_overload_error(resp.status_code, error_detail)
            else APIError
        )
        last_error = error_cls(
            f"DeepSeek LLM API returned {resp.status_code}: {error_detail}"
        )
        if resp.status_code in TRANSIENT_STATUS_CODES and attempt < MAX_RETRIES:
            delay = RETRY_BASE_DELAY * (2**attempt)
            logger.warning(
                "DeepSeek transient error %s, retrying in %.1fs (attempt %d/%d)",
                resp.status_code,
                delay,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(delay)
            continue
        raise last_error

    raise last_error or APIError("DeepSeek LLM request failed")


def _deepseek_chat_completion_stream(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    extra_body: dict[str, Any] | None = None,
) -> Iterator[str]:
    """Stream DeepSeek content and classify pre-stream overload failures."""
    api_key = _get_deepseek_api_key()
    url = f"{_get_deepseek_base_url()}{DEEPSEEK_CHAT_ENDPOINT}"
    resolved_model = _get_deepseek_model(model)
    body: dict[str, Any] = {
        "model": resolved_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if extra_body is not None:
        body.update(extra_body)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    logger.debug(
        "DeepSeek streaming call: model=%s, msg_count=%d",
        resolved_model,
        len(messages),
    )

    resp: requests.Response | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = _get_session().post(
                url,
                json=body,
                headers=headers,
                timeout=API_TIMEOUT_SECONDS,
                stream=True,
            )
        except requests.Timeout:
            if attempt >= MAX_RETRIES:
                raise APIError(
                    f"DeepSeek LLM streaming request timed out after "
                    f"{API_TIMEOUT_SECONDS}s"
                )
            delay = RETRY_BASE_DELAY * (2**attempt)
            logger.warning(
                "DeepSeek stream timeout, retrying in %.1fs (attempt %d/%d)",
                delay,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(delay)
            continue
        except requests.ConnectionError as exc:
            if attempt >= MAX_RETRIES:
                raise APIError(
                    f"DeepSeek LLM streaming connection failed: {exc}"
                ) from exc
            delay = RETRY_BASE_DELAY * (2**attempt)
            logger.warning(
                "DeepSeek stream connection error, retrying in %.1fs "
                "(attempt %d/%d)",
                delay,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(delay)
            continue

        if resp.status_code == 200:
            break

        error_detail = _extract_error(resp)
        is_overload = _is_overload_error(resp.status_code, error_detail)
        error_cls = ProviderOverloadedError if is_overload else APIError
        error = error_cls(
            f"DeepSeek LLM streaming API returned {resp.status_code}: "
            f"{error_detail}"
        )
        if resp.status_code in TRANSIENT_STATUS_CODES and attempt < MAX_RETRIES:
            resp.close()
            delay = RETRY_BASE_DELAY * (2**attempt)
            logger.warning(
                "DeepSeek stream transient error %s, retrying in %.1fs "
                "(attempt %d/%d)",
                resp.status_code,
                delay,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(delay)
            continue
        raise error

    if resp is None:
        raise APIError(
            "DeepSeek LLM streaming request failed before receiving a response"
        )

    accumulated: list[str] = []
    for raw_line in resp.iter_lines(decode_unicode=False):
        if not raw_line:
            continue
        line = (
            raw_line.decode("utf-8", errors="replace")
            if isinstance(raw_line, bytes)
            else raw_line
        )
        if not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            break
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if data.get("error"):
            error = data["error"]
            detail = (
                error.get("message", str(error))
                if isinstance(error, dict)
                else str(error)
            )
            if not accumulated and _is_overload_error(200, detail):
                raise ProviderOverloadedError(
                    f"DeepSeek LLM streaming API overloaded: {detail}"
                )
            raise APIError(f"DeepSeek LLM streaming API error: {detail}")
        choices = data.get("choices", [])
        if not choices:
            continue
        content = choices[0].get("delta", {}).get("content", "")
        if content:
            accumulated.append(content)
            yield content

    full_text = "".join(accumulated)
    logger.debug("DeepSeek LLM stream complete: %d chars", len(full_text))
    yield "[STREAM_DONE]"
    yield full_text


def _notify_fallback(
    callback: FallbackCallback | None,
    error: ProviderOverloadedError,
) -> None:
    logger.warning(
        "DeepSeek overload persisted after retries; falling back to Doubao: %s",
        error,
    )
    if callback is None:
        return
    try:
        callback(FALLBACK_PROVIDER, error)
    except Exception:
        # Provider fallback must remain available even if persistence/logging fails.
        logger.exception("Failed to persist LLM provider fallback")


def chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    response_format: dict[str, str] | None = None,
    extra_body: dict[str, Any] | None = None,
    provider: str | None = None,
    on_provider_fallback: FallbackCallback | None = None,
) -> dict[str, Any]:
    """Use DeepSeek by default; use Doubao only after DeepSeek overload."""
    resolved_provider = _normalize_provider(provider)
    kwargs = {
        "messages": messages,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": response_format,
        "extra_body": extra_body,
    }
    if resolved_provider == FALLBACK_PROVIDER:
        return _ark_chat_completion(**kwargs)
    try:
        return _deepseek_chat_completion(**kwargs)
    except ProviderOverloadedError as exc:
        _notify_fallback(on_provider_fallback, exc)
        # A model override for DeepSeek must not leak into the Doubao request.
        kwargs["model"] = None
        return _ark_chat_completion(**kwargs)


def chat_completion_stream(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    extra_body: dict[str, Any] | None = None,
    provider: str | None = None,
    on_provider_fallback: FallbackCallback | None = None,
) -> Iterator[str]:
    """Stream DeepSeek by default; restart on Doubao only before any text."""
    resolved_provider = _normalize_provider(provider)
    kwargs = {
        "messages": messages,
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "extra_body": extra_body,
    }
    if resolved_provider == FALLBACK_PROVIDER:
        yield from _ark_chat_completion_stream(**kwargs)
        return

    emitted_content = False
    try:
        for item in _deepseek_chat_completion_stream(**kwargs):
            if item not in ("[STREAM_DONE]",):
                emitted_content = emitted_content or bool(item)
            yield item
    except ProviderOverloadedError as exc:
        if emitted_content:
            # Restarting after visible output would duplicate/corrupt the answer.
            raise
        _notify_fallback(on_provider_fallback, exc)
        kwargs["model"] = None
        yield from _ark_chat_completion_stream(**kwargs)


def _extract_error(resp: requests.Response) -> str:
    """Best-effort error extraction from a non-200 response."""
    try:
        body = resp.json()
        if "error" in body:
            err = body["error"]
            if isinstance(err, dict):
                return err.get("message", str(err))
            return str(err)
        return resp.text[:500]
    except (json.JSONDecodeError, KeyError):
        return resp.text[:500]


def _parse_response(data: dict[str, Any]) -> dict[str, Any]:
    """Extract the first choice from an OpenAI-compatible chat response."""
    if "choices" not in data or len(data["choices"]) == 0:
        raise APIError(f"Ark LLM returned no choices: {json.dumps(data, ensure_ascii=False)[:500]}")

    choice = data["choices"][0]
    finish_reason = choice.get("finish_reason", "unknown")
    message = choice.get("message", {})
    content = message.get("content", "")
    role = message.get("role", "assistant")

    result = {
        "role": role,
        "content": content,
        "finish_reason": finish_reason,
        "usage": data.get("usage", {}),
    }

    if finish_reason == "length":
        logger.warning("Ark LLM response truncated (max_tokens reached)")

    return result
