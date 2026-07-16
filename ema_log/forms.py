from django import forms
from django.core.exceptions import ValidationError

from .models import EMASubmission

SKILL_CHOICES = [
    ("正念", "正念"),
    ("痛苦耐受", "痛苦耐受"),
    ("情绪调节", "情绪调节"),
    ("人际效能", "人际效能"),
]


class EMAForm(forms.ModelForm):
    dbt_skills_used = forms.MultipleChoiceField(
        label="使用过哪些DBT技能？",
        choices=SKILL_CHOICES,
        widget=forms.CheckboxSelectMultiple(
            attrs={"class": "skill-checkbox"}
        ),
        required=False,
    )

    class Meta:
        model = EMASubmission
        fields = [
            "sad_score", "anxious_score", "angry_score", "calm_score",
            "hopeful_score", "distress_score", "nssi_urge_score",
            "suicide_urge_score",
            "used_dbt_skill", "dbt_skills_used", "skill_effectiveness_score",
            "medical_doctor_visit", "medical_group_therapy",
            "medical_medication_change",
        ]
        widgets = {
            "used_dbt_skill": forms.RadioSelect(
                choices=[(True, "是"), (False, "否")],
                attrs={"class": "dbt-skill-radio"},
            ),
        }

    def clean(self):
        cleaned = super().clean()
        used_dbt_skill = cleaned.get("used_dbt_skill")
        skills = cleaned.get("dbt_skills_used", [])
        effectiveness = cleaned.get("skill_effectiveness_score")

        if used_dbt_skill:
            if not skills:
                self.add_error("dbt_skills_used", "请选择至少一项使用的DBT技能。")
            if effectiveness is None:
                self.add_error(
                    "skill_effectiveness_score",
                    "请评价技能使用对你的帮助程度。",
                )
        return cleaned

    def clean_skill_effectiveness_score(self):
        score = self.cleaned_data.get("skill_effectiveness_score")
        if score is not None and (score < 1 or score > 10):
            raise ValidationError("请选择1-10之间的分值。")
        return score

    def _validate_vas_score(self, value, field_name):
        if value is None or value < 1 or value > 10:
            raise ValidationError(f"请选择1-10之间的分值。")
        return value

    def clean_sad_score(self):
        return self._validate_vas_score(self.cleaned_data.get("sad_score"), "sad_score")

    def clean_anxious_score(self):
        return self._validate_vas_score(self.cleaned_data.get("anxious_score"), "anxious_score")

    def clean_angry_score(self):
        return self._validate_vas_score(self.cleaned_data.get("angry_score"), "angry_score")

    def clean_calm_score(self):
        return self._validate_vas_score(self.cleaned_data.get("calm_score"), "calm_score")

    def clean_hopeful_score(self):
        return self._validate_vas_score(self.cleaned_data.get("hopeful_score"), "hopeful_score")

    def clean_distress_score(self):
        return self._validate_vas_score(self.cleaned_data.get("distress_score"), "distress_score")

    def clean_nssi_urge_score(self):
        return self._validate_vas_score(self.cleaned_data.get("nssi_urge_score"), "nssi_urge_score")

    def clean_suicide_urge_score(self):
        return self._validate_vas_score(
            self.cleaned_data.get("suicide_urge_score"), "suicide_urge_score"
        )
