from dbt_platform.utils import gen_uuid

from django.db import models


class EMASubmission(models.Model):
    submission_id = models.CharField(
        primary_key=True,
        max_length=36,
        default=gen_uuid,
        editable=False,
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="ema_submissions"
    )
    # 1. 实时情绪状态 Momentary Affect (1-10 VAS)
    sad_score = models.IntegerField()
    anxious_score = models.IntegerField()
    angry_score = models.IntegerField()
    calm_score = models.IntegerField()
    hopeful_score = models.IntegerField()
    # 2. 主观痛苦水平 Distress (1-10)
    distress_score = models.IntegerField()
    # 3. 自伤冲动 NSSI Urge (1-10)
    nssi_urge_score = models.IntegerField()
    # 4. 自杀冲动 Suicide Urge (1-10). Existing records created before
    # this question was added have no recoverable score.
    suicide_urge_score = models.IntegerField(null=True)
    # 5. DBT技能使用
    used_dbt_skill = models.BooleanField()
    dbt_skills_used = models.JSONField(default=list)
    # 6. 技能有效性评价 (1-10, nullable when no skill used)
    skill_effectiveness_score = models.IntegerField(null=True, blank=True)
    # 7. 医疗接触记录
    medical_doctor_visit = models.BooleanField(default=False)
    medical_group_therapy = models.BooleanField(default=False)
    medical_medication_change = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ema_submissions"
        ordering = ["-created_at"]
