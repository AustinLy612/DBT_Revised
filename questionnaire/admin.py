from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "gender",
        "age",
        "grade",
        "profile_completed_display",
        "created_at",
        "updated_at",
    ]
    list_filter = ["gender", "grade", "created_at"]
    search_fields = ["user__username", "other_hobby_text", "other_concern_text"]
    readonly_fields = ["profile_id", "created_at", "updated_at"]
    fieldsets = (
        (_("用户信息"), {"fields": ("user", "gender", "age", "grade")}),
        (_("兴趣与困扰"), {"fields": ("hobby_tags", "concern_tags", "other_hobby_text", "other_concern_text")}),
        (_("系统字段"), {"fields": ("profile_id", "created_at", "updated_at")}),
    )

    @admin.display(description="问卷状态", boolean=True)
    def profile_completed_display(self, obj):
        return obj.user.profile_completed
