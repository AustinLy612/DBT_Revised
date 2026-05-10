from dbt_platform.utils import gen_uuid

from django.db import models


class AdminOperationLog(models.Model):
    log_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    admin = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="admin_operations"
    )
    operation_type = models.CharField(max_length=32)
    target_type = models.CharField(max_length=64)
    target_id = models.CharField(max_length=36, blank=True, default="")
    export_format = models.CharField(max_length=10, blank=True, default="")
    export_scope = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "admin_operation_logs"
