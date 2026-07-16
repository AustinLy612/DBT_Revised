from django.contrib import admin
from django.urls import include, path

from . import views

urlpatterns = [
    path("", views.index_view, name="index"),
    path("admin/", admin.site.urls),
    # App URL namespaces (wired up as apps gain views)
    path("accounts/", include("accounts.urls")),
    path("questionnaire/", include("questionnaire.urls")),
    path("teaching/", include("teaching.urls")),
    path("testing/", include("testing.urls")),
    path("mood/", include("mood.urls")),
    path("risk/", include("risk.urls")),
    path("knowledge/", include("knowledge_base.urls")),
    path("export/", include("export_app.urls")),
    path("reports/", include("reports.urls")),
    path("media/", include("media_app.urls")),
    path("ema-log/", include("ema_log.urls")),
    # Health check
    path("health/", include("dbt_platform.health_urls")),
]
