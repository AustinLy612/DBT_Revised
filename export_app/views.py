import csv
import json

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from accounts.models import User


def _is_admin(user):
    return user.is_authenticated and (user.role == "admin" or user.is_staff)


@login_required
def export_page_view(request):
    """Admin export page — lists students with export options."""
    if not _is_admin(request.user):
        raise PermissionDenied("你没有权限访问导出功能。")

    students = User.objects.filter(role="student").order_by("username")
    return render(
        request,
        "export_app/export_page.html",
        {"students": students},
    )


@login_required
def export_user_json_view(request, user_id):
    """Export a single student's full data as JSON."""
    if not _is_admin(request.user):
        raise PermissionDenied("你没有权限访问导出功能。")

    student = get_object_or_404(User, id=user_id)

    from .services import export_user_json

    json_str = export_user_json(student)

    _log_export(request.user, "export_data", "user", user_id, "json")

    response = HttpResponse(json_str, content_type="application/json; charset=utf-8")
    filename = f"user_{student.username}_data.json"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def export_user_csv_view(request, user_id):
    """Export a single student's full data as CSV."""
    if not _is_admin(request.user):
        raise PermissionDenied("你没有权限访问导出功能。")

    student = get_object_or_404(User, id=user_id)

    from .services import export_user_csv

    csv_str = export_user_csv(student)

    _log_export(request.user, "export_data", "user", user_id, "csv")

    response = HttpResponse(csv_str, content_type="text/csv; charset=utf-8-sig")
    filename = f"user_{student.username}_data.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def export_users_json_view(request):
    """Export multiple students' data as a single JSON file."""
    if not _is_admin(request.user):
        raise PermissionDenied("你没有权限访问导出功能。")

    user_ids = request.GET.getlist("user_ids")
    if not user_ids:
        user_ids = list(
            User.objects.filter(role="student").values_list("id", flat=True)
        )

    from .services import aggregate_user_data

    all_data = {}
    for uid in user_ids:
        try:
            student = User.objects.get(id=uid)
            all_data[uid] = aggregate_user_data(student)
        except User.DoesNotExist:
            all_data[uid] = {"error": "user not found"}

    json_str = json.dumps(all_data, ensure_ascii=False, indent=2)

    _log_export(
        request.user,
        "export_data",
        "users_bulk",
        ",".join(user_ids),
        "json",
        export_scope={"user_count": len(user_ids)},
    )

    response = HttpResponse(json_str, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="users_export.json"'
    return response


@login_required
def export_users_csv_view(request):
    """Export multiple students' data as a single CSV file."""
    if not _is_admin(request.user):
        raise PermissionDenied("你没有权限访问导出功能。")

    user_ids = request.GET.getlist("user_ids")
    if not user_ids:
        user_ids = list(
            User.objects.filter(role="student").values_list("id", flat=True)
        )

    from .services import export_user_csv as _csv_for_user
    from io import StringIO

    output = StringIO()
    output.write("﻿")  # BOM for Excel

    for uid in user_ids:
        try:
            student = User.objects.get(id=uid)
            output.write(f"=== 用户: {student.username} ===\n")
            output.write(_csv_for_user(student))
            output.write("\n\n")
        except User.DoesNotExist:
            output.write(f"=== 用户 {uid}: 不存在 ===\n\n")

    csv_str = output.getvalue()

    _log_export(
        request.user,
        "export_data",
        "users_bulk",
        ",".join(user_ids),
        "csv",
        export_scope={"user_count": len(user_ids)},
    )

    response = HttpResponse(csv_str, content_type="text/csv; charset=utf-8-sig")
    response["Content-Disposition"] = 'attachment; filename="users_export.csv"'
    return response


def _log_export(admin, operation_type, target_type, target_id, export_format, export_scope=None):
    """Create an AdminOperationLog entry for an export operation."""
    from .models import AdminOperationLog

    AdminOperationLog.objects.create(
        admin=admin,
        operation_type=operation_type,
        target_type=target_type,
        target_id=target_id,
        export_format=export_format,
        export_scope=export_scope or {},
    )
