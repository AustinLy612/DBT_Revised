from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Test, TestQuestion


class TestQuestionInline(admin.TabularInline):
    model = TestQuestion
    fields = ["question_text_preview", "user_answer", "correct_option", "is_correct"]
    readonly_fields = ["question_text_preview", "user_answer", "correct_option", "is_correct"]
    extra = 0
    can_delete = False
    max_num = 20

    def question_text_preview(self, obj):
        return obj.question_text[:60] + "..." if len(obj.question_text) > 60 else obj.question_text

    question_text_preview.short_description = "题目"

    def has_add_permission(self, request, obj):
        return False


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = [
        "test_id_short",
        "user",
        "session_short",
        "attempt_no",
        "status",
        "passed_display",
        "correct_count",
        "total_questions",
        "created_at",
    ]
    list_filter = ["status", "passed", "attempt_no", "created_at"]
    search_fields = ["user__username"]
    readonly_fields = ["test_id", "created_at"]
    fieldsets = (
        (_("基本信息"), {"fields": ("test_id", "user", "session", "status", "created_at")}),
        (_("测试结果"), {"fields": ("attempt_no", "total_questions", "correct_count", "passed")}),
        (_("RAG上下文"), {"fields": ("rag_context_ids",)}),
    )
    inlines = [TestQuestionInline]

    @admin.display(description="测试ID")
    def test_id_short(self, obj):
        return obj.test_id[:12] + "..."

    @admin.display(description="会话")
    def session_short(self, obj):
        return obj.session.session_id[:12] + "..."

    @admin.display(description="通过", boolean=True)
    def passed_display(self, obj):
        return obj.passed


@admin.register(TestQuestion)
class TestQuestionAdmin(admin.ModelAdmin):
    list_display = [
        "question_id_short",
        "test_link",
        "question_text_preview",
        "user_answer",
        "correct_option",
        "is_correct",
    ]
    list_filter = ["is_correct"]
    search_fields = ["question_text", "explanation"]
    readonly_fields = ["question_id", "image_generated_at"]
    fieldsets = (
        (_("基本信息"), {"fields": ("question_id", "test", "question_text", "options", "correct_option")}),
        (_("用户作答"), {"fields": ("user_answer", "is_correct", "explanation")}),
        (_("场景与来源"), {"fields": ("scenario_text", "source_chunk_ids")}),
        (_("图像"), {"fields": ("image_prompt", "temporary_image_url", "image_model", "image_generated_at")}),
    )

    @admin.display(description="题目ID")
    def question_id_short(self, obj):
        return obj.question_id[:12] + "..."

    @admin.display(description="测试")
    def test_link(self, obj):
        return obj.test.test_id[:12] + "..."

    @admin.display(description="题目预览")
    def question_text_preview(self, obj):
        return obj.question_text[:60] + "..." if len(obj.question_text) > 60 else obj.question_text
