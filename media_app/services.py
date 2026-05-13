"""Media services — image generation (MiniMax), TTS (Volcengine), and ASR (Volcengine).

Image files and raw audio are NOT persisted. Only metadata is stored.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger("dbt_platform.media_app")

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
IMAGE_GENERATION_ENDPOINT = "/v1/image_generation"
TTS_HOST = "https://openspeech.bytedance.com"
TTS_ENDPOINT = "/api/v3/tts/unidirectional"
TTS_RESOURCE_ID = "seed-tts-2.0"
ASR_ENDPOINT = "/v1/audio/transcription"

# ── Default models ──
DEFAULT_IMAGE_MODEL = "image-01"
DEFAULT_IMAGE_LIVE_MODEL = "image-01-live"
DEFAULT_TTS_MODEL = "volcengine-tts"  # semantic label for logging

API_TIMEOUT_SECONDS = 120
IMAGE_MAX_RETRIES = 3
IMAGE_RETRY_BASE_DELAY = 2.0  # seconds, multiplied by 2^attempt


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
	aspect_ratio: str = "1:1",
	max_retries: int = IMAGE_MAX_RETRIES,
	retry_base_delay: float = IMAGE_RETRY_BASE_DELAY,
) -> dict[str, Any]:
	"""Call MiniMax Image Generation API.

	Args:
		prompt: Image generation prompt (Chinese supported).
		model: Model ID (image-01 or image-01-live).
		n: Number of images to generate (1-9).
		aspect_ratio: Image aspect ratio (e.g. "1:1", "16:9", "4:3").
		max_retries: Max retry attempts for transient errors (429, 502, 503, 529).
		retry_base_delay: Base delay in seconds; multiplied by 2^attempt for backoff.

	Returns:
		Dict with keys: "urls" (list[str]), "model", "usage".

	Raises:
		ConfigurationError: If MINIMAX_API_KEY is not set.
		APIError: If the API returns a non-transient error or all retries exhausted.
	"""
	api_key = _get_api_key()
	base_url = _get_base_url()
	url = f"{base_url}{IMAGE_GENERATION_ENDPOINT}"

	body = {
		"model": model,
		"prompt": prompt,
		"n": n,
		"aspect_ratio": aspect_ratio,
			"prompt_optimizer": True,
	}

	headers = {
		"Authorization": f"Bearer {api_key}",
		"Content-Type": "application/json",
	}

	logger.info("MiniMax image generation: model=%s, prompt=%.100s...", model, prompt)

	last_error: Exception | None = None
	_retry_statuses = {429, 502, 503, 529}

	for attempt in range(max_retries + 1):
		try:
			resp = requests.post(url, json=body, headers=headers, timeout=API_TIMEOUT_SECONDS)
		except requests.Timeout:
			last_error = APIError(f"MiniMax image API timed out after {API_TIMEOUT_SECONDS}s")
			if attempt < max_retries:
				delay = retry_base_delay * (2 ** attempt)
				logger.warning("MiniMax image timeout, retrying in %.1fs (attempt %d/%d)",
				               delay, attempt + 1, max_retries)
				time.sleep(delay)
				continue
			raise last_error
		except requests.ConnectionError as exc:
			last_error = APIError(f"MiniMax image API connection failed: {exc}")
			last_error.__cause__ = exc
			if attempt < max_retries:
				delay = retry_base_delay * (2 ** attempt)
				logger.warning("MiniMax image connection error, retrying in %.1fs (attempt %d/%d)",
				               delay, attempt + 1, max_retries)
				time.sleep(delay)
				continue
			raise last_error
		except requests.RequestException as exc:
			last_error = APIError(f"MiniMax image API request failed: {exc}")
			last_error.__cause__ = exc
			if attempt < max_retries:
				delay = retry_base_delay * (2 ** attempt)
				logger.warning("MiniMax image request error, retrying in %.1fs (attempt %d/%d)",
				               delay, attempt + 1, max_retries)
				time.sleep(delay)
				continue
			raise last_error

		# Retry on transient HTTP errors (429 rate-limit, 502/503 server errors, 529 overload)
		if resp.status_code in _retry_statuses:
			error_detail = _extract_error(resp)
			last_error = APIError(f"MiniMax image API returned {resp.status_code}: {error_detail}")
			if attempt < max_retries:
				delay = retry_base_delay * (2 ** attempt)
				logger.warning(
					"MiniMax image transient error %s, retrying in %.1fs (attempt %d/%d)",
					resp.status_code, delay, attempt + 1, max_retries,
				)
				time.sleep(delay)
				continue
			raise last_error

		if resp.status_code != 200:
			raise APIError(
				f"MiniMax image API returned {resp.status_code}: {_extract_error(resp)}"
			)

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

	raise last_error  # type: ignore[misc]


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
		resp = requests.post(url, json=body, headers=headers, timeout=API_TIMEOUT_SECONDS, stream=True)
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
		resp = requests.post(url, json=body, headers=headers, timeout=API_TIMEOUT_SECONDS, stream=True)
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
