"""Media app models — metadata logs for image generation, TTS, and ASR.

Image files and audio data are NOT persisted. Only metadata (prompt, model,
temporary URL, timestamp) is stored, per PRD data constraints.
"""

from dbt_platform.utils import gen_uuid

from django.db import models


class ImageGenerationLog(models.Model):
    """Metadata log for image generation calls.

    Image files are NOT persisted — temporary_url expires.
    """

    class Status(models.TextChoices):
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    image_id = models.CharField(
        primary_key=True, max_length=36, default=gen_uuid, editable=False
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="image_generations"
    )
    prompt = models.TextField()
    model = models.CharField(max_length=64, default="jimeng_t2i_v31")
    temporary_image_url = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.SUCCESS
    )
    error_message = models.TextField(blank=True, default="")
    # Optional links to the context where the image was generated
    session = models.ForeignKey(
        "teaching.TeachingSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="image_generations",
    )
    test_question = models.ForeignKey(
        "testing.TestQuestion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="image_generations",
    )
    source = models.CharField(
        max_length=20,
        choices=(
            ("teaching_scene", "教学情景"),
            ("test_illustration", "测试题配图"),
            ("manual", "手动生成"),
        ),
        default="manual",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "image_generation_logs"
        ordering = ["-created_at"]


class AudioSynthesisLog(models.Model):
    """Metadata log for TTS (text-to-speech) calls.

    Audio files are NOT persisted.
    """

    class Status(models.TextChoices):
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    synthesis_id = models.CharField(
        primary_key=True, max_length=36, default=gen_uuid, editable=False
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="tts_syntheses"
    )
    text = models.TextField()
    model = models.CharField(max_length=64, default="volcengine-tts")
    voice = models.CharField(max_length=64, blank=True, default="")
    temporary_audio_url = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.SUCCESS
    )
    error_message = models.TextField(blank=True, default="")
    # Link to the message that was synthesized (optional)
    message = models.ForeignKey(
        "teaching.ChatMessage",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tts_syntheses",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audio_synthesis_logs"
        ordering = ["-created_at"]


class AudioTranscriptionLog(models.Model):
    """Metadata log for ASR (automatic speech recognition) calls.

    Raw audio is NOT persisted — only the transcribed text and metadata.
    """

    class Status(models.TextChoices):
        SUCCESS = "success", "成功"
        FAILED = "failed", "失败"

    transcription_id = models.CharField(
        primary_key=True, max_length=36, default=gen_uuid, editable=False
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="transcriptions"
    )
    transcribed_text = models.TextField(blank=True, default="")
    model = models.CharField(max_length=64, default="")
    audio_duration_ms = models.IntegerField(default=0)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.SUCCESS
    )
    error_message = models.TextField(blank=True, default="")
    session = models.ForeignKey(
        "teaching.TeachingSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transcriptions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audio_transcription_logs"
        ordering = ["-created_at"]
