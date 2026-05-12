"""Media services — MiniMax image generation, TTS, and ASR API wrappers.

Follows the same patterns as knowledge_base/rag/llm_client.py:
- Reads MINIMAX_API_KEY / MINIMAX_BASE_URL from Django settings
- Raises ConfigurationError if API key is missing
- Raises APIError on non-200, timeout, or connection errors
- All functions return structured results

Image files and raw audio are NOT persisted. Only metadata is stored.
"""

from __future__ import annotations

import io
import json
import logging
import time
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger("dbt_platform.media_app")

# ── Endpoints ──
IMAGE_GENERATION_ENDPOINT = "/v1/image_generation"
TTS_ENDPOINT = "/v1/t2a_v2"
ASR_ENDPOINT = "/v1/audio/transcription"

# ── Default models ──
DEFAULT_IMAGE_MODEL = "image-01-live"
DEFAULT_IMAGE_LIVE_MODEL = "image-01-live"
DEFAULT_TTS_MODEL = "speech-2.8-hd"
DEFAULT_TTS_HD_MODEL = "speech-2.8-hd"

API_TIMEOUT_SECONDS = 120


class ConfigurationError(RuntimeError):
    """Raised when the MiniMax client is not properly configured."""


class APIError(RuntimeError):
    """Raised when the MiniMax API returns an error."""


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _get_api_key() -> str:
    key = settings.MINIMAX_API_KEY
    if not key:
        raise ConfigurationError(
            "MINIMAX_API_KEY is not set. Configure it in .env to use media services."
        )
    return key


def _get_base_url() -> str:
    return settings.MINIMAX_BASE_URL.rstrip("/")


def _extract_error(resp: requests.Response) -> str:
    try:
        body = resp.json()
        if "error" in body:
            return body["error"].get("message", str(body["error"]))
        if "base_resp" in body:
            return body["base_resp"].get("status_msg", str(body))
        return resp.text[:500]
    except (json.JSONDecodeError, KeyError):
        return resp.text[:500]


# ═══════════════════════════════════════════════════════════════
# Image Generation
# ═══════════════════════════════════════════════════════════════


def generate_image(
    prompt: str,
    *,
    model: str = DEFAULT_IMAGE_MODEL,
    n: int = 1,
    size: str = "1024x1024",
) -> dict[str, Any]:
    """Call MiniMax Image Generation API.

    Args:
        prompt: Image generation prompt (Chinese supported).
        model: Model ID (image-01 or image-01-live).
        n: Number of images to generate (1-9).
        size: Image size (e.g. "1024x1024", "768x1024").

    Returns:
        Dict with keys: "urls" (list[str]), "model", "usage".

    Raises:
        ConfigurationError: If MINIMAX_API_KEY is not set.
        APIError: If the API returns an error.
    """
    api_key = _get_api_key()
    base_url = _get_base_url()
    url = f"{base_url}{IMAGE_GENERATION_ENDPOINT}"

    body = {
        "model": model,
        "prompt": prompt,
        "n": n,
        "size": size,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    logger.info("MiniMax image generation: model=%s, prompt=%.100s...", model, prompt)

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=API_TIMEOUT_SECONDS)
    except requests.Timeout:
        raise APIError(f"MiniMax image API timed out after {API_TIMEOUT_SECONDS}s")
    except requests.ConnectionError as exc:
        raise APIError(f"MiniMax image API connection failed: {exc}") from exc
    except requests.RequestException as exc:
        raise APIError(f"MiniMax image API request failed: {exc}") from exc

    if resp.status_code != 200:
        error_detail = _extract_error(resp)
        logger.error("MiniMax image API returned %s: %s", resp.status_code, error_detail)
        raise APIError(f"MiniMax image API returned {resp.status_code}: {error_detail}")

    data = resp.json()

    # Check for business-level error
    base_resp = data.get("base_resp", {})
    if base_resp.get("status_code") and base_resp.get("status_code") != 0:
        logger.error("MiniMax image API error: %s", base_resp)
        raise APIError(
            f"MiniMax image error {base_resp.get('status_code')}: {base_resp.get('status_msg', 'unknown')}"
        )

    urls = []
    if "data" in data:
        image_urls = data["data"].get("image_urls", [])
        urls.extend(image_urls)

    return {
        "urls": urls,
        "model": model,
        "usage": data.get("usage", {}),
    }


# ═══════════════════════════════════════════════════════════════
# Text-to-Speech (TTS)
# ═══════════════════════════════════════════════════════════════


def synthesize_speech(
    text: str,
    *,
    model: str = DEFAULT_TTS_MODEL,
    voice: str = "",
    speed: float = 1.0,
    vol: float = 1.0,
    return_audio_bytes: bool = True,
) -> dict[str, Any]:
    """Call MiniMax TTS API to synthesize speech from text.

    Args:
        text: Text to synthesize (max ~5000 chars).
        model: Model ID (speech-2.8-turbo or speech-2.8-hd).
        voice: Voice ID (empty = default).
        speed: Speech speed (0.5-2.0).
        vol: Volume (0.1-2.0).
        return_audio_bytes: If True, download audio and return bytes.
            If False, return the temporary URL.

    Returns:
        Dict with keys: "audio_bytes" (bytes | None), "audio_url" (str),
        "model", "usage", "format".

    Raises:
        ConfigurationError: If MINIMAX_API_KEY is not set.
        APIError: If the API returns an error.
    """
    api_key = _get_api_key()
    base_url = _get_base_url()
    url = f"{base_url}{TTS_ENDPOINT}"

    body = {
        "model": model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": voice or "Chinese (Mandarin)_Warm_Girl",
            "speed": speed,
            "vol": vol,
        },
        "audio_setting": {
            "format": "mp3",
            "sample_rate": 24000,
        },
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    logger.info("MiniMax TTS: model=%s, text_len=%d", model, len(text))

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=API_TIMEOUT_SECONDS)
    except requests.Timeout:
        raise APIError(f"MiniMax TTS API timed out after {API_TIMEOUT_SECONDS}s")
    except requests.ConnectionError as exc:
        raise APIError(f"MiniMax TTS API connection failed: {exc}") from exc

    if resp.status_code != 200:
        error_detail = _extract_error(resp)
        logger.error("MiniMax TTS API returned %s: %s", resp.status_code, error_detail)
        raise APIError(f"MiniMax TTS API returned {resp.status_code}: {error_detail}")

    data = resp.json()

    # Check for business-level error (HTTP 200 but base_resp error)
    base_resp = data.get("base_resp", {})
    if base_resp.get("status_code") and base_resp.get("status_code") != 0:
        logger.error("MiniMax TTS API error: %s", base_resp)
        raise APIError(
            f"MiniMax TTS error {base_resp.get('status_code')}: {base_resp.get('status_msg', 'unknown')}"
        )

    audio_bytes = None
    audio_url = ""

    # MiniMax TTS returns audio as hex-encoded bytes, URL, or base64
    if "data" in data and "audio" in data["data"]:
        audio_field = data["data"]["audio"]
        if audio_field.startswith("http://") or audio_field.startswith("https://"):
            audio_url = audio_field
            if return_audio_bytes:
                audio_bytes = _download_audio(audio_url)
        elif audio_field.startswith("data:audio"):
            # base64-encoded inline audio with data URI prefix
            import base64
            b64_data = audio_field.split(",", 1)[-1]
            audio_bytes = base64.b64decode(b64_data)
        elif len(audio_field) > 100:
            # Hex-encoded or raw base64 audio bytes (MiniMax China returns hex)
            import base64
            try:
                audio_bytes = bytes.fromhex(audio_field)
            except ValueError:
                # Try base64 decoding as fallback
                try:
                    audio_bytes = base64.b64decode(audio_field)
                except Exception:
                    logger.warning("Unable to decode audio field (len=%d)", len(audio_field))
    elif "audio_url" in data:
        audio_url = data["audio_url"]
        if return_audio_bytes:
            audio_bytes = _download_audio(audio_url)
    elif "extra_info" in data and "audio_url" in data["extra_info"]:
        audio_url = data["extra_info"]["audio_url"]
        if return_audio_bytes:
            audio_bytes = _download_audio(audio_url)

    # Try raw response as fallback
    if not audio_bytes and not audio_url and resp.content:
        content_type = resp.headers.get("content-type", "")
        if "audio" in content_type:
            audio_bytes = resp.content

    return {
        "audio_bytes": audio_bytes,
        "audio_url": audio_url,
        "model": model,
        "format": "mp3",
        "usage": data.get("usage", {}),
    }


def _download_audio(audio_url: str, timeout: int = 60) -> bytes | None:
    """Download audio from a temporary URL. Returns None on failure."""
    try:
        r = requests.get(audio_url, timeout=timeout)
        if r.status_code == 200:
            return r.content
        logger.warning("Failed to download audio from %s: status=%s", audio_url[:80], r.status_code)
    except requests.RequestException as exc:
        logger.warning("Failed to download audio from %s: %s", audio_url[:80], exc)
    return None


# ═══════════════════════════════════════════════════════════════
# Automatic Speech Recognition (ASR)
# ═══════════════════════════════════════════════════════════════
#
# Provider: Volcengine (火山引擎) 豆包语音 ASR
# Flow:     POST audio → get job_id → poll for result → return text
#
# Credentials: set VOLCENGINE_API_KEY in .env (from 火山引擎控制台 → 语音技术)

VOLCENGINE_ASR_HOST = "https://openspeech.bytedance.com"
VOLCENGINE_ASR_RESOURCE_ID = "volc.bigasr.sauc.duration"
ASR_POLL_MAX_RETRIES = 30
ASR_POLL_INTERVAL = 0.2  # seconds


def _get_volcengine_api_key() -> str:
    """Return volcengine API key or raise ConfigurationError."""
    key = getattr(settings, "VOLCENGINE_API_KEY", "")
    if not key:
        raise ConfigurationError(
            "Volcengine ASR not configured. Set VOLCENGINE_API_KEY in .env"
        )
    return key


def transcribe_audio(
    audio_bytes: bytes,
    *,
    audio_format: str = "wav",
    model: str = "",
) -> dict[str, Any]:
    """Transcribe speech to text via volcengine ASR.

    Args:
        audio_bytes: Raw audio data.
        audio_format: Audio format string (wav, mp3, webm, etc.).
        model: ASR model ID (empty = auto-detect).

    Returns:
        Dict with keys: "transcribed_text" (str), "model" (str), "usage".
    """
    api_key = _get_volcengine_api_key()

    mime_map = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "webm": "audio/webm",
        "ogg": "audio/ogg",
        "m4a": "audio/mp4",
    }
    content_type = mime_map.get(audio_format, "audio/wav")

    headers = {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": VOLCENGINE_ASR_RESOURCE_ID,
        "X-Api-Request-Id": str(time.time_ns()),
        "Content-Type": content_type,
    }

    logger.info("Volcengine ASR submit: audio_bytes=%d, format=%s", len(audio_bytes), audio_format)

    # Step 1: Submit audio
    try:
        resp = requests.post(
            f"{VOLCENGINE_ASR_HOST}/api/v1/vc/submit",
            data=audio_bytes,
            headers=headers,
            timeout=API_TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        raise APIError(f"Volcengine ASR submit timed out after {API_TIMEOUT_SECONDS}s")
    except requests.ConnectionError as exc:
        raise APIError(f"Volcengine ASR submit connection failed: {exc}") from exc

    if resp.status_code != 200:
        error_detail = _extract_error(resp)
        raise APIError(f"Volcengine ASR submit returned {resp.status_code}: {error_detail}")

    result = resp.json()
    if result.get("code") != 0:
        raise APIError(f"Volcengine ASR submit failed: [{result.get('code')}] {result.get('message')}")

    job_id = result.get("id", "")
    if not job_id:
        raise APIError("Volcengine ASR submit returned no job ID")

    logger.info("Volcengine ASR job submitted: %s", job_id)

    # Step 2: Poll for result
    import time as _time
    last_duration = -1.0
    stale_count = 0
    for attempt in range(ASR_POLL_MAX_RETRIES):
        _time.sleep(ASR_POLL_INTERVAL)
        try:
            poll_resp = requests.get(
                f"{VOLCENGINE_ASR_HOST}/api/v1/vc/query",
                params={"id": job_id},
                headers={
                    "X-Api-Key": api_key,
                    "X-Api-Resource-Id": VOLCENGINE_ASR_RESOURCE_ID,
                    "X-Api-Request-Id": str(time.time_ns()),
                },
                timeout=API_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning("Volcengine ASR poll attempt %d failed: %s", attempt + 1, exc)
            continue

        if poll_resp.status_code != 200:
            continue

        poll_result = poll_resp.json()
        if poll_result.get("code") != 0:
            continue

        utterances = poll_result.get("utterances", [])
        current_duration = poll_result.get("duration", 0)

        # Collect utterance texts
        texts = []
        for u in utterances:
            t = u.get("text", "").strip()
            if t:
                texts.append(t)

        if texts:
            transcribed = " ".join(texts)
            logger.info("Volcengine ASR complete: job=%s, text=%s", job_id, transcribed[:100])
            return {
                "transcribed_text": transcribed,
                "model": "volcengine-bigasr",
                "usage": {"duration": current_duration},
            }

        # Detect completion: duration stopped changing for 3 consecutive polls
        if current_duration > 0 and current_duration == last_duration:
            stale_count += 1
            if stale_count >= 3:
                # Processing is done but no speech detected
                logger.info("Volcengine ASR complete: job=%s, no speech detected", job_id)
                return {
                    "transcribed_text": "",
                    "model": "volcengine-bigasr",
                    "usage": {"duration": current_duration},
                }
        else:
            stale_count = 0
        last_duration = current_duration

    raise APIError(f"Volcengine ASR timed out waiting for result (job {job_id})")
