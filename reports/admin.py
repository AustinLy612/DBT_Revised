from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import ReportAccessLog


@admin.register(ReportAccessLog)
class ReportAccessLogAdmin(admin.ModelAdmin):
    list_display = [
        "log_id_short",
        "viewer",
        "viewer_role",
        "student",
        "action_type",
        "report_type",
        "export_format_display",
        "created_at",
    ]
    list_filter = ["action_type", "viewer_role", "report_type", "export_format", "created_at"]
    search_fields = ["viewer__username", "student__username"]
    readonly_fields = ["log_id", "created_at"]
    fieldsets = (
        (_("基本信息"), {"fields": ("log_id", "viewer", "viewer_role", "action_type", "created_at")}),
        (_("目标"), {"fields": ("student", "report_type")}),
        (_("导出"), {"fields": ("export_format",)}),
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

    @admin.display(description="导出格式")
    def export_format_display(self, obj):
        return obj.export_format if obj.export_format else "-"
