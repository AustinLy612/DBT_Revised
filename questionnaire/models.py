from dbt_platform.utils import gen_uuid

from django.db import models


class UserProfile(models.Model):
    class Gender(models.TextChoices):
        MALE = "male", "男"
        FEMALE = "female", "女"
        OTHER = "other", "其他"
        PREFER_NOT_TO_SAY = "prefer_not_to_say", "不愿透露"

    GRADE_CHOICES = [
        ("grade_7", "初一"),
        ("grade_8", "初二"),
        ("grade_9", "初三"),
        ("grade_10", "高一"),
        ("grade_11", "高二"),
        ("grade_12", "高三"),
    ]

    profile_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    user = models.OneToOneField(
        "accounts.User", on_delete=models.CASCADE, related_name="profile"
    )
    gender = models.CharField(max_length=20, choices=Gender.choices)
    age = models.IntegerField()
    grade = models.CharField(max_length=10, choices=GRADE_CHOICES)
    hobby_tags = models.JSONField(default=list)
    concern_tags = models.JSONField(default=list)
    other_hobby_text = models.TextField(blank=True, default="")
    other_concern_text = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_profiles"
