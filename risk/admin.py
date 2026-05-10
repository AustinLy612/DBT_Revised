from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import RiskEvent


@admin.register(RiskEvent)
class RiskEventAdmin(admin.ModelAdmin):
    list_display = [
        "risk_event_id_short",
        "user",
        "session_short",
        "detection_source",
        "session_stopped_display",
        "follow_up_mode",
        "trigger_time",
    ]
    list_filter = ["detection_source", "session_stopped", "follow_up_mode", "trigger_time"]
    search_fields = ["user__username", "trigger_text", "action_taken"]
    readonly_fields = ["risk_event_id", "trigger_time"]
    fieldsets = (
        (_("基本信息"), {"fields": ("risk_event_id", "user", "session", "trigger_time")}),
        (_("风险详情"), {"fields": ("trigger_text", "detection_source", "session_stopped")}),
        (_("处理信息"), {"fields": ("action_taken", "follow_up_mode", "exported_flag")}),
    )

    @admin.display(description="事件ID")
    def risk_event_id_short(self, obj):
        return obj.risk_event_id[:12] + "..."

    @admin.display(description="会话")
    def session_short(self, obj):
        return obj.session.session_id[:12] + "..."

    @admin.display(description="会话已中止", boolean=True)
    def session_stopped_display(self, obj):
        return obj.session_stopped
