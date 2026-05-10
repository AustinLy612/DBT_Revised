from dbt_platform.utils import gen_uuid

from django.db import models


class RiskEvent(models.Model):
    class DetectionSource(models.TextChoices):
        KEYWORD = "keyword", "关键词"
        AI = "ai", "AI语义"
        BOTH = "both", "两者"

    risk_event_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="risk_events"
    )
    session = models.ForeignKey(
        "teaching.TeachingSession",
        on_delete=models.CASCADE,
        related_name="risk_events",
    )
    trigger_time = models.DateTimeField(auto_now_add=True)
    trigger_text = models.TextField()
    detection_source = models.CharField(
        max_length=10, choices=DetectionSource.choices, default=DetectionSource.KEYWORD
    )
    action_taken = models.TextField(blank=True, default="")
    session_stopped = models.BooleanField(default=True)
    follow_up_mode = models.CharField(max_length=32, default="onsite_manual_followup")
    exported_flag = models.BooleanField(default=False)

    class Meta:
        db_table = "risk_events"
