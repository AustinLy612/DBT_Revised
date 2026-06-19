"""Media services — image generation (Volcengine Jimeng), TTS (Volcengine), and ASR (Volcengine).

Image files and raw audio are NOT persisted. Only metadata is stored.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import io
import json
import logging
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger("dbt_platform.media_app")

# Thread-local session pool — one requests.Session per thread for connection reuse.
_local = threading.local()


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

# ── Redis TTS cache ──
_redis_client = None
TTS_CACHE_TTL_SECONDS = 3600  # 1 hour


def _get_redis():
    """Return a Redis client, or None if unavailable."""
    global _redis_client
    if _redis_client is None:
        try:
            import redis as redis_lib

            _redis_client = redis_lib.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD or None,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            _redis_client.ping()
        except Exception:
            logger.warning("Redis unavailable — TTS cache disabled")
            _redis_client = False
            return None
    if _redis_client is False:
        return None
    return _redis_client


def _tts_cache_key(text: str, voice: str) -> str:
    digest = hashlib.sha256(f"{text}|{voice}".encode()).hexdigest()[:16]
    return f"tts:audio:{digest}"


def _tts_cache_get(text: str, voice: str) -> bytes | None:
    client = _get_redis()
    if client is None:
        return None
    try:
        raw = client.get(_tts_cache_key(text, voice))
        if raw:
            return raw  # Redis returns bytes
    except Exception:
        pass
    return None


def _tts_cache_set(text: str, voice: str, audio: bytes) -> None:
    client = _get_redis()
    if client is None:
        return
    try:
        client.setex(_tts_cache_key(text, voice), TTS_CACHE_TTL_SECONDS, audio)
    except Exception:
        pass

# ── Endpoints ──
VISUAL_API_HOST = "visual.volcengineapi.com"
VISUAL_API_PATH = "/"
IMAGE_REQ_KEY = "jimeng_t2i_v31"
IMAGE_API_VERSION = "2022-08-31"
IMAGE_REGION = "cn-north-1"
IMAGE_SERVICE = "cv"
TTS_HOST = "https://openspeech.bytedance.com"
TTS_ENDPOINT = "/api/v3/tts/unidirectional"
TTS_RESOURCE_ID = "seed-tts-2.0"
ASR_ENDPOINT = "/v1/audio/transcription"

# ── Default models ──
DEFAULT_IMAGE_MODEL = "jimeng_t2i_v31"
DEFAULT_TTS_MODEL = "volcengine-tts"  # semantic label for logging

API_TIMEOUT_SECONDS = 120
IMAGE_MAX_RETRIES = 3
IMAGE_RETRY_BASE_DELAY = 2.0  # seconds, multiplied by 2^attempt
IMAGE_POLL_MAX_ATTEMPTS = 60       # 60 × 2s = 120s max wait
IMAGE_POLL_INTERVAL = 2.0          # seconds between polls


class ConfigurationError(RuntimeError):
	"""Raised when the image generation client is not properly configured."""


class APIError(RuntimeError):
	"""Raised when the image generation API returns an error."""


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _parse_image_api_key() -> tuple[str, str, str]:
	"""Parse VOLCENGINE_IMAGE_API_KEY into (access_key, secret_key, session_token).

	Supports two formats:
	  - Long-term key: AccessKeyId.SecretAccessKey  (2 parts, no session token)
	  - STS key:        AccessKeyId.SecretAccessKey.SessionToken  (3 parts)
	"""
	key = getattr(settings, "VOLCENGINE_IMAGE_API_KEY", "")
	if not key:
		raise ConfigurationError(
			"VOLCENGINE_IMAGE_API_KEY is not set. Set it in .env to use image generation."
		)
	parts = key.split(".")
	if len(parts) == 3:
		return parts[0], parts[1], parts[2]
	if len(parts) == 2:
		return parts[0], parts[1], ""
	raise ConfigurationError(
		"VOLCENGINE_IMAGE_API_KEY format is invalid. "
		"Expected AK.SK or AK.SK.Token (STS format)."
	)


def _extract_error(resp: requests.Response) -> str:
	try:
		body = resp.json()
		if "error" in body:
			return body["error"].get("message", str(body["error"]))
		if "message" in body:
			return body["message"]
		return resp.text[:500]
	except (json.JSONDecodeError, KeyError):
		return resp.text[:500]


# ═══════════════════════════════════════════════════════════════
# Volcengine Signature V4
# ═══════════════════════════════════════════════════════════════


def _sign(key: bytes, msg: str) -> bytes:
	return _hmac.new(key, msg.encode("utf-8"), "sha256").digest()


def _sha256_hex(data: str | bytes) -> str:
	return hashlib.sha256(
		data.encode("utf-8") if isinstance(data, str) else data
	).hexdigest()


def _volcengine_sign_headers(
	method: str,
	host: str,
	path: str,
	query: dict[str, str],
	body: str,
	access_key: str,
	secret_key: str,
	session_token: str,
) -> dict[str, str]:
	"""Build Volcengine Signature V4 request headers."""
	now = datetime.now(timezone.utc)
	timestamp = now.strftime("%Y%m%dT%H%M%SZ")
	datestamp = now.strftime("%Y%m%d")

	# Canonical query string (sorted, encoded)
	canonical_query = "&".join(
		f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
		for k, v in sorted(query.items())
	)

	# Payload hash
	payload_hash = _sha256_hex(body)

	# Headers to sign (must include host and x-date at minimum)
	headers_to_sign = {
		"host": host,
		"x-date": timestamp,
		"x-content-sha256": payload_hash,
	}
	if session_token:
		headers_to_sign["x-security-token"] = session_token

	# Canonical headers (sorted by key, lowercased, trimmed)
	canonical_headers = "".join(
		f"{k}:{v}\n" for k, v in sorted(headers_to_sign.items())
	)
	signed_headers = ";".join(sorted(headers_to_sign.keys()))

	# Canonical request
	canonical_request = (
		f"{method}\n"
		f"{path}\n"
		f"{canonical_query}\n"
		f"{canonical_headers}\n"
		f"{signed_headers}\n"
		f"{payload_hash}"
	)

	# Credential scope
	credential_scope = f"{datestamp}/{IMAGE_REGION}/{IMAGE_SERVICE}/request"

	# String to sign
	string_to_sign = (
		f"HMAC-SHA256\n"
		f"{timestamp}\n"
		f"{credential_scope}\n"
		f"{_sha256_hex(canonical_request)}"
	)

	# Signing key — Volcengine uses the Secret Access Key directly as kSecret
	# (NOT prefixed like AWS's "AWS4"+sk). See:
	# https://www.volcengine.com/docs/6369/67270
	k_date = _sign(secret_key.encode("utf-8"), datestamp)
	k_region = _sign(k_date, IMAGE_REGION)
	k_service = _sign(k_region, IMAGE_SERVICE)
	k_signing = _sign(k_service, "request")

	# Signature
	signature = _hmac.new(
		k_signing, string_to_sign.encode("utf-8"), "sha256"
	).hexdigest()

	# Authorization header
	authorization = (
		f"HMAC-SHA256 "
		f"Credential={access_key}/{credential_scope}, "
		f"SignedHeaders={signed_headers}, "
		f"Signature={signature}"
	)

	result = {
		"Host": host,
		"X-Date": timestamp,
		"X-Content-Sha256": payload_hash,
		"Authorization": authorization,
		"Content-Type": "application/json",
	}
	if session_token:
		result["X-Security-Token"] = session_token

	return result


# ═══════════════════════════════════════════════════════════════
# Image Generation — Volcengine Jimeng (即梦文生图3.1)
# ═══════════════════════════════════════════════════════════════
#
# The Jimeng API is asynchronous: submit task → poll for result.
# Auth uses Volcengine Signature V4 (HMAC-SHA256) with STS credentials.


def generate_image(
	prompt: str,
	*,
	model: str = DEFAULT_IMAGE_MODEL,
	n: int = 1,
	width: int = 1328,
	height: int = 1328,
	seed: int = -1,
	use_pre_llm: bool = True,
	max_retries: int = IMAGE_MAX_RETRIES,
	retry_base_delay: float = IMAGE_RETRY_BASE_DELAY,
) -> dict[str, Any]:
	"""Generate images via Volcengine Jimeng (即梦文生图3.1).

	Args:
		prompt: Image generation prompt (Chinese supported, max 800 chars).
		model: Semantic label (always jimeng_t2i_v31).
		n: Number of images (currently API returns 1 per task).
		width: Image width in pixels. Supported aspect ratios 1:3 to 3:1.
		height: Image height in pixels. Width×height within [512×512, 2048×2048].
		seed: Random seed (-1 for random).
		use_pre_llm: Whether to use LLM to expand the prompt.
		max_retries: Max retries for transient errors.
		retry_base_delay: Base delay for exponential backoff.

	Returns:
		Dict with keys: "urls" (list[str]), "model", "usage".

	Raises:
		ConfigurationError: If VOLCENGINE_IMAGE_API_KEY is not set.
		APIError: If the API returns an error or all retries exhausted.
	"""
	if len(prompt) > 800:
		prompt = prompt[:800]

	access_key, secret_key, session_token = _parse_image_api_key()
	host = VISUAL_API_HOST
	path = VISUAL_API_PATH

	# ── Step 1: Submit the task ──
	submit_query = {
		"Action": "CVSync2AsyncSubmitTask",
		"Version": IMAGE_API_VERSION,
	}
	submit_body = json.dumps({
		"req_key": "jimeng_t2i_v31",
		"prompt": prompt,
		"seed": seed,
		"width": width,
		"height": height,
		"use_pre_llm": use_pre_llm,
	}, ensure_ascii=False)

	logger.info("Jimeng image generation submit: prompt=%.100s..., size=%dx%d",
	           prompt, width, height)

	_task_id: str | None = None
	last_error: Exception | None = None
	_retry_statuses = {429, 502, 503, 529}

	for attempt in range(max_retries + 1):
		try:
			headers = _volcengine_sign_headers(
				"POST", host, path, submit_query, submit_body,
				access_key, secret_key, session_token,
			)
			url = f"https://{host}{path}?{urllib.parse.urlencode(submit_query)}"
			resp = _get_session().post(url, data=submit_body.encode("utf-8"),
			                           headers=headers, timeout=API_TIMEOUT_SECONDS)
		except requests.Timeout:
			last_error = APIError(f"Jimeng submit timed out after {API_TIMEOUT_SECONDS}s")
			if attempt < max_retries:
				delay = retry_base_delay * (2 ** attempt)
				logger.warning("Jimeng submit timeout, retrying in %.1fs (attempt %d/%d)",
				               delay, attempt + 1, max_retries)
				time.sleep(delay)
				continue
			raise last_error
		except requests.ConnectionError as exc:
			last_error = APIError(f"Jimeng submit connection failed: {exc}")
			last_error.__cause__ = exc
			if attempt < max_retries:
				delay = retry_base_delay * (2 ** attempt)
				logger.warning("Jimeng submit connection error, retrying in %.1fs (attempt %d/%d)",
				               delay, attempt + 1, max_retries)
				time.sleep(delay)
				continue
			raise last_error
		except requests.RequestException as exc:
			last_error = APIError(f"Jimeng submit request failed: {exc}")
			last_error.__cause__ = exc
			if attempt < max_retries:
				delay = retry_base_delay * (2 ** attempt)
				logger.warning("Jimeng submit request error, retrying in %.1fs (attempt %d/%d)",
				               delay, attempt + 1, max_retries)
				time.sleep(delay)
				continue
			raise last_error

		if resp.status_code in _retry_statuses:
			last_error = APIError(f"Jimeng submit returned {resp.status_code}: {_extract_error(resp)}")
			if attempt < max_retries:
				delay = retry_base_delay * (2 ** attempt)
				logger.warning("Jimeng submit transient error %s, retrying in %.1fs (attempt %d/%d)",
				               resp.status_code, delay, attempt + 1, max_retries)
				time.sleep(delay)
				continue
			raise last_error

		if resp.status_code != 200:
			raise APIError(
				f"Jimeng submit returned {resp.status_code}: {_extract_error(resp)}"
			)

		data = resp.json()
		code = data.get("code", -1)
		if code != 10000:
			msg = data.get("message", "unknown error")
			logger.error("Jimeng submit error: code=%s, message=%s", code, msg)

			# Retryable errors: 50429 (QPS limit), 50430 (concurrent limit),
			# 50511 (post-img risk — retry may pass), 50519 (copyright retry)
			_retryable_codes = {50429, 50430, 50511, 50519}
			if code in _retryable_codes and attempt < max_retries:
				delay = retry_base_delay * (2 ** attempt)
				logger.warning("Jimeng submit retryable error %s, retrying in %.1fs (attempt %d/%d)",
				               code, delay, attempt + 1, max_retries)
				time.sleep(delay)
				continue
			raise APIError(f"Jimeng submit error {code}: {msg}")

		_task_id = data.get("data", {}).get("task_id", "")
		if _task_id:
			break

	if not _task_id:
		raise last_error or APIError("Jimeng submit did not return a task_id")

	logger.info("Jimeng task submitted: task_id=%s", _task_id)

	# ── Step 2: Poll for result ──
	poll_query = {
		"Action": "CVSync2AsyncGetResult",
		"Version": IMAGE_API_VERSION,
	}
	req_json_str = json.dumps({"return_url": True}, ensure_ascii=False)

	# Re-derive credentials for the poll (signature is time-sensitive)
	for poll_attempt in range(IMAGE_POLL_MAX_ATTEMPTS):
		time.sleep(IMAGE_POLL_INTERVAL)

		poll_body = json.dumps({
			"req_key": "jimeng_t2i_v31",
			"task_id": _task_id,
			"req_json": req_json_str,
		}, ensure_ascii=False)

		try:
			# Re-sign for each poll (timestamp changes)
			headers = _volcengine_sign_headers(
				"POST", host, path, poll_query, poll_body,
				access_key, secret_key, session_token,
			)
			url = f"https://{host}{path}?{urllib.parse.urlencode(poll_query)}"
			resp = _get_session().post(url, data=poll_body.encode("utf-8"),
			                           headers=headers, timeout=API_TIMEOUT_SECONDS)
		except requests.Timeout:
			logger.warning("Jimeng poll timeout (attempt %d/%d)",
			               poll_attempt + 1, IMAGE_POLL_MAX_ATTEMPTS)
			continue
		except requests.ConnectionError:
			logger.warning("Jimeng poll connection error (attempt %d/%d)",
			               poll_attempt + 1, IMAGE_POLL_MAX_ATTEMPTS)
			continue
		except requests.RequestException as exc:
			logger.warning("Jimeng poll request error (attempt %d/%d): %s",
			               poll_attempt + 1, IMAGE_POLL_MAX_ATTEMPTS, exc)
			continue

		if resp.status_code != 200:
			logger.warning("Jimeng poll returned %s (attempt %d/%d)",
			               resp.status_code, poll_attempt + 1, IMAGE_POLL_MAX_ATTEMPTS)
			continue

		data = resp.json()
		code = data.get("code", -1)
		if code != 10000:
			msg = data.get("message", "unknown error")
			logger.error("Jimeng poll error: code=%s, message=%s", code, msg)
			raise APIError(f"Jimeng poll error {code}: {msg}")

		status = data.get("data", {}).get("status", "")
		if status == "done":
			image_urls = data.get("data", {}).get("image_urls", []) or []
			logger.info("Jimeng image generation complete: task_id=%s, urls=%d",
			            _task_id, len(image_urls))
			return {
				"urls": image_urls,
				"model": model,
				"usage": {"task_id": _task_id},
			}

		if status in ("in_queue", "generating"):
			logger.debug("Jimeng task %s status=%s (poll %d/%d)",
			             _task_id, status, poll_attempt + 1, IMAGE_POLL_MAX_ATTEMPTS)
			continue

		if status == "not_found":
			raise APIError(f"Jimeng task {_task_id} not found (may have expired)")
		if status == "expired":
			raise APIError(f"Jimeng task {_task_id} expired, please retry")

		logger.warning("Jimeng unknown task status: %s", status)

	raise APIError(
		f"Jimeng task {_task_id} did not complete within {IMAGE_POLL_MAX_ATTEMPTS * IMAGE_POLL_INTERVAL:.0f}s"
	)


# ═══════════════════════════════════════════════════════════════
# Text-to-Speech (TTS)
# ═══════════════════════════════════════════════════════════════
#
# Provider: Volcengine (火山引擎) 豆包语音合成模型2.0
# Endpoint: POST https://openspeech.bytedance.com/api/v3/tts/unidirectional
# Auth:    X-Api-Key + X-Api-Resource-Id (new console, no appid needed)
# Response: HTTP chunked JSON lines, each with base64 audio in "data" field


def synthesize_speech(
	text: str,
	*,
	model: str = DEFAULT_TTS_MODEL,
	voice: str = "",
	speed: float = 1.0,
	vol: float = 1.0,
	return_audio_bytes: bool = True,
) -> dict[str, Any]:
	"""Call Volcengine TTS V3 API to synthesize speech from text.

	Args:
		text: Text to synthesize.
		model: Semantic label for logging (not sent to API).
		voice: Speaker ID (empty = default from settings).
		speed: Speech speed (0.2-3.0).
		vol: Volume (0.1-3.0).
		return_audio_bytes: If True, decode base64 audio and return bytes.
			If False, return base64 string directly.

	Returns:
		Dict with keys: "audio_bytes" (bytes | None), "audio_url" (str),
		"model", "format", "voice".

	Raises:
		ConfigurationError: If volcengine credentials are not set.
		APIError: If the API returns an error.
	"""
	api_key = _get_volcengine_api_key()
	speaker = voice or getattr(settings, "VOLCENGINE_TTS_SPEAKER", "zh_female_shuangkuaisisi_moon_bigtts")

	# -- Redis cache check --
	if return_audio_bytes:
		cached = _tts_cache_get(text, speaker)
		if cached is not None:
			logger.info("TTS cache hit for text_len=%d, speaker=%s", len(text), speaker)
			return {
				"audio_bytes": cached,
				"audio_url": "",
				"model": model,
				"voice": speaker,
				"format": "mp3",
			}

	url = f"{TTS_HOST}{TTS_ENDPOINT}"

	body = {
		"user": {
			"uid": "dbt_user",
		},
		"req_params": {
			"text": text,
			"speaker": speaker,
			"additions": json.dumps({"speed_ratio": speed, "volume_ratio": vol}),
			"audio_params": {
				"format": "mp3",
				"sample_rate": 24000,
			},
		},
	}

	headers = {
		"X-Api-Key": api_key,
		"X-Api-Resource-Id": TTS_RESOURCE_ID,
		"Content-Type": "application/json",
	}

	logger.info(
		"Volcengine TTS V3: speaker=%s, text_len=%d",
		speaker,
		len(text),
	)

	try:
		resp = _get_session().post(url, json=body, headers=headers, timeout=API_TIMEOUT_SECONDS, stream=True)
	except requests.Timeout:
		raise APIError(f"Volcengine TTS V3 timed out after {API_TIMEOUT_SECONDS}s")
	except requests.ConnectionError as exc:
		raise APIError(f"Volcengine TTS V3 connection failed: {exc}") from exc
	except requests.RequestException as exc:
		raise APIError(f"Volcengine TTS V3 request failed: {exc}") from exc

	if resp.status_code != 200:
		error_detail = _extract_error(resp)
		logger.error("Volcengine TTS V3 returned %s: %s", resp.status_code, error_detail)
		raise APIError(f"Volcengine TTS API returned {resp.status_code}: {error_detail}")

	# V3 response is streamed JSON lines: each line is a JSON object
	# code 0 with non-null data = audio chunk
	# code 20000000 with "OK" = final success marker
	# other non-zero codes (e.g. 55000000) = errors
	audio_chunks: list[bytes] = []
	success = False
	for line in resp.iter_lines(decode_unicode=True):
		if not line:
			continue
		try:
			chunk = json.loads(line)
		except json.JSONDecodeError:
			logger.warning("Volcengine TTS V3: non-JSON line in stream")
			continue

		code = chunk.get("code", -1)
		if code == 0:
			# Audio chunk or sentence boundary
			b64_data = chunk.get("data", "")
			if b64_data:
				try:
					import base64
					audio_chunks.append(base64.b64decode(b64_data))
				except Exception:
					logger.warning("Failed to decode base64 audio chunk")
		elif code == 20000000:
			success = True
		else:
			msg = chunk.get("message", "unknown error")
			logger.error("Volcengine TTS V3 error: code=%s, message=%s", code, msg)
			raise APIError(f"Volcengine TTS error {code}: {msg}")

	if not success:
		raise APIError("Volcengine TTS V3 did not complete successfully")

	if not audio_chunks:
		raise APIError("Volcengine TTS V3 returned no audio data")

	audio_bytes = b"".join(audio_chunks)

	# -- Redis cache store --
	if return_audio_bytes:
		_tts_cache_set(text, speaker, audio_bytes)

	audio_url = ""
	if not return_audio_bytes:
		import base64
		b64_all = base64.b64encode(audio_bytes).decode()
		audio_url = f"data:audio/mpeg;base64,{b64_all}"

	return {
		"audio_bytes": audio_bytes,
		"audio_url": audio_url,
		"model": model,
		"voice": speaker,
		"format": "mp3",
	}


# ═══════════════════════════════════════════════════════════════
# Text-to-Speech (TTS) -- Streaming
# ═══════════════════════════════════════════════════════════════


def stream_synthesize_speech(
	text: str,
	*,
	voice: str = "",
	speed: float = 1.0,
	vol: float = 1.0,
) -> "Generator[bytes, None, None]":
	"""Stream audio chunks from Volcengine TTS as they arrive.

	Generator yields raw MP3 audio bytes. Each yield is one decoded audio chunk
	from the Volcengine streaming response. Chunks can be fed directly to a
	StreamingHttpResponse for progressive browser playback via MediaSource.

	Redis cache is checked first; on cache hit the cached bytes are yielded in
	chunks. On cache miss, chunks are yielded as they arrive and the full audio
	is cached after streaming completes.

	Args:
	    text: Text to synthesize.
	    voice: Speaker ID (empty = default from settings).
	    speed: Speech speed (0.2-3.0).
	    vol: Volume (0.1-3.0).

	Yields:
	    Raw MP3 audio bytes.

	Raises:
	    ConfigurationError: If volcengine credentials are not set.
	    APIError: If the API returns an error (raised before first yield).
	"""
	import base64

	api_key = _get_volcengine_api_key()
	speaker = voice or getattr(settings, "VOLCENGINE_TTS_SPEAKER", "zh_female_shuangkuaisisi_moon_bigtts")

	# -- Redis cache check --
	cached = _tts_cache_get(text, speaker)
	if cached is not None:
		logger.info("TTS stream cache hit: text_len=%d, speaker=%s", len(text), speaker)
		chunk_size = 16384
		for i in range(0, len(cached), chunk_size):
			yield cached[i:i + chunk_size]
		return

	url = f"{TTS_HOST}{TTS_ENDPOINT}"

	body = {
		"user": {
			"uid": "dbt_user",
		},
		"req_params": {
			"text": text,
			"speaker": speaker,
			"additions": json.dumps({"speed_ratio": speed, "volume_ratio": vol}),
			"audio_params": {
				"format": "mp3",
				"sample_rate": 24000,
			},
		},
	}

	headers = {
		"X-Api-Key": api_key,
		"X-Api-Resource-Id": TTS_RESOURCE_ID,
		"Content-Type": "application/json",
	}

	logger.info(
		"Volcengine TTS V3 stream: speaker=%s, text_len=%d",
		speaker,
		len(text),
	)

	try:
		resp = _get_session().post(url, json=body, headers=headers, timeout=API_TIMEOUT_SECONDS, stream=True)
	except requests.Timeout:
		raise APIError(f"Volcengine TTS V3 timed out after {API_TIMEOUT_SECONDS}s")
	except requests.ConnectionError as exc:
		raise APIError(f"Volcengine TTS V3 connection failed: {exc}") from exc
	except requests.RequestException as exc:
		raise APIError(f"Volcengine TTS V3 request failed: {exc}") from exc

	if resp.status_code != 200:
		error_detail = _extract_error(resp)
		logger.error("Volcengine TTS V3 returned %s: %s", resp.status_code, error_detail)
		raise APIError(f"Volcengine TTS API returned {resp.status_code}: {error_detail}")

	# Stream audio chunks: yield each decoded chunk immediately,
	# while accumulating for Redis cache
	all_audio: list[bytes] = []
	success = False
	for line in resp.iter_lines(decode_unicode=True):
		if not line:
			continue
		try:
			chunk = json.loads(line)
		except json.JSONDecodeError:
			logger.warning("Volcengine TTS V3 stream: non-JSON line")
			continue

		code = chunk.get("code", -1)
		if code == 0:
			b64_data = chunk.get("data", "")
			if b64_data:
				try:
					audio_chunk = base64.b64decode(b64_data)
					all_audio.append(audio_chunk)
					yield audio_chunk
				except Exception:
					logger.warning("Failed to decode base64 audio chunk in stream")
		elif code == 20000000:
			success = True
		else:
			msg = chunk.get("message", "unknown error")
			logger.error("Volcengine TTS V3 stream error: code=%s, message=%s", code, msg)
			raise APIError(f"Volcengine TTS error {code}: {msg}")

	if not success:
		raise APIError("Volcengine TTS V3 stream did not complete successfully")

	if not all_audio:
		raise APIError("Volcengine TTS V3 stream returned no audio data")

	# Cache the full audio for future requests
	full_audio = b"".join(all_audio)
	_tts_cache_set(text, speaker, full_audio)

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
	"""Return volcengine API key (shared by ASR and TTS) or raise ConfigurationError."""
	key = getattr(settings, "VOLCENGINE_API_KEY", "")
	if not key:
		raise ConfigurationError(
			"Volcengine services not configured. Set VOLCENGINE_API_KEY in .env"
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
		resp = _get_session().post(
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
			poll_resp = _get_session().get(
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
