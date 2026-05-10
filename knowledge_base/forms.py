import uuid

from django import forms
from django.core.validators import FileExtensionValidator
from django.utils.translation import gettext_lazy as _

from .models import KnowledgeDocument


class KnowledgeDocumentUploadForm(forms.ModelForm):
    file = forms.FileField(
        label=_("文档文件"),
        validators=[FileExtensionValidator(["txt", "md", "pdf", "docx"])],
        help_text=_("支持 .txt, .md, .pdf, .docx 格式"),
    )

    class Meta:
        model = KnowledgeDocument
        fields = [
            "title", "module", "skill", "version",
            "difficulty", "is_beginner_friendly",
            "scenario_tags", "risk_flags",
        ]
        labels = {
            "title": _("文档标题"),
            "module": _("所属模块"),
            "skill": _("技能"),
            "version": _("版本号"),
            "difficulty": _("难度"),
            "is_beginner_friendly": _("是否新手友好"),
            "scenario_tags": _("场景标签"),
            "risk_flags": _("风险标记"),
        }
        help_texts = {
            "scenario_tags": _("JSON 数组，例如: [\"校园\", \"家庭\"]"),
            "risk_flags": _("JSON 数组，例如: [\"self_harm\", \"suicide\"]"),
        }
        widgets = {
            "scenario_tags": forms.TextInput(),
            "risk_flags": forms.TextInput(),
        }

    def clean_scenario_tags(self):
        val = self.cleaned_data.get("scenario_tags")
        return self._parse_json_field(val, "scenario_tags")

    def clean_risk_flags(self):
        val = self.cleaned_data.get("risk_flags")
        return self._parse_json_field(val, "risk_flags")

    @staticmethod
    def _parse_json_field(value, field_name):
        """Accept JSON arrays or comma-separated values."""
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                import json
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
            return [s.strip() for s in value.split(",") if s.strip()]
        return []
