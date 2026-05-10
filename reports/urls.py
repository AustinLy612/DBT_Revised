from django.urls import path

from . import views

app_name = "reports"
urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("student/<str:student_id>/", views.student_report_view, name="student_report"),
    path("student/<str:student_id>/pdf/", views.student_report_pdf_view, name="student_report_pdf"),
]
