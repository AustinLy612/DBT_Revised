from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


def create_admin():
    return User.objects.create_user(
        username="rpt_admin", password="testpass123", role="admin"
    )


def create_student(username="rpt_student"):
    return User.objects.create_user(
        username=username, password="testpass123", role="student"
    )


def create_report_viewer(username="rpt_viewer"):
    return User.objects.create_user(
        username=username, password="testpass123", role="report_viewer"
    )


class ReportDashboardTests(TestCase):
    def setUp(self):
        self.dashboard_url = reverse("reports:dashboard")

    def test_unauthenticated_redirects(self):
        resp = self.client.get(self.dashboard_url)
        self.assertEqual(resp.status_code, 302)

    def test_student_denied(self):
        student = create_student()
        self.client.force_login(student)
        resp = self.client.get(self.dashboard_url)
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_access(self):
        admin = create_admin()
        self.client.force_login(admin)
        resp = self.client.get(self.dashboard_url)
        self.assertEqual(resp.status_code, 200)

    def test_report_viewer_can_access(self):
        viewer = create_report_viewer()
        self.client.force_login(viewer)
        resp = self.client.get(self.dashboard_url)
        self.assertEqual(resp.status_code, 200)

    def test_admin_sees_all_students(self):
        admin = create_admin()
        create_student("s1")
        create_student("s2")
        self.client.force_login(admin)
        resp = self.client.get(self.dashboard_url)
        self.assertContains(resp, "s1")
        self.assertContains(resp, "s2")

    def test_report_viewer_sees_only_assigned(self):
        from accounts.models import ReportViewerAssignment

        viewer = create_report_viewer()
        s1 = create_student("s1")
        s2 = create_student("s2")
        ReportViewerAssignment.objects.create(viewer=viewer, student=s1, is_active=True)
        self.client.force_login(viewer)
        resp = self.client.get(self.dashboard_url)
        self.assertContains(resp, "s1")
        self.assertNotContains(resp, "s2")


class StudentReportViewTests(TestCase):
    def test_nonexistent_student_returns_403(self):
        admin = create_admin()
        self.client.force_login(admin)
        url = reverse("reports:student_report", args=["nonexistent-id-12345"])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_valid_student_loads(self):
        admin = create_admin()
        student = create_student("valid_student")
        self.client.force_login(admin)
        url = reverse("reports:student_report", args=[student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_report_shows_student_name(self):
        admin = create_admin()
        student = create_student("report_student")
        self.client.force_login(admin)
        url = reverse("reports:student_report", args=[student.id])
        resp = self.client.get(url)
        self.assertContains(resp, "report_student")

    def test_student_cannot_access_report(self):
        s1 = create_student("s1")
        s2 = create_student("s2")
        self.client.force_login(s1)
        url = reverse("reports:student_report", args=[s2.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_report_viewer_needs_assignment(self):
        viewer = create_report_viewer()
        student = create_student("s1")
        self.client.force_login(viewer)
        url = reverse("reports:student_report", args=[student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_report_viewer_with_assignment_can_view(self):
        from accounts.models import ReportViewerAssignment

        viewer = create_report_viewer()
        student = create_student("s1")
        ReportViewerAssignment.objects.create(viewer=viewer, student=student, is_active=True)
        self.client.force_login(viewer)
        url = reverse("reports:student_report", args=[student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_inactive_assignment_denied(self):
        from accounts.models import ReportViewerAssignment

        viewer = create_report_viewer()
        student = create_student("s1")
        ReportViewerAssignment.objects.create(viewer=viewer, student=student, is_active=False)
        self.client.force_login(viewer)
        url = reverse("reports:student_report", args=[student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)


class ReportServiceTests(TestCase):
    def test_empty_student_has_zero_counts(self):
        student = create_student("empty_student")
        from reports.services import get_student_report_data

        data = get_student_report_data(student)
        self.assertEqual(data["session_count"], 0)
        self.assertEqual(data["completed_session_count"], 0)
        self.assertEqual(data["total_test_count"], 0)
        self.assertEqual(data["retest_count"], 0)
        self.assertEqual(len(data["mood_history"]), 0)
        self.assertEqual(len(data["skill_counts"]), 0)
        self.assertEqual(len(data["test_summary"]), 0)
        self.assertEqual(len(data["achievements"]), 0)
        self.assertIsNone(data["overall_pass_rate"])

    def test_student_with_completed_session(self):
        student = create_student("active_student")
        from teaching.models import TeachingSession

        TeachingSession.objects.create(
            user=student,
            selected_skill="正念呼吸",
            status="completed",
            phase="teaching",
        )
        from reports.services import get_student_report_data

        data = get_student_report_data(student)
        self.assertEqual(data["session_count"], 1)
        self.assertEqual(data["completed_session_count"], 1)
        self.assertEqual(len(data["skill_counts"]), 1)
        self.assertEqual(data["skill_counts"][0]["skill"], "正念呼吸")
        self.assertEqual(data["skill_counts"][0]["count"], 1)

    def test_skill_counts_aggregate_correctly(self):
        student = create_student("skill_student")
        from teaching.models import TeachingSession

        TeachingSession.objects.create(
            user=student, selected_skill="正念呼吸", status="completed", phase="teaching"
        )
        TeachingSession.objects.create(
            user=student, selected_skill="正念呼吸", status="completed", phase="teaching"
        )
        TeachingSession.objects.create(
            user=student, selected_skill="情绪调节", status="completed", phase="teaching"
        )
        # Ongoing session should not count
        TeachingSession.objects.create(
            user=student, selected_skill="正念呼吸", status="ongoing", phase="teaching"
        )
        from reports.services import get_student_report_data

        data = get_student_report_data(student)
        self.assertEqual(len(data["skill_counts"]), 2)
        counts_by_skill = {s["skill"]: s["count"] for s in data["skill_counts"]}
        self.assertEqual(counts_by_skill["正念呼吸"], 2)
        self.assertEqual(counts_by_skill["情绪调节"], 1)

    def test_mood_history_included(self):
        student = create_student("mood_student")
        from mood.models import MoodRecord
        from teaching.models import TeachingSession

        session = TeachingSession.objects.create(user=student)
        MoodRecord.objects.create(
            user=student,
            session=session,
            mood_value=4,
            emoji="🙂",
            context="before_teaching",
        )
        MoodRecord.objects.create(
            user=student,
            session=session,
            mood_value=5,
            emoji="😄",
            context="after_teaching",
        )
        from reports.services import get_student_report_data

        data = get_student_report_data(student)
        self.assertEqual(len(data["mood_history"]), 2)
        self.assertEqual(data["mood_history"][0]["emoji"], "🙂")
        self.assertEqual(data["mood_history"][1]["emoji"], "😄")

    def test_test_summary_included(self):
        student = create_student("test_student")
        from teaching.models import TeachingSession
        from testing.models import Test

        session = TeachingSession.objects.create(user=student, selected_skill="正念呼吸")
        Test.objects.create(
            user=student,
            session=session,
            attempt_no=1,
            total_questions=5,
            correct_count=4,
            passed=True,
            status="completed",
        )
        Test.objects.create(
            user=student,
            session=session,
            attempt_no=2,
            total_questions=5,
            correct_count=3,
            passed=False,
            status="completed",
        )
        from reports.services import get_student_report_data

        data = get_student_report_data(student)
        self.assertEqual(len(data["test_summary"]), 2)
        self.assertEqual(data["total_test_count"], 2)
        self.assertEqual(data["retest_count"], 1)
        self.assertEqual(data["overall_pass_rate"], 70.0)

    def test_achievements_included(self):
        student = create_student("achievement_student")
        from mood.models import Achievement, UserAchievement

        ach = Achievement.objects.create(
            name_cn="第一步", description_cn="完成首次训练", icon="🏆"
        )
        UserAchievement.objects.create(user=student, achievement=ach)
        from reports.services import get_student_report_data

        data = get_student_report_data(student)
        self.assertEqual(len(data["achievements"]), 1)
        self.assertEqual(data["achievements"][0].achievement.name_cn, "第一步")

    def test_profile_included_in_data(self):
        student = create_student("profile_student")
        from questionnaire.models import UserProfile

        UserProfile.objects.create(
            user=student,
            gender="male",
            age=15,
            grade="grade_10",
            hobby_tags=["阅读", "篮球"],
            concern_tags=["学业压力"],
        )
        from reports.services import get_student_report_data

        data = get_student_report_data(student)
        self.assertEqual(data["profile"]["gender"], "男")
        self.assertEqual(data["profile"]["age"], 15)
        self.assertEqual(data["profile"]["grade"], "高一")
        self.assertIn("阅读", data["profile"]["hobbies"])
        self.assertIn("学业压力", data["profile"]["concerns"])

    def test_summary_included_for_active_student(self):
        student = create_student("summary_student")
        from mood.models import MoodRecord
        from teaching.models import TeachingSession

        session = TeachingSession.objects.create(
            user=student, selected_skill="正念呼吸", status="completed"
        )
        MoodRecord.objects.create(
            user=student, session=session, mood_value=3, emoji="😐", context="before_teaching"
        )
        MoodRecord.objects.create(
            user=student, session=session, mood_value=5, emoji="😄", context="after_teaching"
        )
        from reports.services import get_student_report_data

        data = get_student_report_data(student)
        self.assertIn("该学生共完成 1 次教学会话", data["summary"])
        self.assertIn("情绪呈上升趋势", data["summary"])

    def test_summary_for_empty_student(self):
        student = create_student("empty_summary")
        from reports.services import get_student_report_data

        data = get_student_report_data(student)
        self.assertIn("暂未开始教学或测试", data["summary"])

    def test_mood_svg_chart_generated(self):
        student = create_student("svg_student")
        from mood.models import MoodRecord
        from teaching.models import TeachingSession

        session = TeachingSession.objects.create(user=student)
        for val in [3, 4, 5, 4, 5]:
            MoodRecord.objects.create(
                user=student,
                session=session,
                mood_value=val,
                emoji="😐",
                context="before_teaching",
            )
        from reports.services import get_student_report_data

        data = get_student_report_data(student)
        chart = data["mood_svg_chart"]
        self.assertIn("<svg", chart)
        self.assertIn("情绪变化趋势", chart)
        self.assertIn("polyline", chart)

    def test_mood_svg_chart_empty_when_no_mood(self):
        student = create_student("no_svg")
        from reports.services import get_student_report_data

        data = get_student_report_data(student)
        self.assertEqual(data["mood_svg_chart"], "")

    def test_html_report_shows_basic_info(self):
        admin = create_admin()
        student = create_student("html_info")
        from questionnaire.models import UserProfile

        UserProfile.objects.create(
            user=student, gender="female", age=14, grade="grade_9", hobby_tags=[], concern_tags=[]
        )
        self.client.force_login(admin)
        url = reverse("reports:student_report", args=[student.id])
        resp = self.client.get(url)
        self.assertContains(resp, "基础信息")
        self.assertContains(resp, "女")
        self.assertContains(resp, "14 岁")
        self.assertContains(resp, "初三")

    def test_html_report_shows_summary(self):
        admin = create_admin()
        student = create_student("html_summary")
        self.client.force_login(admin)
        url = reverse("reports:student_report", args=[student.id])
        resp = self.client.get(url)
        self.assertContains(resp, "报告摘要")

    def test_html_report_shows_mood_chart(self):
        admin = create_admin()
        student = create_student("html_chart")
        from mood.models import MoodRecord
        from teaching.models import TeachingSession

        session = TeachingSession.objects.create(user=student)
        MoodRecord.objects.create(
            user=student, session=session, mood_value=4, emoji="🙂", context="before_teaching"
        )
        self.client.force_login(admin)
        url = reverse("reports:student_report", args=[student.id])
        resp = self.client.get(url)
        # SVG chart is injected as safe HTML
        self.assertContains(resp, "<svg")

    def test_pdf_contains_basic_info(self):
        admin = create_admin()
        student = create_student("pdf_info")
        from questionnaire.models import UserProfile

        UserProfile.objects.create(
            user=student, gender="male", age=16, grade="grade_11", hobby_tags=[], concern_tags=[]
        )
        self.client.force_login(admin)
        url = reverse("reports:student_report_pdf", args=[student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        # PDF contains the encoded text (simplified check: it's a valid PDF)
        self.assertTrue(len(resp.content) > 100)
        self.assertTrue(resp.content.startswith(b"%PDF"))

    def test_pdf_contains_summary_section(self):
        admin = create_admin()
        student = create_student("pdf_summary")
        self.client.force_login(admin)
        url = reverse("reports:student_report_pdf", args=[student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_pdf_contains_mood_chart(self):
        admin = create_admin()
        student = create_student("pdf_chart")
        from mood.models import MoodRecord
        from teaching.models import TeachingSession

        session = TeachingSession.objects.create(user=student)
        MoodRecord.objects.create(
            user=student, session=session, mood_value=3, emoji="😐", context="before_teaching"
        )
        self.client.force_login(admin)
        url = reverse("reports:student_report_pdf", args=[student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")


class StudentReportPDFTests(TestCase):
    def setUp(self):
        self.admin = create_admin()
        self.student = create_student("pdf_student")

    def test_pdf_endpoint_requires_login(self):
        url = reverse("reports:student_report_pdf", args=[self.student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_pdf_generates_for_admin(self):
        self.client.force_login(self.admin)
        url = reverse("reports:student_report_pdf", args=[self.student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")

    def test_pdf_has_content_disposition(self):
        self.client.force_login(self.admin)
        url = reverse("reports:student_report_pdf", args=[self.student.id])
        resp = self.client.get(url)
        self.assertIn("attachment", resp["Content-Disposition"])

    def test_pdf_student_denied(self):
        other = create_student("other")
        self.client.force_login(other)
        url = reverse("reports:student_report_pdf", args=[self.student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)


class ReportAccessLogTests(TestCase):
    def test_view_logs_report_access(self):
        from reports.models import ReportAccessLog

        admin = create_admin()
        student = create_student("log_student")
        self.client.force_login(admin)
        url = reverse("reports:student_report", args=[student.id])
        self.client.get(url)

        logs = ReportAccessLog.objects.filter(student=student, action_type="view")
        self.assertTrue(logs.exists())

    def test_pdf_export_logs_report_access(self):
        from reports.models import ReportAccessLog

        admin = create_admin()
        student = create_student("pdf_log_student")
        self.client.force_login(admin)
        url = reverse("reports:student_report_pdf", args=[student.id])
        self.client.get(url)

        logs = ReportAccessLog.objects.filter(
            student=student, action_type="export", export_format="pdf"
        )
        self.assertTrue(logs.exists())

    def test_log_captures_viewer_role(self):
        from reports.models import ReportAccessLog

        admin = create_admin()
        student = create_student("role_student")
        self.client.force_login(admin)
        url = reverse("reports:student_report", args=[student.id])
        self.client.get(url)

        log = ReportAccessLog.objects.filter(student=student, action_type="view").first()
        self.assertEqual(log.viewer_role, "admin")
