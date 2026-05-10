from dbt_platform.utils import gen_uuid

from django.db import models


class MoodRecord(models.Model):
    class Context(models.TextChoices):
        MANUAL = "manual", "手动记录"
        BEFORE_TEACHING = "before_teaching", "教学前"
        AFTER_TEACHING = "after_teaching", "教学后"
        AFTER_TESTING = "after_testing", "测试后"

    mood_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="mood_records"
    )
    session = models.ForeignKey(
        "teaching.TeachingSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mood_records",
    )
    mood_value = models.IntegerField()
    emoji = models.CharField(max_length=8)
    note = models.TextField(blank=True, default="")
    context = models.CharField(max_length=16, choices=Context.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mood_records"


class Achievement(models.Model):
    achievement_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    name_cn = models.CharField(max_length=64)
    description_cn = models.TextField()
    trigger_rule = models.JSONField(default=dict)
    icon = models.CharField(max_length=32, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "achievements"


class UserAchievement(models.Model):
    id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="achievements"
    )
    achievement = models.ForeignKey(
        Achievement, on_delete=models.CASCADE, related_name="users"
    )
    unlocked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_achievements"
        unique_together = ("user", "achievement")
