from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import InviteCode, ReportViewerAssignment

User = get_user_model()


# ── Test Helpers ──

def create_invite_code(code="TEST-CODE-001", status="active"):
    """Create a test invite code."""
    from .models import gen_uuid

    return InviteCode.objects.create(
        id=gen_uuid(),
        code=code,
        status=status,
    )


def create_user(username, password="testpass123", role="student", invite_code=""):
    """Create a test user."""
    user = User.objects.create_user(
        username=username,
        password=password,
        role=role,
        invite_code=invite_code,
    )
    return user


# ── Registration Tests ──


class RegistrationTests(TestCase):
    def setUp(self):
        self.invite = create_invite_code("REG-VALID-001", "active")
        self.register_url = reverse("accounts:register")

    def test_registration_page_loads(self):
        resp = self.client.get(self.register_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "注册")

    def test_registration_with_valid_invite_code(self):
        resp = self.client.post(
            self.register_url,
            {
                "username": "newstudent",
                "password": "securepass123",
                "password_confirm": "securepass123",
                "invite_code": "REG-VALID-001",
            },
        )
        self.assertRedirects(resp, reverse("questionnaire:profile"))

        user = User.objects.get(username="newstudent")
        self.assertEqual(user.role, "student")
        self.assertEqual(user.invite_code, "REG-VALID-001")
        self.assertTrue(user.check_password("securepass123"))

        self.invite.refresh_from_db()
        self.assertEqual(self.invite.status, "used")
        self.assertEqual(self.invite.used_by, str(user.id))

    def test_authenticated_user_redirected(self):
        user = create_user("existing")
        self.client.force_login(user)
        resp = self.client.get(self.register_url)
        self.assertRedirects(resp, reverse("index"))

    def test_registration_without_invite_code_fails(self):
        resp = self.client.post(
            self.register_url,
            {
                "username": "newstudent",
                "password": "securepass123",
                "password_confirm": "securepass123",
                "invite_code": "",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="newstudent").exists())

    def test_registration_with_invalid_invite_code_fails(self):
        resp = self.client.post(
            self.register_url,
            {
                "username": "newstudent",
                "password": "securepass123",
                "password_confirm": "securepass123",
                "invite_code": "NONEXISTENT",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="newstudent").exists())

    def test_registration_with_disabled_invite_code_fails(self):
        create_invite_code("REG-DISABLED-001", "disabled")
        resp = self.client.post(
            self.register_url,
            {
                "username": "newstudent",
                "password": "securepass123",
                "password_confirm": "securepass123",
                "invite_code": "REG-DISABLED-001",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="newstudent").exists())

    def test_registration_with_used_invite_code_fails(self):
        used = create_invite_code("REG-USED-001", "used")
        resp = self.client.post(
            self.register_url,
            {
                "username": "newstudent",
                "password": "securepass123",
                "password_confirm": "securepass123",
                "invite_code": "REG-USED-001",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="newstudent").exists())

    def test_registration_password_mismatch_fails(self):
        resp = self.client.post(
            self.register_url,
            {
                "username": "newstudent",
                "password": "securepass123",
                "password_confirm": "differentpass",
                "invite_code": "REG-VALID-001",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="newstudent").exists())

    def test_registration_duplicate_username_fails(self):
        self.client.post(
            self.register_url,
            {
                "username": "newstudent",
                "password": "securepass123",
                "password_confirm": "securepass123",
                "invite_code": "REG-VALID-001",
            },
        )
        # Logout after first registration succeeds
        self.client.logout()
        invite2 = create_invite_code("REG-VALID-002", "active")
        resp = self.client.post(
            self.register_url,
            {
                "username": "newstudent",
                "password": "securepass123",
                "password_confirm": "securepass123",
                "invite_code": "REG-VALID-002",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(User.objects.filter(username="newstudent").count(), 1)

    def test_registration_short_username_fails(self):
        resp = self.client.post(
            self.register_url,
            {
                "username": "ab",
                "password": "securepass123",
                "password_confirm": "securepass123",
                "invite_code": "REG-VALID-001",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="ab").exists())

    def test_registration_short_password_fails(self):
        resp = self.client.post(
            self.register_url,
            {
                "username": "newstudent",
                "password": "short",
                "password_confirm": "short",
                "invite_code": "REG-VALID-001",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username="newstudent").exists())


# ── Login / Logout Tests ──


class LoginLogoutTests(TestCase):
    def setUp(self):
        self.user = create_user("teststudent", "testpass123")
        self.login_url = reverse("accounts:login")
        self.logout_url = reverse("accounts:logout")

    def test_login_page_loads(self):
        resp = self.client.get(self.login_url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "登录")

    def test_login_with_valid_credentials(self):
        resp = self.client.post(
            self.login_url,
            {"username": "teststudent", "password": "testpass123"},
        )
        self.assertRedirects(resp, reverse("index"))
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_with_invalid_credentials(self):
        resp = self.client.post(
            self.login_url,
            {"username": "teststudent", "password": "wrongpassword"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_with_nonexistent_user(self):
        resp = self.client.post(
            self.login_url,
            {"username": "ghost", "password": "somepassword"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_authenticated_user_redirected_from_login(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.login_url)
        self.assertRedirects(resp, reverse("index"))

    def test_logout(self):
        self.client.force_login(self.user)
        resp = self.client.post(self.logout_url)
        self.assertRedirects(resp, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_page_requires_post(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.logout_url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_updates_last_login(self):
        old_last_login = self.user.last_login
        self.client.post(
            self.login_url,
            {"username": "teststudent", "password": "testpass123"},
        )
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_login)
        if old_last_login:
            self.assertGreater(self.user.last_login, old_last_login)

    def test_login_with_next_redirect(self):
        url = self.login_url + "?next=/teaching/"
        resp = self.client.post(
            url,
            {"username": "teststudent", "password": "testpass123"},
        )
        self.assertRedirects(resp, "/teaching/", fetch_redirect_response=False)

    def test_login_rejects_external_next_redirect(self):
        url = self.login_url + "?next=https://evil.com/phishing"
        resp = self.client.post(
            url,
            {"username": "teststudent", "password": "testpass123"},
        )
        self.assertRedirects(resp, reverse("index"))


# ── Role-Based Access Control Tests ──


class RoleBasedAccessTests(TestCase):
    def setUp(self):
        self.student = create_user("student1", "testpass123", "student")
        self.report_viewer = create_user("viewer1", "testpass123", "report_viewer")
        self.admin = create_user("admin1", "testpass123", "admin")
        self.admin_url = "/admin/"

    def test_admin_has_is_staff_true(self):
        self.assertTrue(self.admin.is_staff)
        self.assertTrue(self.admin.is_superuser)

    def test_admin_downgrade_clears_staff_and_superuser(self):
        self.admin.role = "student"
        self.admin.save()
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_staff)
        self.assertFalse(self.admin.is_superuser)
        self.assertEqual(self.admin.role, "student")

    def test_downgraded_admin_blocked_from_admin(self):
        self.admin.role = "student"
        self.admin.save()
        self.client.force_login(self.admin)
        resp = self.client.get("/admin/")
        self.assertRedirects(resp, reverse("index"))

    def test_student_has_is_staff_false(self):
        self.assertFalse(self.student.is_staff)
        self.assertFalse(self.student.is_superuser)

    def test_admin_user_can_access_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.admin_url)
        # Admin must get a real page, not a redirect
        self.assertEqual(resp.status_code, 200)

    def test_student_cannot_access_admin(self):
        self.client.force_login(self.student)
        resp = self.client.get(self.admin_url)
        self.assertRedirects(resp, reverse("index"))

    def test_report_viewer_cannot_access_admin(self):
        self.client.force_login(self.report_viewer)
        resp = self.client.get(self.admin_url)
        self.assertRedirects(resp, reverse("index"))

    def test_unauthenticated_user_redirected_from_admin(self):
        resp = self.client.get(self.admin_url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)


# ── Invite Code Model Tests ──


class InviteCodeModelTests(TestCase):
    def test_create_invite_code(self):
        code = create_invite_code("MODEL-TEST-001", "active")
        self.assertEqual(code.code, "MODEL-TEST-001")
        self.assertEqual(code.status, "active")
        self.assertIsNotNone(code.id)
        self.assertIsNotNone(code.created_at)

    def test_invite_code_unique(self):
        create_invite_code("UNIQUE-CODE", "active")
        from django.db import IntegrityError

        with self.assertRaises(Exception):
            InviteCode.objects.create(code="UNIQUE-CODE")

    def test_invite_code_status_transitions(self):
        code = create_invite_code("TRANSITION-CODE", "active")
        code.status = "disabled"
        code.save()
        code.refresh_from_db()
        self.assertEqual(code.status, "disabled")

        code.status = "used"
        code.used_by = "user-123"
        code.used_at = timezone.now()
        code.save()
        code.refresh_from_db()
        self.assertEqual(code.status, "used")
        self.assertEqual(code.used_by, "user-123")


# ── Report Viewer Assignment Tests ──


class ReportViewerAssignmentTests(TestCase):
    def setUp(self):
        self.viewer = create_user("viewer_user", "testpass123", "report_viewer")
        self.student_a = create_user("student_a", "testpass123", "student")
        self.student_b = create_user("student_b", "testpass123", "student")

    def test_create_assignment(self):
        assignment = ReportViewerAssignment.objects.create(
            viewer=self.viewer,
            student=self.student_a,
        )
        self.assertTrue(assignment.is_active)
        self.assertEqual(assignment.viewer, self.viewer)
        self.assertEqual(assignment.student, self.student_a)

    def test_unique_viewer_student_pair(self):
        ReportViewerAssignment.objects.create(
            viewer=self.viewer,
            student=self.student_a,
        )
        from django.db import IntegrityError

        with self.assertRaises(Exception):
            ReportViewerAssignment.objects.create(
                viewer=self.viewer,
                student=self.student_a,
            )

    def test_deactivate_assignment(self):
        assignment = ReportViewerAssignment.objects.create(
            viewer=self.viewer,
            student=self.student_a,
        )
        assignment.is_active = False
        assignment.save()
        assignment.refresh_from_db()
        self.assertFalse(assignment.is_active)

    def test_viewer_can_have_multiple_students(self):
        ReportViewerAssignment.objects.create(
            viewer=self.viewer,
            student=self.student_a,
        )
        ReportViewerAssignment.objects.create(
            viewer=self.viewer,
            student=self.student_b,
        )
        count = ReportViewerAssignment.objects.filter(
            viewer=self.viewer, is_active=True
        ).count()
        self.assertEqual(count, 2)


# ── Role Decorator Tests ──


class RoleDecoratorTests(TestCase):
    def test_student_required_decorator(self):
        from .decorators import student_required
        from django.http import HttpResponse

        @student_required
        def test_view(request):
            return HttpResponse("ok")

        self.assertIsNotNone(test_view)

    def test_admin_required_decorator(self):
        from .decorators import admin_required
        from django.http import HttpResponse

        @admin_required
        def test_view(request):
            return HttpResponse("ok")

        self.assertIsNotNone(test_view)

    def test_report_viewer_required_decorator(self):
        from .decorators import report_viewer_required
        from django.http import HttpResponse

        @report_viewer_required
        def test_view(request):
            return HttpResponse("ok")

        self.assertIsNotNone(test_view)
