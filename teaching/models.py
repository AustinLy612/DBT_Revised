from dbt_platform.utils import gen_uuid

from django.db import models


class TeachingSession(models.Model):
    class Status(models.TextChoices):
        ONGOING = "ongoing", "进行中"
        COMPLETED = "completed", "已完成"
        STOPPED_BY_RISK = "stopped_by_risk", "风险中止"
        USER_TERMINATED = "user_terminated", "用户终止"

    class Phase(models.TextChoices):
        PRE_MOOD_RECORDING = "pre_mood_recording", "教学前心情记录"
        PERSONAL_INQUIRY = "personal_inquiry", "个人情况了解"
        INFO_COLLECTION = "info_collection", "信息收集"
        SKILL_SELECTION = "skill_selection", "技能选择"
        RAG_RETRIEVAL_FOR_TEACHING = "rag_retrieval_for_teaching", "RAG教学检索"
        TEACHING = "teaching", "教学中"

    session_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="teaching_sessions"
    )
    selected_module = models.CharField(max_length=64, blank=True, default="")
    selected_skill = models.CharField(max_length=128, blank=True, default="")
    selection_reason = models.TextField(blank=True, default="")
    teaching_plan = models.JSONField(default=dict)
    rag_context_ids = models.JSONField(default=list)
    teaching_summary = models.TextField(blank=True, default="")
    phase = models.CharField(
        max_length=30, choices=Phase.choices, default=Phase.PRE_MOOD_RECORDING
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ONGOING
    )
    pre_mood_id = models.CharField(max_length=36, blank=True, default="")
    post_mood_id = models.CharField(max_length=36, blank=True, default="")
    personal_context = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "teaching_sessions"


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "用户"
        ASSISTANT = "assistant", "AI助手"
        SYSTEM = "system", "系统"
        TOOL = "tool", "工具"

    message_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    session = models.ForeignKey(
        TeachingSession, on_delete=models.CASCADE, related_name="messages"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="chat_messages"
    )
    role = models.CharField(max_length=12, choices=Role.choices)
    content = models.TextField()
    modality = models.CharField(max_length=20, default="text")
    image_prompt = models.CharField(max_length=500, default="", blank=True)
    image_url = models.URLField(max_length=1000, default="", blank=True)
    teaching_step = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_messages"
