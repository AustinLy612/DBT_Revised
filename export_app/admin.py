from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import AdminOperationLog


@admin.register(AdminOperationLog)
class AdminOperationLogAdmin(admin.ModelAdmin):
    list_display = [
        "log_id_short",
        "admin",
        "operation_type",
        "target_type",
        "target_id_short",
        "export_format_display",
        "created_at",
    ]
    list_filter = ["operation_type", "export_format", "created_at"]
    search_fields = ["admin__username", "operation_type", "target_type", "target_id"]
    readonly_fields = ["log_id", "created_at"]
    fieldsets = (
        (_("基本信息"), {"fields": ("log_id", "admin", "operation_type", "created_at")}),
        (_("操作目标"), {"fields": ("target_type", "target_id")}),
        (_("导出信息"), {"fields": ("export_format", "export_scope")}),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="日志ID")
    def log_id_short(self, obj):
        return obj.log_id[:12] + "..."

    @admin.display(description="目标ID")
    def target_id_short(self, obj):
        return obj.target_id[:12] + "..." if obj.target_id else "-"

    @admin.display(description="导出格式")
    def export_format_display(self, obj):
        return obj.export_format if obj.export_format else "-"
