import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


def create_admin():
    return User.objects.create_user(
        username="exp_admin", password="testpass123", role="admin"
    )


def create_student(username="exp_student"):
    return User.objects.create_user(
        username=username, password="testpass123", role="student"
    )


def create_report_viewer(username="exp_viewer"):
    return User.objects.create_user(
        username=username, password="testpass123", role="report_viewer"
    )


class ExportPageTests(TestCase):
    def test_export_page_requires_login(self):
        url = reverse("export_app:export_page")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_student_denied(self):
        student = create_student()
        self.client.force_login(student)
        url = reverse("export_app:export_page")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_access(self):
        admin = create_admin()
        self.client.force_login(admin)
        url = reverse("export_app:export_page")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_export_page_lists_students(self):
        admin = create_admin()
        create_student("s1")
        create_student("s2")
        self.client.force_login(admin)
        url = reverse("export_app:export_page")
        resp = self.client.get(url)
        self.assertContains(resp, "s1")
        self.assertContains(resp, "s2")


class ExportJSONViewTests(TestCase):
    def setUp(self):
        self.admin = create_admin()
        self.student = create_student("json_student")

    def test_unauth_redirect(self):
        url = reverse("export_app:export_user_json", args=[self.student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_admin_can_export_json(self):
        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_json", args=[self.student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/json; charset=utf-8")

    def test_json_contains_user_data(self):
        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_json", args=[self.student.id])
        resp = self.client.get(url)
        data = json.loads(resp.content)
        self.assertIn("user", data)
        self.assertEqual(data["user"]["username"], "json_student")
        self.assertIn("profile", data)
        self.assertIn("teaching_sessions", data)
        self.assertIn("tests", data)
        self.assertIn("mood_records", data)
        self.assertIn("risk_events", data)
        self.assertIn("achievements", data)

    def test_json_includes_session_data(self):
        from teaching.models import TeachingSession

        session = TeachingSession.objects.create(
            user=self.student,
            selected_skill="正念呼吸",
            status="completed",
        )
        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_json", args=[self.student.id])
        resp = self.client.get(url)
        data = json.loads(resp.content)
        self.assertEqual(len(data["teaching_sessions"]), 1)
        self.assertEqual(data["teaching_sessions"][0]["session_id"], session.session_id)

    def test_json_includes_test_data(self):
        from teaching.models import TeachingSession
        from testing.models import Test

        session = TeachingSession.objects.create(user=self.student)
        Test.objects.create(
            user=self.student,
            session=session,
            total_questions=5,
            correct_count=4,
            passed=True,
        )
        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_json", args=[self.student.id])
        resp = self.client.get(url)
        data = json.loads(resp.content)
        self.assertEqual(len(data["tests"]), 1)

    def test_json_nonexistent_user_404(self):
        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_json", args=["nonexistent-id"])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)


class ExportCSVViewTests(TestCase):
    def setUp(self):
        self.admin = create_admin()
        self.student = create_student("csv_student")

    def test_admin_can_export_csv(self):
        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_csv", args=[self.student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])

    def test_csv_contains_sections(self):
        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_csv", args=[self.student.id])
        resp = self.client.get(url)
        content = resp.content.decode("utf-8-sig")
        self.assertIn("=== 用户信息 ===", content)
        self.assertIn("=== 教学会话 ===", content)
        self.assertIn("=== 测试记录 ===", content)
        self.assertIn("=== 情绪记录 ===", content)
        self.assertIn("风险事件", content)
        self.assertIn("=== 成就 ===", content)

    def test_csv_contains_username(self):
        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_csv", args=[self.student.id])
        resp = self.client.get(url)
        content = resp.content.decode("utf-8-sig")
        self.assertIn("csv_student", content)

    def test_csv_student_denied(self):
        other = create_student("other_student")
        self.client.force_login(other)
        url = reverse("export_app:export_user_csv", args=[self.student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)


class ExportBulkViewsTests(TestCase):
    def setUp(self):
        self.admin = create_admin()
        self.student1 = create_student("bulk_s1")
        self.student2 = create_student("bulk_s2")

    def test_bulk_json_export(self):
        self.client.force_login(self.admin)
        url = reverse("export_app:export_users_json")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn(self.student1.id, data)
        self.assertIn(self.student2.id, data)

    def test_bulk_json_with_specific_ids(self):
        self.client.force_login(self.admin)
        url = (
            reverse("export_app:export_users_json")
            + "?user_ids="
            + self.student1.id
        )
        resp = self.client.get(url)
        data = json.loads(resp.content)
        self.assertIn(self.student1.id, data)
        self.assertNotIn(self.student2.id, data)

    def test_bulk_csv_export(self):
        self.client.force_login(self.admin)
        url = reverse("export_app:export_users_csv")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8-sig")
        self.assertIn("bulk_s1", content)
        self.assertIn("bulk_s2", content)


class AdminOperationLogTests(TestCase):
    def setUp(self):
        self.admin = create_admin()
        self.student = create_student("audit_student")

    def test_json_export_creates_log(self):
        from export_app.models import AdminOperationLog

        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_json", args=[self.student.id])
        self.client.get(url)
        logs = AdminOperationLog.objects.filter(
            admin=self.admin, operation_type="export_data", export_format="json"
        )
        self.assertTrue(logs.exists())

    def test_csv_export_creates_log(self):
        from export_app.models import AdminOperationLog

        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_csv", args=[self.student.id])
        self.client.get(url)
        logs = AdminOperationLog.objects.filter(
            admin=self.admin, operation_type="export_data", export_format="csv"
        )
        self.assertTrue(logs.exists())

    def test_bulk_json_export_creates_log(self):
        from export_app.models import AdminOperationLog

        self.client.force_login(self.admin)
        url = reverse("export_app:export_users_json")
        self.client.get(url)
        logs = AdminOperationLog.objects.filter(
            admin=self.admin,
            operation_type="export_data",
            export_format="json",
        )
        self.assertTrue(logs.exists())

    def test_log_captures_target_info(self):
        from export_app.models import AdminOperationLog

        self.client.force_login(self.admin)
        url = reverse("export_app:export_user_json", args=[self.student.id])
        self.client.get(url)
        log = AdminOperationLog.objects.filter(
            admin=self.admin, export_format="json"
        ).first()
        self.assertEqual(log.target_type, "user")
        self.assertEqual(log.target_id, self.student.id)
        self.assertEqual(log.operation_type, "export_data")

    def test_bulk_log_captures_scope(self):
        from export_app.models import AdminOperationLog

        self.client.force_login(self.admin)
        url = reverse("export_app:export_users_json")
        self.client.get(url)
        log = AdminOperationLog.objects.filter(
            admin=self.admin, export_format="json", target_type="users_bulk"
        ).first()
        self.assertIsNotNone(log)
        self.assertIn("user_count", log.export_scope)


class ExportServiceTests(TestCase):
    def test_aggregate_user_data_structure(self):
        student = create_student("svc_student")
        from export_app.services import aggregate_user_data

        data = aggregate_user_data(student)
        self.assertIn("user", data)
        self.assertIn("profile", data)
        self.assertIn("teaching_sessions", data)
        self.assertIn("tests", data)
        self.assertIn("mood_records", data)
        self.assertIn("risk_events", data)
        self.assertIn("achievements", data)

    def test_export_user_json_returns_string(self):
        student = create_student("json_svc_student")
        from export_app.services import export_user_json

        result = export_user_json(student)
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertEqual(parsed["user"]["username"], "json_svc_student")

    def test_export_user_csv_returns_string(self):
        student = create_student("csv_svc_student")
        from export_app.services import export_user_csv

        result = export_user_csv(student)
        self.assertIsInstance(result, str)
        self.assertIn("csv_svc_student", result)

    def test_aggregate_includes_messages_in_sessions(self):
        student = create_student("msg_student")
        from teaching.models import ChatMessage, TeachingSession

        session = TeachingSession.objects.create(user=student)
        ChatMessage.objects.create(
            session=session, user=student, role="user", content="你好"
        )
        from export_app.services import aggregate_user_data

        data = aggregate_user_data(student)
        msgs = data["teaching_sessions"][0]["messages"]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["content"], "你好")

    def test_aggregate_includes_questions_in_tests(self):
        student = create_student("q_student")
        from teaching.models import TeachingSession
        from testing.models import Test, TestQuestion

        session = TeachingSession.objects.create(user=student)
        test = Test.objects.create(user=student, session=session)
        TestQuestion.objects.create(
            test=test,
            question_text="What is DBT?",
            options=["A", "B", "C", "D"],
            correct_option="A",
        )
        from export_app.services import aggregate_user_data

        data = aggregate_user_data(student)
        questions = data["tests"][0]["questions"]
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["question_text"], "What is DBT?")


class ExportReportViewerDeniedTests(TestCase):
    """Report viewers should NOT be able to access admin export functionality."""

    def setUp(self):
        self.viewer = create_report_viewer()
        self.student = create_student("denied_student")

    def test_export_page_denied(self):
        self.client.force_login(self.viewer)
        url = reverse("export_app:export_page")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_export_json_denied(self):
        self.client.force_login(self.viewer)
        url = reverse("export_app:export_user_json", args=[self.student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_export_csv_denied(self):
        self.client.force_login(self.viewer)
        url = reverse("export_app:export_user_csv", args=[self.student.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_bulk_json_denied(self):
        self.client.force_login(self.viewer)
        url = reverse("export_app:export_users_json")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_bulk_csv_denied(self):
        self.client.force_login(self.viewer)
        url = reverse("export_app:export_users_csv")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)
