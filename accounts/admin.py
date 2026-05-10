from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from .models import InviteCode, ReportViewerAssignment, User


# ── Inlines for User admin detail page ──


class UserProfileInline(admin.StackedInline):
    model = None  # set below via get_inline_models

    @classmethod
    def build(cls):
        """Return a concrete inline class bound to UserProfile."""
        from questionnaire.models import UserProfile

        class _UserProfileInline(admin.StackedInline):
            model = UserProfile
            fields = ["gender", "age", "grade", "hobby_tags", "concern_tags", "updated_at"]
            readonly_fields = ["updated_at"]
            can_delete = False
            extra = 0
            max_num = 1

            def has_add_permission(self, request, obj):
                return False

            @admin.display(description="爱好标签")
            def hobby_tags_display(self, obj):
                return ", ".join(obj.hobby_tags) if obj.hobby_tags else "-"

            @admin.display(description="困扰标签")
            def concern_tags_display(self, obj):
                return ", ".join(obj.concern_tags) if obj.concern_tags else "-"

        return _UserProfileInline


class TeachingSessionInline(admin.TabularInline):
    from teaching.models import TeachingSession as _TS

    model = _TS
    fields = ["session_link", "status", "selected_module", "selected_skill", "started_at"]
    readonly_fields = ["session_link", "status", "selected_module", "selected_skill", "started_at"]
    extra = 0
    can_delete = False
    show_change_link = True

    def session_link(self, obj):
        return obj.session_id[:12] + "..."

    session_link.short_description = "会话ID"

    def has_add_permission(self, request, obj):
        return False


class TestInline(admin.TabularInline):
    from testing.models import Test as _T

    model = _T
    fields = ["test_id_short", "attempt_no", "correct_count", "total_questions", "passed", "status", "created_at"]
    readonly_fields = ["test_id_short", "attempt_no", "correct_count", "total_questions", "passed", "status", "created_at"]
    extra = 0
    can_delete = False
    show_change_link = True

    def test_id_short(self, obj):
        return obj.test_id[:12] + "..."

    test_id_short.short_description = "测试ID"

    def has_add_permission(self, request, obj):
        return False


class MoodRecordInline(admin.TabularInline):
    from mood.models import MoodRecord as _MR

    model = _MR
    fields = ["mood_value", "emoji", "context", "created_at"]
    readonly_fields = ["mood_value", "emoji", "context", "created_at"]
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj):
        return False


class RiskEventInline(admin.TabularInline):
    from risk.models import RiskEvent as _RE

    model = _RE
    fields = ["detection_source", "session_stopped_display", "trigger_preview", "trigger_time"]
    readonly_fields = ["detection_source", "session_stopped_display", "trigger_preview", "trigger_time"]
    extra = 0
    can_delete = False
    show_change_link = True

    def trigger_preview(self, obj):
        return obj.trigger_text[:50] + "..." if len(obj.trigger_text) > 50 else obj.trigger_text

    trigger_preview.short_description = "触发文本"

    @admin.display(description="已中止", boolean=True)
    def session_stopped_display(self, obj):
        return obj.session_stopped

    def has_add_permission(self, request, obj):
        return False


class UserAchievementInline(admin.TabularInline):
    from mood.models import UserAchievement as _UA

    model = _UA
    fields = ["achievement_name", "unlocked_at"]
    readonly_fields = ["achievement_name", "unlocked_at"]
    extra = 0
    can_delete = False

    def achievement_name(self, obj):
        return obj.achievement.name_cn

    achievement_name.short_description = "成就"

    def has_add_permission(self, request, obj):
        return False


class RetrievalLogInline(admin.TabularInline):
    from knowledge_base.models import RetrievalLog as _RL

    model = _RL
    fields = ["query_preview", "use_case", "created_at"]
    readonly_fields = ["query_preview", "use_case", "created_at"]
    extra = 0
    can_delete = False
    show_change_link = True

    def query_preview(self, obj):
        return obj.query[:60] + "..." if len(obj.query) > 60 else obj.query

    query_preview.short_description = "查询"

    def has_add_permission(self, request, obj):
        return False


class AdminOperationInline(admin.TabularInline):
    from export_app.models import AdminOperationLog as _AOL

    model = _AOL
    fk_name = "admin"
    fields = ["operation_type", "target_type", "target_id", "created_at"]
    readonly_fields = ["operation_type", "target_type", "target_id", "created_at"]
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj):
        return False


class ReportAccessByViewerInline(admin.TabularInline):
    from reports.models import ReportAccessLog as _RAL

    model = _RAL
    fk_name = "viewer"
    fields = ["student_link", "action_type", "report_type", "created_at"]
    readonly_fields = ["student_link", "action_type", "report_type", "created_at"]
    extra = 0
    can_delete = False
    show_change_link = True

    def student_link(self, obj):
        return obj.student.username

    student_link.short_description = "学生"

    def has_add_permission(self, request, obj):
        return False


class ReportAccessByStudentInline(admin.TabularInline):
    from reports.models import ReportAccessLog as _RAL

    model = _RAL
    fk_name = "student"
    fields = ["viewer_link", "action_type", "report_type", "created_at"]
    readonly_fields = ["viewer_link", "action_type", "report_type", "created_at"]
    extra = 0
    can_delete = False
    show_change_link = True

    def viewer_link(self, obj):
        return obj.viewer.username

    viewer_link.short_description = "查看者"

    def has_add_permission(self, request, obj):
        return False


class ViewerAssignmentInline(admin.TabularInline):
    model = ReportViewerAssignment
    fk_name = "viewer"
    fields = ["student", "is_active", "created_at"]
    readonly_fields = ["student", "is_active", "created_at"]
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj):
        return False


class StudentAssignmentInline(admin.TabularInline):
    model = ReportViewerAssignment
    fk_name = "student"
    fields = ["viewer", "is_active", "created_at"]
    readonly_fields = ["viewer", "is_active", "created_at"]
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj):
        return False


# ── User Admin ──


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = [
        "username",
        "role",
        "invite_code",
        "profile_completed",
        "is_active",
        "last_login",
    ]
    list_filter = ["role", "profile_completed", "is_active"]
    search_fields = ["username", "invite_code"]

    fieldsets = UserAdmin.fieldsets + (
        (_("DBT Platform"), {"fields": ("role", "invite_code", "profile_completed")}),
    )

    # User detail page shows ALL related records aggregated by category
    inlines = [
        UserProfileInline.build(),
        TeachingSessionInline,
        TestInline,
        MoodRecordInline,
        RiskEventInline,
        UserAchievementInline,
        RetrievalLogInline,
        AdminOperationInline,
        ViewerAssignmentInline,
        StudentAssignmentInline,
        ReportAccessByViewerInline,
        ReportAccessByStudentInline,
    ]

    def get_inlines(self, request, obj=None):
        """Dynamically filter inlines based on user role."""
        all_inlines = self.inlines
        if obj is None:
            return all_inlines

        # Role-specific inlines: show only what makes sense for this user's role
        skip = set()
        if obj.role != User.Role.ADMIN:
            skip.add(AdminOperationInline)
        if obj.role != User.Role.REPORT_VIEWER:
            skip.add(ViewerAssignmentInline)
            skip.add(ReportAccessByViewerInline)
        if obj.role != User.Role.STUDENT:
            skip.add(StudentAssignmentInline)
            skip.add(ReportAccessByStudentInline)

        return [i for i in all_inlines if i not in skip]


# ── Invite Code Admin ──


@admin.register(InviteCode)
class InviteCodeAdmin(admin.ModelAdmin):
    list_display = ["code", "status", "created_by", "used_by", "created_at", "used_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["code", "used_by"]
    readonly_fields = ["created_at", "used_at"]
    actions = ["batch_create_codes", "enable_codes", "disable_codes"]

    def get_readonly_fields(self, request, obj=None):
        if obj:  # 修改已有记录时 code 不可改
            return self.readonly_fields + ["code"]
        return self.readonly_fields  # 新增时可以输入 code

    @admin.action(description="生成一批邀请码")
    def batch_create_codes(self, request, queryset):
        import uuid

        count = 10
        new_codes = []
        for _ in range(count):
            code = str(uuid.uuid4())[:8].upper()
            new_codes.append(
                InviteCode(
                    code=code,
                    status=InviteCode.Status.ACTIVE,
                    created_by=str(request.user.id),
                )
            )
        InviteCode.objects.bulk_create(new_codes)
        self.message_user(request, f"已成功生成 {count} 个邀请码。")

    @admin.action(description="启用所选邀请码")
    def enable_codes(self, request, queryset):
        updated = queryset.filter(status=InviteCode.Status.DISABLED).update(
            status=InviteCode.Status.ACTIVE
        )
        self.message_user(request, f"已启用 {updated} 个邀请码。")

    @admin.action(description="停用所选邀请码")
    def disable_codes(self, request, queryset):
        updated = queryset.exclude(status=InviteCode.Status.USED).update(
            status=InviteCode.Status.DISABLED
        )
        self.message_user(request, f"已停用 {updated} 个邀请码。")


# ── ReportViewerAssignment Admin ──


@admin.register(ReportViewerAssignment)
class ReportViewerAssignmentAdmin(admin.ModelAdmin):
    list_display = ["viewer", "student", "is_active", "created_at"]
    list_filter = ["is_active", "created_at"]
    search_fields = ["viewer__username", "student__username"]
    autocomplete_fields = ["viewer", "student"]
    actions = ["activate_assignments", "deactivate_assignments"]

    @admin.action(description="启用所选授权")
    def activate_assignments(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"已启用 {updated} 条授权。")

    @admin.action(description="停用所选授权")
    def deactivate_assignments(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"已停用 {updated} 条授权。")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "viewer":
            kwargs["queryset"] = User.objects.filter(role="report_viewer")
        elif db_field.name == "student":
            kwargs["queryset"] = User.objects.filter(role="student")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
