"""Media app views — image generation, TTS, and ASR endpoints.

All views require authentication. TTS/image/ASR endpoints check
for profile completion via @profile_required where appropriate.
"""

from __future__ import annotations

import logging
from http import HTTPStatus

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from . import services
from .models import AudioSynthesisLog, AudioTranscriptionLog, ImageGenerationLog
from questionnaire.decorators import profile_required

logger = logging.getLogger("dbt_platform.media_app")


# ═══════════════════════════════════════════════════════════════
# Image Generation
# ═══════════════════════════════════════════════════════════════


@profile_required
@csrf_exempt
def generate_image_view(request: HttpRequest) -> HttpResponse:
    """HTMX endpoint: generate an image from a text prompt.

    POST params:
        prompt: Image description text.
        model: Optional model ID (default: doubao-seedream-5-0-lite-260128).
        source: "teaching_scene" | "test_illustration" | "manual"
        session_id: Optional teaching session ID.
        test_question_id: Optional test question ID.

    Returns HTML fragment with the generated image or error message.
    """
    if request.method != "POST":
        return HttpResponse(status=HTTPStatus.METHOD_NOT_ALLOWED)

    prompt = request.POST.get("prompt", "").strip()
    if not prompt:
        return _htmx_error("请输入图片描述。")

    model = request.POST.get("model", services.DEFAULT_IMAGE_MODEL)
    source = request.POST.get("source", "manual")
    session_id = request.POST.get("session_id", "").strip()
    test_question_id = request.POST.get("test_question_id", "").strip()

    from media_app.concurrency import run_with_image_slot, try_acquire_image_slot

    resource_id = test_question_id or session_id or f"manual:{request.user.id}"
    if not try_acquire_image_slot("interactive"):
        return _htmx_error("配图服务繁忙，请稍后再试。")

    try:
        result = run_with_image_slot(
            resource_id,
            lambda: services.generate_image(prompt, model=model),
            kind="interactive",
        )
    except services.ConfigurationError as exc:
        logger.error("Image generation config error: %s", exc)
        return _htmx_error("图像生成服务未配置，请联系管理员。")
    except services.APIError as exc:
        logger.error("Image generation API error: %s", exc)
        log = ImageGenerationLog.objects.create(
            user=request.user,
            prompt=prompt,
            model=model,
            status=ImageGenerationLog.Status.FAILED,
            error_message=str(exc)[:500],
            source=source,
        )
        if session_id:
            _set_session(log, session_id)
        if test_question_id:
            _set_test_question(log, test_question_id)
        return _htmx_error("图像生成失败，请稍后再试。")

    image_url = result["urls"][0] if result["urls"] else ""
    if not image_url:
        log = ImageGenerationLog.objects.create(
            user=request.user,
            prompt=prompt,
            model=model,
            status=ImageGenerationLog.Status.FAILED,
            error_message="API returned no image URL",
            source=source,
        )
        return _htmx_error("图像生成失败：未返回图片。")

    log = ImageGenerationLog.objects.create(
        user=request.user,
        prompt=prompt,
        model=model,
        temporary_image_url=image_url,
        status=ImageGenerationLog.Status.SUCCESS,
        source=source,
    )
    if session_id:
        _set_session(log, session_id)
    if test_question_id:
        _set_test_question(log, test_question_id)

    # If this is a test question image, update the question's image fields
    if test_question_id and source == "test_illustration":
        from testing.models import TestQuestion
        try:
            question = TestQuestion.objects.get(question_id=test_question_id)
            question.image_prompt = prompt
            question.temporary_image_url = image_url
            question.image_model = model
            from django.utils import timezone
            question.image_generated_at = timezone.now()
            question.save(update_fields=["image_prompt", "temporary_image_url", "image_model", "image_generated_at"])
        except TestQuestion.DoesNotExist:
            pass

    return HttpResponse(
        f'<div class="generated-image flex flex-col items-center">'
        f'<img src="{image_url}" alt="生成的图片" '
        f'class="rounded-lg shadow max-w-full" loading="lazy">'
        f'<p class="text-xs text-gray-400 mt-1">模型: {model}</p></div>'
    )


# ═══════════════════════════════════════════════════════════════
# Text-to-Speech (TTS)
# ═══════════════════════════════════════════════════════════════


@login_required
@csrf_exempt
def synthesize_speech_view(request: HttpRequest) -> HttpResponse:
    """Synthesize speech from text via Volcengine TTS and return audio bytes.

    POST params:
        text: Text to synthesize.
        model: Optional model label (for logging).
        voice: Optional voice type ID.
        message_id: Optional ChatMessage ID for logging.

    Returns audio/mpeg binary on success, or error JSON.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    logger.info("TTS request received: user=%s, text_len=%d, message_id=%s",
                request.user.username, len(request.POST.get("text", "")),
                request.POST.get("message_id", "")[:36])

    text = request.POST.get("text", "").strip()
    if not text:
        return JsonResponse({"error": "文本不能为空"}, status=400)

    if len(text) > 3000:
        text = text[:3000]
    # Trim to ~1000 bytes for volcengine TTS limit (UTF-8 Chinese ≈ 3 bytes/char)
    while len(text.encode("utf-8")) > 1000:
        text = text[:-1]

    model = request.POST.get("model", services.DEFAULT_TTS_MODEL)
    voice = request.POST.get("voice", "")
    message_id = request.POST.get("message_id", "").strip()

    try:
        result = services.synthesize_speech(
            text, model=model, voice=voice, return_audio_bytes=True
        )
    except services.ConfigurationError:
        return JsonResponse({"error": "语音服务未配置"}, status=503)
    except services.APIError as exc:
        logger.error("TTS API error: %s", exc)
        AudioSynthesisLog.objects.create(
            user=request.user,
            text=text,
            model=model,
            voice=voice,
            status=AudioSynthesisLog.Status.FAILED,
            error_message=str(exc)[:500],
        )
        return JsonResponse({"error": "语音合成失败"}, status=502)

    audio_bytes = result.get("audio_bytes")
    audio_url = result.get("audio_url", "")

    # Try to log the synthesis
    try:
        log = AudioSynthesisLog.objects.create(
            user=request.user,
            text=text[:500],
            model=model,
            voice=voice,
            temporary_audio_url=audio_url or "",
            status=AudioSynthesisLog.Status.SUCCESS,
        )
        if message_id:
            from teaching.models import ChatMessage
            try:
                log.message = ChatMessage.objects.get(message_id=message_id)
                log.save(update_fields=["message"])
            except ChatMessage.DoesNotExist:
                pass
    except Exception:
        logger.exception("Failed to save AudioSynthesisLog")

    if audio_bytes:
        resp = HttpResponse(audio_bytes, content_type="audio/mpeg")
        resp["Content-Disposition"] = 'inline; filename="speech.mp3"'
        resp["Cache-Control"] = "no-cache"
        return resp

    if audio_url:
        return JsonResponse({"audio_url": audio_url, "format": result.get("format", "mp3")})

    return JsonResponse({"error": "未能获取音频数据"}, status=502)


@login_required
@csrf_exempt
def stream_speech_view(request: HttpRequest) -> HttpResponse:
    """Stream synthesized speech via chunked transfer encoding.

    POST params:
        text: Text to synthesize.
        voice: Optional voice type ID.
        message_id: Optional ChatMessage ID for logging.

    Returns StreamingHttpResponse (audio/mpeg) with chunked transfer,
    or JSON error on configuration / pre-flight failure.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    text = request.POST.get("text", "").strip()
    if not text:
        return JsonResponse({"error": "文本不能为空"}, status=400)

    if len(text) > 3000:
        text = text[:3000]
    while len(text.encode("utf-8")) > 1000:
        text = text[:-1]

    voice = request.POST.get("voice", "")
    message_id = request.POST.get("message_id", "").strip()

    try:
        generator = services.stream_synthesize_speech(text, voice=voice)
        # Prime the generator: if the API call setup fails (ConfigurationError,
        # APIError), we catch it here before StreamingHttpResponse begins.
        # The first yield / exception happens on the first next() call.
        first_chunk = next(generator)

        def _stream_with_first():
            """Yield the pre-fetched first chunk, then the rest."""
            yield first_chunk
            yield from generator

        response = StreamingHttpResponse(
            _stream_with_first(),
            content_type="audio/mpeg",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    except services.ConfigurationError:
        return JsonResponse({"error": "语音服务未配置"}, status=503)
    except services.APIError as exc:
        logger.error("TTS stream API error: %s", exc)
        AudioSynthesisLog.objects.create(
            user=request.user,
            text=text,
            model=services.DEFAULT_TTS_MODEL,
            voice=voice,
            status=AudioSynthesisLog.Status.FAILED,
            error_message=str(exc)[:500],
        )
        return JsonResponse({"error": "语音合成失败"}, status=502)
    except Exception:
        logger.exception("TTS stream unexpected error")
        return JsonResponse({"error": "语音合成失败"}, status=502)


# ═══════════════════════════════════════════════════════════════
# Automatic Speech Recognition (ASR)
# ═══════════════════════════════════════════════════════════════


@login_required
@csrf_exempt
def transcribe_audio_view(request: HttpRequest) -> HttpResponse:
    """Receive audio file and return transcribed text.

    POST params:
        audio: Raw audio file upload (WAV, MP3, webm, etc.).
        session_id: Optional teaching session ID for context.

    Returns JSON: {"text": "...", "success": true/false, "error": "..."}
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    audio_file = request.FILES.get("audio")
    if not audio_file:
        return JsonResponse({"success": False, "error": "未收到音频数据"})

    audio_bytes = audio_file.read()
    if not audio_bytes:
        return JsonResponse({"success": False, "error": "音频数据为空"})

    # Determine format from content type or extension
    content_type = audio_file.content_type or ""
    audio_format = "webm"
    if "wav" in content_type or audio_file.name.endswith(".wav"):
        audio_format = "wav"
    elif "mp3" in content_type or audio_file.name.endswith(".mp3"):
        audio_format = "mp3"
    elif "mpeg" in content_type:
        audio_format = "mp3"
    elif "ogg" in content_type or audio_file.name.endswith(".ogg"):
        audio_format = "ogg"
    elif "mp4" in content_type or audio_file.name.endswith(".m4a"):
        audio_format = "m4a"

    audio_duration_ms = _estimate_audio_duration(len(audio_bytes), audio_format)

    session_id = request.POST.get("session_id", "").strip()

    try:
        result = services.transcribe_audio(audio_bytes, audio_format=audio_format)
    except services.ConfigurationError:
        return JsonResponse({
            "success": False,
            "error": "语音识别未配置。请在.env中设置 VOLCENGINE_API_KEY。",
        })
    except services.APIError as exc:
        logger.error("ASR API error: %s", exc)
        AudioTranscriptionLog.objects.create(
            user=request.user,
            status=AudioTranscriptionLog.Status.FAILED,
            error_message=str(exc)[:500],
            audio_duration_ms=audio_duration_ms,
            model="",
        )
        return JsonResponse({"success": False, "error": "语音识别失败，请稍后再试。"})

    transcribed_text = result["transcribed_text"]

    # Log the transcription (audio data is NOT saved)
    try:
        log = AudioTranscriptionLog.objects.create(
            user=request.user,
            transcribed_text=transcribed_text,
            model=result.get("model", ""),
            audio_duration_ms=audio_duration_ms,
            status=AudioTranscriptionLog.Status.SUCCESS,
        )
        if session_id:
            from teaching.models import TeachingSession
            try:
                log.session = TeachingSession.objects.get(session_id=session_id)
                log.save(update_fields=["session"])
            except TeachingSession.DoesNotExist:
                pass
    except Exception:
        logger.exception("Failed to save AudioTranscriptionLog")

    return JsonResponse({"success": True, "text": transcribed_text})


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _htmx_error(message: str) -> HttpResponse:
    return HttpResponse(
        f'<div class="text-red-500 text-sm p-3 bg-red-50 rounded-lg">{message}</div>'
    )


def _set_session(log, session_id: str) -> None:
    try:
        from teaching.models import TeachingSession
        log.session = TeachingSession.objects.get(session_id=session_id)
        log.save(update_fields=["session"])
    except Exception:
        pass


def _set_test_question(log, question_id: str) -> None:
    try:
        from testing.models import TestQuestion
        log.test_question = TestQuestion.objects.get(question_id=question_id)
        log.save(update_fields=["test_question"])
    except Exception:
        pass


def _estimate_audio_duration(num_bytes: int, fmt: str) -> int:
    """Rough estimate of audio duration in milliseconds from byte count."""
    # Approximate bitrates for common formats
    rates = {
        "wav": 176400,   # 44.1kHz 16-bit stereo
        "mp3": 16000,    # 128kbps
        "webm": 8000,    # variable, rough estimate
        "ogg": 12000,
        "m4a": 16000,
    }
    bps = rates.get(fmt, 16000)
    return int(num_bytes / bps * 1000)
