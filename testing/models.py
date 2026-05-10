from dbt_platform.utils import gen_uuid

from django.db import models


class Test(models.Model):
    class Status(models.TextChoices):
        ONGOING = "ongoing", "进行中"
        COMPLETED = "completed", "已完成"
        USER_TERMINATED = "user_terminated", "用户终止"

    test_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    session = models.ForeignKey(
        "teaching.TeachingSession", on_delete=models.CASCADE, related_name="tests"
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="tests"
    )
    attempt_no = models.IntegerField(default=1)
    rag_context_ids = models.JSONField(default=list)
    total_questions = models.IntegerField(default=5)
    correct_count = models.IntegerField(default=0)
    passed = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ONGOING
    )
    post_mood_id = models.CharField(max_length=36, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tests"


class TestQuestion(models.Model):
    question_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name="questions")
    scenario_text = models.TextField(blank=True, default="")
    question_text = models.TextField()
    options = models.JSONField(default=list)
    correct_option = models.CharField(max_length=4)
    user_answer = models.CharField(max_length=4, blank=True, default="")
    is_correct = models.BooleanField(default=False)
    explanation = models.TextField(blank=True, default="")
    source_chunk_ids = models.JSONField(default=list)
    image_prompt = models.TextField(blank=True, default="")
    temporary_image_url = models.TextField(blank=True, default="")
    image_model = models.CharField(max_length=64, blank=True, default="")
    image_generated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "test_questions"
        ordering = ["created_at"]
