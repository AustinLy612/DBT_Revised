"""Volcengine Ark Agent Plan LLM client wrapper.

Provides OpenAI-compatible chat completions using Doubao Seed, including
structured JSON output and SSE streaming.
"""

import json
import logging
import threading
import time
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger("dbt_platform.knowledge_base.rag")

# Thread-local session pool — one requests.Session per thread for connection reuse.
# gthread workers share the same process, so a module-level global is not safe.
_local = threading.local()

DEFAULT_MODEL = "doubao-seed-2.1-turbo"
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 4096
API_TIMEOUT_SECONDS = 120
CHAT_ENDPOINT = "/chat/completions"
MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.5  # seconds, multiplied by 2^attempt


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
    """Raised when the Ark LLM client is not properly configured."""


class APIError(RuntimeError):
    """Raised when the Ark LLM API returns an error."""


def _get_api_key() -> str:
    key = settings.ARK_AGENT_PLAN_API_KEY or settings.ARK_API_KEY
    if not key:
        raise ConfigurationError(
            "ARK_AGENT_PLAN_API_KEY is not set. Configure it to use LLM capabilities."
        )
    return key


def _get_base_url() -> str:
    return settings.ARK_LLM_BASE_URL.rstrip("/")


def _get_model(model: str | None) -> str:
    return model or settings.ARK_LLM_MODEL or DEFAULT_MODEL


def chat_completion(
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
    api_key = _get_api_key()
    base_url = _get_base_url()
    url = f"{base_url}{CHAT_ENDPOINT}"
    resolved_model = _get_model(model)

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


def chat_completion_stream(
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
    api_key = _get_api_key()
    base_url = _get_base_url()
    url = f"{base_url}{CHAT_ENDPOINT}"
    resolved_model = _get_model(model)

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
