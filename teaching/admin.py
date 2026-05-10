from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import ChatMessage, TeachingSession


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    fields = ["role", "content_preview", "modality", "created_at"]
    readonly_fields = ["role", "content_preview", "modality", "created_at"]
    extra = 0
    can_delete = False
    max_num = 50

    def content_preview(self, obj):
        return obj.content[:80] + "..." if len(obj.content) > 80 else obj.content

    content_preview.short_description = "内容预览"

    def has_add_permission(self, request, obj):
        return False


@admin.register(TeachingSession)
class TeachingSessionAdmin(admin.ModelAdmin):
    list_display = [
        "session_id_short",
        "user",
        "phase",
        "status",
        "selected_module",
        "selected_skill",
        "message_count",
        "started_at",
        "completed_at",
    ]
    list_filter = ["status", "phase", "selected_module", "started_at"]
    search_fields = ["user__username", "selected_module", "selected_skill", "teaching_summary"]
    readonly_fields = ["session_id", "started_at", "completed_at"]
    fieldsets = (
        (_("基本信息"), {"fields": ("session_id", "user", "phase", "status", "started_at", "completed_at")}),
        (_("教学内容"), {"fields": ("selected_module", "selected_skill", "selection_reason", "teaching_summary", "teaching_plan")}),
        (_("RAG上下文"), {"fields": ("rag_context_ids",)}),
        (_("情绪记录"), {"fields": ("pre_mood_id", "post_mood_id")}),
    )
    inlines = [ChatMessageInline]

    @admin.display(description="会话ID")
    def session_id_short(self, obj):
        return obj.session_id[:12] + "..."

    @admin.display(description="消息数")
    def message_count(self, obj):
        return obj.messages.count()


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ["message_id_short", "session_link", "user", "role", "content_preview", "created_at"]
    list_filter = ["role", "modality", "created_at"]
    search_fields = ["content", "user__username"]
    readonly_fields = ["message_id", "created_at"]
    fieldsets = (
        (_("基本信息"), {"fields": ("message_id", "session", "user", "role", "modality", "created_at")}),
        (_("内容"), {"fields": ("content",)}),
    )

    @admin.display(description="消息ID")
    def message_id_short(self, obj):
        return obj.message_id[:12] + "..."

    @admin.display(description="会话")
    def session_link(self, obj):
        return f"{obj.session.session_id[:12]}..."

    @admin.display(description="内容预览")
    def content_preview(self, obj):
        return obj.content[:60] + "..." if len(obj.content) > 60 else obj.content
