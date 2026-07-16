from django.contrib import admin

from .models import EMASubmission


@admin.register(EMASubmission)
class EMASubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "submission_id",
        "user",
        "created_at",
        "distress_score",
        "nssi_urge_score",
        "suicide_urge_score",
        "used_dbt_skill",
    )
    list_filter = ("created_at", "used_dbt_skill")
    search_fields = ("user__username",)
    readonly_fields = ("submission_id", "created_at")
    ordering = ("-created_at",)
