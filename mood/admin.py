from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Achievement, MoodRecord, UserAchievement


@admin.register(MoodRecord)
class MoodRecordAdmin(admin.ModelAdmin):
    list_display = ["mood_id_short", "user", "mood_value", "emoji", "context", "session_short", "created_at"]
    list_filter = ["context", "mood_value", "created_at"]
    search_fields = ["user__username", "note"]
    readonly_fields = ["mood_id", "created_at"]
    fieldsets = (
        (_("基本信息"), {"fields": ("mood_id", "user", "session", "context", "created_at")}),
        (_("情绪记录"), {"fields": ("mood_value", "emoji", "note")}),
    )

    @admin.display(description="情绪ID")
    def mood_id_short(self, obj):
        return obj.mood_id[:12] + "..."

    @admin.display(description="会话")
    def session_short(self, obj):
        if obj.session:
            return obj.session.session_id[:12] + "..."
        return "-"


@admin.register(Achievement)
class AchievementAdmin(admin.ModelAdmin):
    list_display = ["achievement_id_short", "name_cn", "description_preview", "icon", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name_cn", "description_cn"]
    readonly_fields = ["achievement_id"]
    fieldsets = (
        (_("基本信息"), {"fields": ("achievement_id", "name_cn", "description_cn", "icon", "is_active")}),
        (_("触发规则"), {"fields": ("trigger_rule",)}),
    )

    @admin.display(description="成就ID")
    def achievement_id_short(self, obj):
        return obj.achievement_id[:12] + "..."

    @admin.display(description="描述")
    def description_preview(self, obj):
        return obj.description_cn[:40] + "..." if len(obj.description_cn) > 40 else obj.description_cn


@admin.register(UserAchievement)
class UserAchievementAdmin(admin.ModelAdmin):
    list_display = ["user", "achievement_name", "unlocked_at"]
    list_filter = ["unlocked_at"]
    search_fields = ["user__username", "achievement__name_cn"]
    readonly_fields = ["id", "unlocked_at"]
    fieldsets = (
        (_("基本信息"), {"fields": ("id", "user", "achievement", "unlocked_at")}),
    )

    @admin.display(description="成就名称")
    def achievement_name(self, obj):
        return obj.achievement.name_cn
