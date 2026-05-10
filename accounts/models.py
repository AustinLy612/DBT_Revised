from dbt_platform.utils import gen_uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user with role field for the three-tier permission model.

    Saving a user with role='admin' automatically grants is_staff and
    is_superuser so Django's admin panel actually admits them.
    """

    class Role(models.TextChoices):
        STUDENT = "student", "学生用户"
        REPORT_VIEWER = "report_viewer", "报告查看用户"
        ADMIN = "admin", "管理员"

    id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT)
    invite_code = models.CharField(max_length=64, blank=True, default="")
    profile_completed = models.BooleanField(default=False)

    class Meta:
        db_table = "users"

    def save(self, *args, **kwargs):
        if self.role == self.Role.ADMIN:
            self.is_staff = True
            self.is_superuser = True
        else:
            self.is_staff = False
            self.is_superuser = False
        super().save(*args, **kwargs)


class InviteCode(models.Model):
    """Invite codes managed by admins for controlled registration."""

    class Status(models.TextChoices):
        ACTIVE = "active", "可用"
        USED = "used", "已使用"
        DISABLED = "disabled", "已停用"

    id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    code = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.CharField(max_length=36, blank=True, default="")
    used_by = models.CharField(max_length=36, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "invite_codes"


class ReportViewerAssignment(models.Model):
    """Maps a report_viewer to the students they can view."""

    id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    viewer = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="viewer_assignments",
    )
    student = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="student_assignments",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "report_viewer_assignments"
        unique_together = ["viewer", "student"]
