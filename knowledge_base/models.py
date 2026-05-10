from dbt_platform.utils import gen_uuid

from django.db import models


class KnowledgeDocument(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "已上传"
        PROCESSING = "processing", "处理中"
        RETRIEVABLE = "retrievable", "可检索"
        FAILED = "failed", "失败"

    class Difficulty(models.TextChoices):
        BEGINNER = "beginner", "初级"
        INTERMEDIATE = "intermediate", "中级"
        ADVANCED = "advanced", "高级"

    document_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    title = models.CharField(max_length=256)
    module = models.CharField(max_length=64)
    skill = models.CharField(max_length=128, blank=True, default="")
    version = models.CharField(max_length=16, default="1.0")
    difficulty = models.CharField(
        max_length=16, choices=Difficulty.choices, default=Difficulty.BEGINNER
    )
    is_beginner_friendly = models.BooleanField(default=True)
    scenario_tags = models.JSONField(default=list)
    risk_flags = models.JSONField(default=list)
    file_url = models.TextField(blank=True, default="")
    uploaded_by = models.CharField(max_length=36, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.UPLOADED
    )
    error_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "knowledge_documents"


class KnowledgeChunk(models.Model):
    chunk_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="chunks"
    )
    chunk_text = models.TextField()
    embedding_id = models.CharField(max_length=64, blank=True, default="")
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "knowledge_chunks"


class RetrievalLog(models.Model):
    class UseCase(models.TextChoices):
        TEACHING = "teaching", "教学"
        TEST_GENERATION = "test_generation", "测试生成"
        EXPLANATION = "explanation", "解析"
        RETEST = "retest", "重测"
        RISK = "risk", "风险"

    retrieval_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="retrieval_logs"
    )
    session = models.ForeignKey(
        "teaching.TeachingSession",
        on_delete=models.CASCADE,
        related_name="retrieval_logs",
        null=True,
        blank=True,
    )
    query = models.TextField()
    retrieved_chunk_ids = models.JSONField(default=list)
    use_case = models.CharField(max_length=20, choices=UseCase.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "retrieval_logs"
