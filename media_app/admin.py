from django.contrib import admin

from .models import AudioSynthesisLog, AudioTranscriptionLog, ImageGenerationLog


@admin.register(ImageGenerationLog)
class ImageGenerationLogAdmin(admin.ModelAdmin):
    list_display = [
        "image_id_short",
        "user",
        "source",
        "model",
        "prompt_preview",
        "status",
        "created_at",
    ]
    list_filter = ["status", "source", "model", "created_at"]
    search_fields = ["prompt", "user__username"]
    readonly_fields = [
        "image_id", "user", "prompt", "model", "temporary_image_url",
        "status", "error_message", "session", "test_question",
        "source", "created_at",
    ]
    fieldsets = [
        ("基本信息", {"fields": ["image_id", "user", "source", "status", "created_at"]}),
        ("生成配置", {"fields": ["model", "prompt"]}),
        ("结果", {"fields": ["temporary_image_url", "error_message"]}),
        ("关联", {"fields": ["session", "test_question"]}),
    ]
    ordering = ["-created_at"]

    def image_id_short(self, obj):
        return obj.image_id[:12] + "..."
    image_id_short.short_description = "Image ID"

    def prompt_preview(self, obj):
        return obj.prompt[:60] + "..." if len(obj.prompt) > 60 else obj.prompt
    prompt_preview.short_description = "Prompt"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AudioSynthesisLog)
class AudioSynthesisLogAdmin(admin.ModelAdmin):
    list_display = [
        "synthesis_id_short",
        "user",
        "model",
        "voice",
        "text_preview",
        "status",
        "created_at",
    ]
    list_filter = ["status", "model", "created_at"]
    search_fields = ["text", "user__username"]
    readonly_fields = [
        "synthesis_id", "user", "text", "model", "voice",
        "temporary_audio_url", "status", "error_message",
        "message", "created_at",
    ]
    fieldsets = [
        ("基本信息", {"fields": ["synthesis_id", "user", "status", "created_at"]}),
        ("合成配置", {"fields": ["model", "voice", "text"]}),
        ("结果", {"fields": ["temporary_audio_url", "error_message"]}),
        ("关联", {"fields": ["message"]}),
    ]
    ordering = ["-created_at"]

    def synthesis_id_short(self, obj):
        return obj.synthesis_id[:12] + "..."
    synthesis_id_short.short_description = "Synthesis ID"

    def text_preview(self, obj):
        return obj.text[:60] + "..." if len(obj.text) > 60 else obj.text
    text_preview.short_description = "Text"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AudioTranscriptionLog)
class AudioTranscriptionLogAdmin(admin.ModelAdmin):
    list_display = [
        "transcription_id_short",
        "user",
        "model",
        "text_preview",
        "audio_duration_ms",
        "status",
        "created_at",
    ]
    list_filter = ["status", "model", "created_at"]
    search_fields = ["transcribed_text", "user__username"]
    readonly_fields = [
        "transcription_id", "user", "transcribed_text", "model",
        "audio_duration_ms", "status", "error_message",
        "session", "created_at",
    ]
    fieldsets = [
        ("基本信息", {"fields": ["transcription_id", "user", "status", "created_at"]}),
        ("识别结果", {"fields": ["transcribed_text", "model"]}),
        ("音频信息", {"fields": ["audio_duration_ms"]}),
        ("异常", {"fields": ["error_message"]}),
        ("关联", {"fields": ["session"]}),
    ]
    ordering = ["-created_at"]

    def transcription_id_short(self, obj):
        return obj.transcription_id[:12] + "..."
    transcription_id_short.short_description = "Transcription ID"

    def text_preview(self, obj):
        text = obj.transcribed_text
        return text[:60] + "..." if len(text) > 60 else text
    text_preview.short_description = "Transcribed Text"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False
