from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render


@login_required
def dashboard_view(request):
    """Report viewer dashboard — lists all students."""
    user = request.user

    if user.role not in ("report_viewer", "admin"):
        raise PermissionDenied("你没有权限访问报告系统。")

    from accounts.models import User

    authorized_students = User.objects.filter(role="student").order_by("username")

    return render(
        request,
        "reports/dashboard.html",
        {"authorized_students": authorized_students},
    )


@login_required
def student_report_view(request, student_id):
    """Individual student report — report_viewer and admin can view all."""
    user = request.user

    if user.role not in ("report_viewer", "admin"):
        raise PermissionDenied("你没有权限访问报告系统。")

    from accounts.models import User

    try:
        student = User.objects.get(id=student_id)
    except User.DoesNotExist:
        raise PermissionDenied("学生不存在。")

    from .services import get_student_report_data

    report_data = get_student_report_data(student)

    _log_report_access(user, student, "view", "individual_report")

    return render(
        request,
        "reports/student_report.html",
        report_data,
    )


@login_required
def student_report_pdf_view(request, student_id):
    """Generate and return a PDF version of the student report."""
    user = request.user

    if user.role not in ("report_viewer", "admin"):
        raise PermissionDenied("你没有权限访问报告系统。")

    from accounts.models import User

    student = get_object_or_404(User, id=student_id)

    from .services import get_student_report_data

    report_data = get_student_report_data(student)

    html = render(
        request,
        "reports/student_report_pdf.html",
        report_data,
    ).content.decode("utf-8")

    from weasyprint import HTML

    doc = HTML(string=html, base_url=request.build_absolute_uri("/"))
    pdf_bytes = doc.write_pdf()

    _log_report_access(user, student, "export", "individual_report", export_format="pdf")

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    filename = f"report_{student.username}.pdf"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _log_report_access(viewer, student, action_type, report_type, export_format=""):
    """Create a ReportAccessLog entry."""
    from .models import ReportAccessLog

    ReportAccessLog.objects.create(
        viewer=viewer,
        viewer_role=viewer.role,
        student=student,
        action_type=action_type,
        report_type=report_type,
        export_format=export_format,
    )
