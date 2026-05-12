"""MiniMax LLM client wrapper.

Provides a clean interface for calling MiniMax chat completions with
structured output support.  The client is safe to instantiate even when
no API key is configured — it raises a clear ConfigurationError on first
use rather than failing silently.
"""

import json
import logging
import time
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger("dbt_platform.knowledge_base.rag")

DEFAULT_MODEL = "MiniMax-M2.7"
DEFAULT_TEMPERATURE = 0.3
DEFAULT_MAX_TOKENS = 4096
API_TIMEOUT_SECONDS = 60
CHAT_ENDPOINT = "/v1/text/chatcompletion_v2"
MAX_RETRIES = 2
RETRY_BASE_DELAY = 1.5  # seconds, multiplied by 2^attempt


class ConfigurationError(RuntimeError):
    """Raised when the MiniMax client is not properly configured."""


class APIError(RuntimeError):
    """Raised when the MiniMax API returns an error."""


def _get_api_key() -> str:
    key = settings.MINIMAX_API_KEY
    if not key:
        raise ConfigurationError(
            "MINIMAX_API_KEY is not set. Configure it in .env to use RAG capabilities."
        )
    return key


def _get_base_url() -> str:
    return settings.MINIMAX_BASE_URL.rstrip("/")


def minimax_chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    reply_format: str | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call MiniMax ChatCompletion API and return the first choice message.

    Args:
        messages: List of {"role": "...", "content": "..."} dicts.
        model: MiniMax model ID.
        temperature: Sampling temperature (0-1, lower = more deterministic).
        max_tokens: Maximum output tokens.
        response_format: Optional {"type": "json_object"} for JSON mode.
        extra_body: Optional extra fields to merge into the request body.

    Returns:
        Dict with keys: "role", "content", "finish_reason", "usage".

    Raises:
        ConfigurationError: If MINIMAX_API_KEY is not set.
        APIError: If the API returns an error or non-200 status.
    """
    api_key = _get_api_key()
    base_url = _get_base_url()
    url = f"{base_url}{CHAT_ENDPOINT}"

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "mask_sensitive_info": True,
    }

    if reply_format is not None:
        body["reply_format"] = reply_format

    if extra_body is not None:
        body.update(extra_body)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    logger.debug("MiniMax API call: model=%s, msg_count=%d", model, len(messages))

    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=API_TIMEOUT_SECONDS)
        except requests.Timeout:
            last_error = APIError(f"MiniMax API request timed out after {API_TIMEOUT_SECONDS}s")
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("MiniMax timeout, retrying in %.1fs (attempt %d/%d)", delay, attempt + 1, MAX_RETRIES)
                time.sleep(delay)
                continue
            raise last_error
        except requests.ConnectionError as exc:
            last_error = APIError(f"MiniMax API connection failed: {exc}")
            last_error.__cause__ = exc
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("MiniMax connection error, retrying in %.1fs (attempt %d/%d)", delay, attempt + 1, MAX_RETRIES)
                time.sleep(delay)
                continue
            raise last_error

        # Retry on transient HTTP errors (529 overload, 502/503 server errors)
        if resp.status_code in (529, 502, 503):
            error_detail = _extract_error(resp)
            last_error = APIError(f"MiniMax API returned {resp.status_code}: {error_detail}")
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "MiniMax transient error %s, retrying in %.1fs (attempt %d/%d)",
                    resp.status_code, delay, attempt + 1, MAX_RETRIES,
                )
                time.sleep(delay)
                continue
            raise last_error

        if resp.status_code != 200:
            raise APIError(
                f"MiniMax API returned {resp.status_code}: {_extract_error(resp)}"
            )

        data = resp.json()
        return _parse_response(data)

    raise last_error  # type: ignore[misc]


def minimax_chat_completion_stream(
    messages: list[dict[str, str]],
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    extra_body: dict[str, Any] | None = None,
):
    """Stream MiniMax ChatCompletion and yield content deltas via SSE.

    Yields each incremental content chunk as a plain string. The final
    yield is the full accumulated text (so callers can parse it for
    structured fields after the stream ends).

    Args:
        messages: List of {"role": "...", "content": "..."} dicts.
        model: MiniMax model ID.
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
        extra_body: Optional extra fields to merge into the request body.

    Yields:
        str — incremental content deltas followed by sentinel "[STREAM_DONE]"
        followed by the full accumulated text.

    Raises:
        ConfigurationError: If MINIMAX_API_KEY is not set.
        APIError: If the API returns an error.
    """
    api_key = _get_api_key()
    base_url = _get_base_url()
    url = f"{base_url}{CHAT_ENDPOINT}"

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "mask_sensitive_info": True,
        "stream": True,
    }

    if extra_body is not None:
        body.update(extra_body)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    logger.debug("MiniMax streaming API call: model=%s, msg_count=%d", model, len(messages))

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=API_TIMEOUT_SECONDS, stream=True)
    except requests.Timeout:
        raise APIError(f"MiniMax streaming API timed out after {API_TIMEOUT_SECONDS}s")
    except requests.ConnectionError as exc:
        raise APIError(f"MiniMax streaming API connection failed: {exc}") from exc

    if resp.status_code != 200:
        raise APIError(f"MiniMax streaming API returned {resp.status_code}: {_extract_error(resp)}")

    accumulated: list[str] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
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
    logger.debug("MiniMax stream complete: %d chars", len(full_text))
    yield "[STREAM_DONE]"
    yield full_text


def _extract_error(resp: requests.Response) -> str:
    """Best-effort error extraction from a non-200 response."""
    try:
        body = resp.json()
        if "error" in body:
            return body["error"].get("message", str(body["error"]))
        if "base_resp" in body:
            return body["base_resp"].get("status_msg", str(body))
        return resp.text[:500]
    except (json.JSONDecodeError, KeyError):
        return resp.text[:500]


def _parse_response(data: dict[str, Any]) -> dict[str, Any]:
    """Extract the first choice from a MiniMax chat completion response."""
    if "choices" not in data or len(data["choices"]) == 0:
        raise APIError(f"MiniMax returned no choices: {json.dumps(data, ensure_ascii=False)[:500]}")

    choice = data["choices"][0]
    finish_reason = choice.get("finish_reason", "unknown")
    message = choice.get("message", {})
    content = message.get("content", "")
    role = message.get("role", "assistant")
    reasoning = message.get("reasoning_content", "")

    result = {
        "role": role,
        "content": content,
        "finish_reason": finish_reason,
        "usage": data.get("usage", {}),
    }

    if reasoning:
        logger.debug("MiniMax reasoning: %s", reasoning[:200])

    if finish_reason == "max_output_tokens":
        logger.warning("MiniMax response truncated (max_tokens reached)")

    return result
