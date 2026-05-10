from dbt_platform.utils import gen_uuid

from django.db import models


class ReportAccessLog(models.Model):
    log_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    viewer = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="report_accesses"
    )
    viewer_role = models.CharField(max_length=20)
    student = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="report_views",
    )
    action_type = models.CharField(max_length=12)
    report_type = models.CharField(max_length=32, blank=True, default="")
    export_format = models.CharField(max_length=10, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "report_access_logs"
