"""Read-only rendering checks for the non-admin interface redesign."""

from pathlib import Path
from types import SimpleNamespace

from django.contrib.staticfiles import finders
from django.template.loader import get_template, render_to_string
from django.test import RequestFactory, SimpleTestCase


PAGE_TEMPLATES = (
    "index.html",
    "accounts/login.html",
    "accounts/register.html",
    "accounts/logout_confirm.html",
    "teaching/home.html",
    "teaching/session.html",
    "mood/home.html",
    "mood/record.html",
    "mood/post_mood.html",
    "mood/achievements.html",
    "questionnaire/profile.html",
    "ema_log/ema_form.html",
    "testing/test.html",
    "reports/dashboard.html",
    "reports/student_report.html",
    "export_app/export_page.html",
    "risk/popup.html",
)


class DesignSmokeTests(SimpleTestCase):
    def test_every_non_admin_page_compiles_and_uses_shared_design(self):
        for name in PAGE_TEMPLATES:
            with self.subTest(template=name):
                template = get_template(name)
                self.assertIsNotNone(template)
                source = Path(template.origin.name).read_text(encoding="utf-8")
                self.assertIn('{% extends "base.html" %}', source)
                self.assertIn("{% block page_id %}", source)
        self.assertIsNotNone(finders.find("css/design.css"))

    def test_homepage_is_image_free_and_keeps_auth_links(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"<img", response.content)
        self.assertContains(response, 'class="dbt-home-hero"')
        self.assertNotContains(response, 'class="dbt-hero-panel"')
        self.assertContains(response, 'href="/accounts/login/"')
        self.assertContains(response, 'href="/accounts/register/"')

    def test_learning_tools_are_links_with_distinct_destinations(self):
        request = RequestFactory().get("/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            role="student",
            profile_completed=True,
            username="student",
        )
        html = render_to_string("index.html", request=request)
        self.assertIn('class="dbt-tool-grid"', html)
        for path in ("/mood/", "/mood/achievements/", "/ema-log/"):
            self.assertIn(f'href="{path}"', html)

    def test_personal_inquiry_uses_neutral_readable_text(self):
        source = Path(get_template("teaching/session.html").origin.name).read_text(encoding="utf-8")
        self.assertIn('class="bg-gray-50 border border-gray-200 rounded-lg p-4 mb-5"', source)
        self.assertNotIn("text-purple-900", source)

    def test_shared_theme_switch_is_available_on_every_page(self):
        base = Path(get_template("base.html").origin.name).read_text(encoding="utf-8")
        self.assertIn('class="dbt-theme-toggle"', base)
        self.assertIn("js/theme.js", base)
        self.assertIn("css/design.css' %}?v=20260924c", base)
        theme_script = Path(finders.find("js/theme.js")).read_text(encoding="utf-8")
        self.assertIn('storageKey = "dbt-theme"', theme_script)
        self.assertIn('root.dataset.theme = theme', theme_script)

    def test_public_pages_and_forms_still_render(self):
        for url, form_markers in (
            ("/", ()),
            ("/accounts/login/", (b'name="username"', b'name="password"')),
            ("/accounts/register/", (b'name="username"', b'name="invite_code"')),
            ("/accounts/logout/", ()),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"css/design.css?v=20260924c", response.content)
                for marker in form_markers:
                    self.assertIn(marker, response.content)

    def test_report_viewer_landing_links_to_reports_not_student_questionnaire(self):
        request = RequestFactory().get("/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            role="report_viewer",
            profile_completed=False,
            username="viewer",
        )
        html = render_to_string("index.html", request=request)
        self.assertIn("/reports/", html)
        self.assertNotIn('href="/questionnaire/profile/"', html)
        self.assertNotIn("记录当下感受", html)
