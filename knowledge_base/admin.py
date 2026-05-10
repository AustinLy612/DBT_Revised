import uuid

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path
from django.utils.translation import gettext_lazy as _

from .forms import KnowledgeDocumentUploadForm
from .models import KnowledgeChunk, KnowledgeDocument, RetrievalLog
from .storage import upload_document
from . import tasks


class KnowledgeChunkInline(admin.TabularInline):
    model = KnowledgeChunk
    fields = ["chunk_text_preview", "embedding_id", "created_at"]
    readonly_fields = ["chunk_text_preview", "embedding_id", "created_at"]
    extra = 0
    can_delete = False
    max_num = 200

    def chunk_text_preview(self, obj):
        return obj.chunk_text[:80] + "..." if len(obj.chunk_text) > 80 else obj.chunk_text

    chunk_text_preview.short_description = "文本预览"

    def has_add_permission(self, request, obj):
        return False


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = [
        "document_id_short",
        "title",
        "module",
        "skill",
        "version",
        "difficulty",
        "status",
        "chunk_count",
        "uploaded_at",
    ]
    list_filter = ["status", "module", "difficulty", "uploaded_at"]
    search_fields = ["title", "module", "skill"]
    readonly_fields = ["document_id", "uploaded_at", "status", "file_url", "error_message"]
    fieldsets = (
        (_("基本信息"), {
            "fields": ("document_id", "title", "module", "skill", "version", "difficulty", "status")
        }),
        (_("元数据"), {
            "fields": ("is_beginner_friendly", "scenario_tags", "risk_flags")
        }),
        (_("文件信息"), {
            "fields": ("file_url", "uploaded_by", "uploaded_at", "error_message")
        }),
    )
    inlines = [KnowledgeChunkInline]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "upload/",
                self.admin_site.admin_view(self.upload_document_view),
                name="knowledge_base_upload",
            ),
        ]
        return custom + urls

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            return KnowledgeDocumentUploadForm
        return super().get_form(request, obj, **kwargs)

    def save_model(self, request, obj, form, change):
        if not change:
            uploaded_file = form.cleaned_data.get("file")
            obj.uploaded_by = str(request.user.pk)
            obj.status = KnowledgeDocument.Status.UPLOADED
            super().save_model(request, obj, form, change)

            ext = uploaded_file.name.rsplit(".", 1)[-1].lower() if "." in uploaded_file.name else "bin"
            object_name = f"knowledge/{obj.document_id}/{uuid.uuid4().hex}.{ext}"
            file_bytes = uploaded_file.read()
            upload_document(file_bytes, object_name)
            obj.file_url = object_name
            obj.status = KnowledgeDocument.Status.PROCESSING
            obj.save(update_fields=["file_url", "status"])

            tasks.process_document_async.delay(
                document_id=obj.document_id,
                object_name=object_name,
                filename=uploaded_file.name,
            )
            messages.info(request, _("文档已上传，正在后台处理..."))
            return redirect("admin:knowledge_base_knowledgedocument_changelist")
        else:
            super().save_model(request, obj, form, change)

    def upload_document_view(self, request):
        from django.shortcuts import render
        if request.method == "POST":
            form = KnowledgeDocumentUploadForm(request.POST, request.FILES)
            if form.is_valid():
                self.save_model(request, form.save(commit=False), form, change=False)
                return redirect("admin:knowledge_base_knowledgedocument_changelist")
        else:
            form = KnowledgeDocumentUploadForm()
        return render(
            request,
            "admin/knowledge_base/upload.html",
            {"form": form, "title": _("上传知识文档")},
        )

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if object_id is None:
            return redirect("admin:knowledge_base_upload")
        return super().changeform_view(request, object_id, form_url, extra_context)

    @admin.display(description="文档ID")
    def document_id_short(self, obj):
        return obj.document_id[:12] + "..."

    @admin.display(description="分块数")
    def chunk_count(self, obj):
        return obj.chunks.count()


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ["chunk_id_short", "document_link", "chunk_text_preview", "embedding_id_short", "created_at"]
    search_fields = ["chunk_text"]
    readonly_fields = ["chunk_id", "created_at"]
    fieldsets = (
        (_("基本信息"), {"fields": ("chunk_id", "document", "embedding_id", "created_at")}),
        (_("内容"), {"fields": ("chunk_text",)}),
        (_("元数据"), {"fields": ("metadata",)}),
    )

    @admin.display(description="分块ID")
    def chunk_id_short(self, obj):
        return obj.chunk_id[:12] + "..."

    @admin.display(description="文档")
    def document_link(self, obj):
        return obj.document.title

    @admin.display(description="文本预览")
    def chunk_text_preview(self, obj):
        return obj.chunk_text[:60] + "..." if len(obj.chunk_text) > 60 else obj.chunk_text

    @admin.display(description="向量ID")
    def embedding_id_short(self, obj):
        return obj.embedding_id[:16] + "..." if obj.embedding_id else "-"


@admin.register(RetrievalLog)
class RetrievalLogAdmin(admin.ModelAdmin):
    list_display = [
        "retrieval_id_short",
        "user",
        "session_short",
        "query_preview",
        "use_case",
        "chunk_count",
        "created_at",
    ]
    list_filter = ["use_case", "created_at"]
    search_fields = ["user__username", "query"]
    readonly_fields = ["retrieval_id", "created_at"]
    fieldsets = (
        (_("基本信息"), {"fields": ("retrieval_id", "user", "session", "use_case", "created_at")}),
        (_("检索内容"), {"fields": ("query", "retrieved_chunk_ids")}),
    )

    @admin.display(description="检索ID")
    def retrieval_id_short(self, obj):
        return obj.retrieval_id[:12] + "..."

    @admin.display(description="会话")
    def session_short(self, obj):
        return obj.session.session_id[:12] + "..."

    @admin.display(description="查询预览")
    def query_preview(self, obj):
        return obj.query[:60] + "..." if len(obj.query) > 60 else obj.query

    @admin.display(description="返回分块数")
    def chunk_count(self, obj):
        return len(obj.retrieved_chunk_ids) if isinstance(obj.retrieved_chunk_ids, list) else 0
