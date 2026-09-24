"""Bilingual UI, prompt routing and safety regression tests."""

import gettext
import re
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, SimpleTestCase
from django.template import Context, Template
from django.template.loader import render_to_string
from django.utils import translation

from knowledge_base.rag import prompts
from knowledge_base.rag.prompts_en import _SYSTEMS
from knowledge_base.rag.schemas import (
    RiskAssessment, SkillSelectionResult, TeachingContent,
)
from risk.services import check_keyword_risk, should_assess_risk
from scripts.compile_locale import ROOT, OUTPUT, SOURCE, read_catalog


class BilingualUiTests(SimpleTestCase):
    def test_language_switch_changes_public_home(self):
        client = Client()
        response = client.post(
            "/i18n/setlang/", {"language": "en", "next": "/"}, follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Log in")
        self.assertContains(response, 'lang="en"')
        self.assertNotContains(response, "登录")

    def test_chinese_remains_default(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.LANGUAGE_CODE = "zh-hans"
        with translation.override("zh-hans"):
            html = render_to_string("index.html", request=request)
        self.assertIn("登录", html)

    def test_all_template_trans_tags_have_catalog_entries(self):
        catalog = read_catalog(SOURCE)
        pattern = re.compile(r"\{%\s*trans\s+['\"]([^'\"]+)['\"]")
        missing = set()
        for path in (ROOT / "templates").rglob("*.html"):
            for label in pattern.findall(path.read_text(encoding="utf-8")):
                if label not in catalog:
                    missing.add(label)
        self.assertEqual(missing, set())

    def test_compiled_catalog_loads(self):
        with OUTPUT.open("rb") as compiled:
            catalog = gettext.GNUTranslations(compiled)
        self.assertEqual(catalog.gettext("风险提示"), "Safety notice")

    def test_interpolated_template_copy_is_translated(self):
        snippet = Template(
            '{% load i18n %}{% blocktrans with number=1 %}'
            '第 {{ number }} 次测试{% endblocktrans %}'
        )
        with translation.override("en"):
            self.assertEqual(snippet.render(Context()), "Test attempt 1")

    def test_report_summary_uses_active_language(self):
        from reports.services import _build_summary

        kwargs = dict(completed_session_count=0, total_test_count=0,
                      passed_count=0, overall_pass_rate=None, mood_values=[],
                      skill_count=0, achievement_count=0, risk_count=0, retest_count=0)
        with translation.override("en"):
            self.assertIn("has not started lessons", _build_summary(**kwargs))

    def test_media_errors_and_polling_labels_use_english(self):
        from media_app.concurrency import wait_label_for_status
        from media_app.views import synthesize_speech_view, transcribe_audio_view
        request = RequestFactory().post("/media/tts/", {"text": ""})
        request.user = MagicMock(is_authenticated=True, username="tester")
        with translation.override("en"):
            response = synthesize_speech_view(request)
            self.assertIn("Text cannot be empty", response.content.decode())
            self.assertEqual(translation.gettext(wait_label_for_status("queued")), "Queued, please wait...")
            request = RequestFactory().post("/media/asr/", {})
            request.user = MagicMock(is_authenticated=True)
            response = transcribe_audio_view(request)
            self.assertIn("No audio data was received", response.content.decode())

    def test_auth_labels_follow_language(self):
        from accounts.forms import LoginForm, RegisterForm

        with translation.override("en"):
            self.assertEqual(str(LoginForm().fields["username"].label), "Username")
            self.assertEqual(str(LoginForm().fields["password"].label), "Password")
            self.assertEqual(str(RegisterForm().fields["invite_code"].label), "Invitation code")
            request = RequestFactory().get("/accounts/login/")
            request.user = AnonymousUser()
            request.LANGUAGE_CODE = "en"
            html = render_to_string(
                "accounts/login.html", {"form": LoginForm()}, request=request,
            )
            self.assertIn("Username", html)
            self.assertIn("Password", html)
            self.assertNotIn("用户名", html)

    def test_questionnaire_has_no_empty_gender_choice(self):
        from questionnaire.forms import ProfileForm

        with translation.override("en"):
            form = ProfileForm()
            self.assertFalse(any(not value for value, _ in form.fields["gender"].choices))
            self.assertEqual(form.fields["grade"].choices[0][1], "Select a grade")

    def test_after_lesson_mood_heading_is_english(self):
        request = RequestFactory().get("/mood/post_teaching/example/")
        request.user = AnonymousUser()
        request.LANGUAGE_CODE = "en"
        with translation.override("en"):
            html = render_to_string(
                "mood/post_mood.html",
                {"context_label": "教学后心情记录", "back_url": "/"},
                request=request,
            )
        self.assertIn("After-lesson mood check-in", html)
        self.assertNotIn("教学后心情记录", html)


class EnglishGenerationTests(SimpleTestCase):
    def test_every_llm_flow_has_an_english_system_prompt(self):
        self.assertEqual(len(_SYSTEMS), 9)
        for flow in _SYSTEMS:
            with self.subTest(flow=flow):
                self.assertFalse(re.search(r"[\u3400-\u9fff]", _SYSTEMS[flow]))

    def test_prompt_switch_only_changes_instructions_not_user_data(self):
        with translation.override("en"):
            messages = prompts.build_personal_inquiry_messages(
                profile=None, mood_value=3, mood_note="今天压力大",
            )
        self.assertIn("English", messages[0]["content"])
        self.assertIn("今天压力大", messages[1]["content"])
        with translation.override("zh-hans"):
            chinese = prompts.build_personal_inquiry_messages(profile=None, mood_value=3)
        self.assertIn("学生", chinese[0]["content"])

    def test_english_model_enums_preserve_internal_codes(self):
        skill = SkillSelectionResult(
            selected_module="Mindfulness", selected_skill="Body scan",
            reason="A practical grounding skill", skill_difficulty="beginner",
        )
        self.assertEqual(skill.skill_difficulty, "初级")
        content = TeachingContent(message_type="practice", content="Notice a breath", risk_level="high")
        self.assertEqual((content.message_type, content.risk_level), ("练习", "高"))
        risk = RiskAssessment(risk_level="high", reasoning="Explicit danger", should_stop_session=True)
        self.assertEqual(risk.risk_level, "高")

    def test_english_crisis_language_is_detected(self):
        self.assertTrue(check_keyword_risk("I WANT TO DIE")[0])
        self.assertTrue(should_assess_risk("I hate myself"))
        self.assertFalse(should_assess_risk("This exercise is difficult"))

    def test_skill_identity_survives_language_switch(self):
        from teaching.services import _normalize_skill_name, _default_inquiry_data

        for language in ("en", "zh-hans"):
            with translation.override(language):
                self.assertEqual(_normalize_skill_name("Mindful breathing"), "观察呼吸")
                self.assertEqual(_normalize_skill_name("观察呼吸"), "观察呼吸")
        with translation.override("en"):
            self.assertIn("Before we begin", _default_inquiry_data()["greeting"])

    def test_async_question_task_preserves_selected_language(self):
        from testing.tasks import generate_test_questions_async

        fake_test = MagicMock()
        seen = []
        with patch("testing.models.Test.objects.get", return_value=fake_test), patch(
            "testing.services.generate_and_save_questions",
            side_effect=lambda *_: seen.append(translation.get_language()),
        ):
            generate_test_questions_async.run("sample", "en")
        self.assertEqual(seen, ["en"])
