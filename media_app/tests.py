"""Tests for media_app — image generation, TTS, and ASR integration.

Test strategy:
- Service layer: mock requests to avoid real API calls
- View layer: mock services to test endpoint behavior
- Model layer: test metadata logging CRUD
- Admin: test admin page accessibility
"""

from __future__ import annotations

import io
import json
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import services
from .models import AudioSynthesisLog, AudioTranscriptionLog, ImageGenerationLog

User = get_user_model()

# ── Test helpers ──


def create_student(username="testuser", **kwargs):
    """Create a student user with profile completed."""
    user = User.objects.create_user(
        username=username,
        password="testpass123",
        role=User.Role.STUDENT,
        **kwargs,
    )
    user.profile_completed = True
    user.save(update_fields=["profile_completed"])
    return user


def create_admin(username="adminuser"):
    """Create an admin user."""
    user = User.objects.create_user(
        username=username,
        password="adminpass123",
        role=User.Role.ADMIN,
    )
    return user


# ═══════════════════════════════════════════════════════════════
# Model Tests
# ═══════════════════════════════════════════════════════════════


class ImageGenerationLogModelTests(TestCase):
    def test_create_success_log(self):
        user = create_student()
        log = ImageGenerationLog.objects.create(
            user=user,
            prompt="一只可爱的猫",
            model="image-01",
            temporary_image_url="https://example.com/img.png",
            status=ImageGenerationLog.Status.SUCCESS,
            source="manual",
        )
        self.assertEqual(log.status, "success")
        self.assertEqual(log.model, "image-01")
        self.assertIn("猫", log.prompt)
        self.assertIsNotNone(log.created_at)

    def test_create_failed_log(self):
        user = create_student()
        log = ImageGenerationLog.objects.create(
            user=user,
            prompt="test",
            status=ImageGenerationLog.Status.FAILED,
            error_message="API timeout",
            source="teaching_scene",
        )
        self.assertEqual(log.status, "failed")
        self.assertIn("timeout", log.error_message)
        self.assertEqual(log.source, "teaching_scene")

    def test_log_with_session(self):
        from teaching.models import TeachingSession
        user = create_student()
        session = TeachingSession.objects.create(user=user)
        log = ImageGenerationLog.objects.create(
            user=user, prompt="test", session=session
        )
        self.assertEqual(log.session, session)

    def test_log_with_test_question(self):
        from teaching.models import TeachingSession
        from testing.models import Test, TestQuestion
        user = create_student()
        session = TeachingSession.objects.create(user=user)
        test = Test.objects.create(session=session, user=user)
        question = TestQuestion.objects.create(test=test, question_text="Q?")
        log = ImageGenerationLog.objects.create(
            user=user, prompt="test", test_question=question
        )
        self.assertEqual(log.test_question, question)


class AudioSynthesisLogModelTests(TestCase):
    def test_create_success_log(self):
        user = create_student()
        log = AudioSynthesisLog.objects.create(
            user=user,
            text="你好，欢迎学习DBT技能。",
            model="volcengine-tts",
            voice="default",
            temporary_audio_url="https://example.com/audio.mp3",
            status=AudioSynthesisLog.Status.SUCCESS,
        )
        self.assertEqual(log.status, "success")
        self.assertIn("DBT", log.text)

    def test_create_failed_log(self):
        user = create_student()
        log = AudioSynthesisLog.objects.create(
            user=user,
            text="test",
            status=AudioSynthesisLog.Status.FAILED,
            error_message="Synthesis failed",
        )
        self.assertEqual(log.status, "failed")


class AudioTranscriptionLogModelTests(TestCase):
    def test_create_success_log(self):
        user = create_student()
        log = AudioTranscriptionLog.objects.create(
            user=user,
            transcribed_text="我感觉很焦虑",
            model="speech-01",
            audio_duration_ms=3500,
            status=AudioTranscriptionLog.Status.SUCCESS,
        )
        self.assertEqual(log.status, "success")
        self.assertEqual(log.audio_duration_ms, 3500)
        self.assertIn("焦虑", log.transcribed_text)

    def test_create_failed_log(self):
        user = create_student()
        log = AudioTranscriptionLog.objects.create(
            user=user,
            status=AudioTranscriptionLog.Status.FAILED,
            error_message="Audio format not supported",
            audio_duration_ms=0,
        )
        self.assertEqual(log.status, "failed")

    def test_log_with_session(self):
        from teaching.models import TeachingSession
        user = create_student()
        session = TeachingSession.objects.create(user=user)
        log = AudioTranscriptionLog.objects.create(
            user=user, transcribed_text="test", session=session
        )
        self.assertEqual(log.session, session)


# ═══════════════════════════════════════════════════════════════
# Service Layer Tests
# ═══════════════════════════════════════════════════════════════


class ImageGenerationServiceTests(TestCase):
    def setUp(self):
        self.user = create_student()

    @patch("media_app.services.requests.post")
    def test_generate_image_success(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "data": [{"url": "https://cdn.example.com/generated.png"}],
            "usage": {"total_tokens": 50},
        }
        result = services.generate_image("夕阳下的海滩")
        self.assertEqual(len(result["urls"]), 1)
        self.assertIn("cdn.example.com", result["urls"][0])
        self.assertEqual(result["model"], "image-01")

    @patch("media_app.services.requests.post")
    def test_generate_image_api_error(self, mock_post):
        mock_post.return_value.status_code = 400
        mock_post.return_value.json.return_value = {
            "error": {"message": "Invalid prompt"}
        }
        with self.assertRaises(services.APIError):
            services.generate_image("")

    @patch("media_app.services.requests.post")
    def test_generate_image_timeout(self, mock_post):
        import requests as req
        mock_post.side_effect = req.Timeout()
        with self.assertRaises(services.APIError):
            services.generate_image("test prompt")

    def test_generate_image_missing_key(self):
        with patch.object(services, "_get_api_key", side_effect=services.ConfigurationError("no key")):
            with self.assertRaises(services.ConfigurationError):
                services.generate_image("test")


class TTSServiceTests(TestCase):
    def setUp(self):
        self.user = create_student()
        self.default_speaker = "zh_female_xueayi_saturn_bigtts"

    @patch("media_app.services.requests.post")
    def test_synthesize_speech_success(self, mock_post):
        import base64
        fake_audio = base64.b64encode(b"fake_mp3_audio").decode()

        # V3 API: code 0 = audio chunk, code 20000000 = final success
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = [
            json.dumps({"code": 0, "data": fake_audio}),
            json.dumps({"code": 20000000, "message": "OK", "data": None}),
        ]
        mock_post.return_value = mock_resp

        result = services.synthesize_speech("你好世界")
        self.assertEqual(result["audio_bytes"], b"fake_mp3_audio")
        self.assertEqual(result["format"], "mp3")
        self.assertEqual(result["voice"], self.default_speaker)

    @patch("media_app.services.requests.post")
    def test_synthesize_speech_custom_voice(self, mock_post):
        import base64
        fake_audio = base64.b64encode(b"audio_data").decode()

        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = [
            json.dumps({"code": 0, "data": fake_audio}),
            json.dumps({"code": 20000000, "message": "OK", "data": None}),
        ]
        mock_post.return_value = mock_resp

        result = services.synthesize_speech("test", voice="zh_female_vv_uranus_bigtts")
        self.assertEqual(result["voice"], "zh_female_vv_uranus_bigtts")

    @patch("media_app.services.requests.post")
    def test_synthesize_speech_multiple_chunks(self, mock_post):
        import base64
        fake_audio_1 = base64.b64encode(b"chunk1").decode()
        fake_audio_2 = base64.b64encode(b"chunk2").decode()

        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = [
            json.dumps({"code": 0, "data": fake_audio_1}),
            json.dumps({"code": 0, "data": fake_audio_2}),
            json.dumps({"code": 0, "data": None, "sentence": {"text": "test"}}),
            json.dumps({"code": 20000000, "message": "OK", "data": None}),
        ]
        mock_post.return_value = mock_resp

        result = services.synthesize_speech("test")
        self.assertEqual(result["audio_bytes"], b"chunk1chunk2")

    @patch("media_app.services.requests.post")
    def test_synthesize_speech_api_error(self, mock_post):
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.json.return_value = {}
        mock_post.return_value = mock_resp

        with self.assertRaises(services.APIError):
            services.synthesize_speech("test")

    @patch("media_app.services.requests.post")
    def test_synthesize_speech_business_error(self, mock_post):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = [
            json.dumps({"code": 55000000, "message": "resource ID mismatched"}),
        ]
        mock_post.return_value = mock_resp

        with self.assertRaises(services.APIError) as ctx:
            services.synthesize_speech("!!!")
        self.assertIn("resource ID mismatched", str(ctx.exception))

    def test_synthesize_speech_missing_key(self):
        with patch.object(services, "_get_volcengine_api_key", side_effect=services.ConfigurationError("no key")):
            with self.assertRaises(services.ConfigurationError):
                services.synthesize_speech("test")


class ASRServiceTests(TestCase):
    def setUp(self):
        self.user = create_student()

    @patch("media_app.services.requests.post")
    def test_transcribe_audio_success(self, mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "text": "今天天气真好",
            "usage": {},
        }
        result = services.transcribe_audio(b"fake_wav_data", audio_format="wav")
        self.assertEqual(result["transcribed_text"], "今天天气真好")

    @patch("media_app.services.requests.post")
    def test_transcribe_audio_success_alt_format(self, mock_post):
        """Alternative response format: data.text"""
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {
            "data": {"text": "我想学习正念技能"},
        }
        result = services.transcribe_audio(b"fake_mp3_data", audio_format="mp3")
        self.assertIn("正念", result["transcribed_text"])

    @patch("media_app.services.requests.post")
    def test_transcribe_audio_api_error(self, mock_post):
        mock_post.return_value.status_code = 404
        mock_post.return_value.text = "Not Found"
        mock_post.return_value.json.return_value = {
            "error": {"message": "ASR not available"}
        }
        with self.assertRaises(services.APIError) as ctx:
            services.transcribe_audio(b"data")
        self.assertIn("volcengine", str(ctx.exception))

    def test_transcribe_audio_missing_key(self):
        with patch.object(services, "_get_api_key", side_effect=services.ConfigurationError("no key")):
            with self.assertRaises(services.ConfigurationError):
                services.transcribe_audio(b"data")


# ═══════════════════════════════════════════════════════════════
# View Tests
# ═══════════════════════════════════════════════════════════════


class ImageGenerationViewTests(TestCase):
    def setUp(self):
        self.user = create_student()
        self.client.login(username="testuser", password="testpass123")
        self.url = reverse("media:generate_image")

    def test_unauthenticated_redirects(self):
        self.client.logout()
        resp = self.client.post(self.url, {"prompt": "test"})
        self.assertEqual(resp.status_code, 302)

    def test_requires_post(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_empty_prompt_returns_error(self):
        resp = self.client.post(self.url, {"prompt": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("请输入图片描述", resp.content.decode())

    @patch("media_app.views.services.generate_image")
    def test_success_returns_image_html(self, mock_gen):
        mock_gen.return_value = {
            "urls": ["https://cdn.example.com/img.png"],
            "model": "image-01",
            "usage": {},
        }
        resp = self.client.post(self.url, {
            "prompt": "美丽的风景画",
            "model": "image-01",
            "source": "teaching_scene",
        })
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("cdn.example.com", content)
        self.assertIn("image-01", content)
        # Verify ImageGenerationLog created
        self.assertEqual(ImageGenerationLog.objects.count(), 1)
        log = ImageGenerationLog.objects.first()
        self.assertEqual(log.status, "success")
        self.assertEqual(log.source, "teaching_scene")

    @patch("media_app.views.services.generate_image")
    def test_api_error_returns_error_message(self, mock_gen):
        mock_gen.side_effect = services.APIError("Service unavailable")
        resp = self.client.post(self.url, {"prompt": "test"})
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("图像生成失败", content)
        # Verify failure log created
        log = ImageGenerationLog.objects.first()
        self.assertEqual(log.status, "failed")

    @patch("media_app.views.services.generate_image")
    def test_no_url_returns_error(self, mock_gen):
        mock_gen.return_value = {"urls": [], "model": "image-01", "usage": {}}
        resp = self.client.post(self.url, {"prompt": "test"})
        self.assertIn("未返回图片", resp.content.decode())

    @patch("media_app.views.services.generate_image")
    def test_config_error_returns_message(self, mock_gen):
        mock_gen.side_effect = services.ConfigurationError("no key")
        resp = self.client.post(self.url, {"prompt": "test"})
        self.assertIn("未配置", resp.content.decode())


class TTSViewTests(TestCase):
    def setUp(self):
        self.user = create_student()
        self.client.login(username="testuser", password="testpass123")
        self.url = reverse("media:synthesize_speech")

    def test_unauthenticated_redirects(self):
        self.client.logout()
        resp = self.client.post(self.url, {"text": "test"})
        self.assertEqual(resp.status_code, 302)

    def test_requires_post(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_empty_text_returns_error(self):
        resp = self.client.post(self.url, {"text": ""})
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("文本不能为空", data.get("error", ""))

    @patch("media_app.views.services.synthesize_speech")
    def test_success_returns_audio(self, mock_tts):
        mock_tts.return_value = {
            "audio_bytes": b"fake_mp3_data",
            "audio_url": "",
            "format": "mp3",
        }
        resp = self.client.post(self.url, {"text": "你好世界"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "audio/mpeg")
        self.assertEqual(resp.content, b"fake_mp3_data")
        # Verify log created
        self.assertEqual(AudioSynthesisLog.objects.count(), 1)

    @patch("media_app.views.services.synthesize_speech")
    def test_success_returns_url_json(self, mock_tts):
        mock_tts.return_value = {
            "audio_bytes": None,
            "audio_url": "https://cdn.example.com/audio.mp3",
            "format": "mp3",
        }
        resp = self.client.post(self.url, {"text": "hello"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("cdn.example.com", data.get("audio_url", ""))

    @patch("media_app.views.services.synthesize_speech")
    def test_api_error_returns_error_json(self, mock_tts):
        mock_tts.side_effect = services.APIError("TTS failed")
        resp = self.client.post(self.url, {"text": "test"})
        self.assertEqual(resp.status_code, 502)
        data = resp.json()
        self.assertIn("语音合成失败", data.get("error", ""))

    @patch("media_app.views.services.synthesize_speech")
    def test_config_error_returns_error_json(self, mock_tts):
        mock_tts.side_effect = services.ConfigurationError("no key")
        resp = self.client.post(self.url, {"text": "test"})
        self.assertEqual(resp.status_code, 503)

    @patch("media_app.views.services.synthesize_speech")
    def test_long_text_truncated(self, mock_tts):
        mock_tts.return_value = {"audio_bytes": b"data", "audio_url": "", "format": "mp3"}
        long_text = "测" * 4000
        self.client.post(self.url, {"text": long_text})
        # Text is first truncated to 3000 chars, then trimmed to ≤1000 UTF-8 bytes
        # "测" is 3 bytes in UTF-8, so 1000/3 ≈ 333 chars
        called_text = mock_tts.call_args[0][0]
        self.assertEqual(len(called_text), 333)


class ASRViewTests(TestCase):
    def setUp(self):
        self.user = create_student()
        self.client.login(username="testuser", password="testpass123")
        self.url = reverse("media:transcribe_audio")

    def test_unauthenticated_redirects(self):
        self.client.logout()
        resp = self.client.post(self.url, {})
        self.assertEqual(resp.status_code, 302)

    def test_requires_post(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_no_audio_returns_error(self):
        resp = self.client.post(self.url, {})
        data = resp.json()
        self.assertEqual(data["success"], False)
        self.assertIn("未收到音频", data.get("error", ""))

    @patch("media_app.views.services.transcribe_audio")
    def test_success_returns_transcription(self, mock_asr):
        mock_asr.return_value = {
            "transcribed_text": "我想学习正念呼吸",
            "model": "speech-01",
            "usage": {},
        }
        with open("/tmp/test_audio.webm", "wb") as f:
            f.write(b"fake_webm_audio_data")

        with open("/tmp/test_audio.webm", "rb") as f:
            resp = self.client.post(self.url, {
                "audio": f,
            })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertIn("正念呼吸", data["text"])
        # Verify log created
        self.assertEqual(AudioTranscriptionLog.objects.count(), 1)

    @patch("media_app.views.services.transcribe_audio")
    def test_api_error_returns_error_json(self, mock_asr):
        mock_asr.side_effect = services.APIError("ASR unavailable")
        with open("/tmp/test_audio2.webm", "wb") as f:
            f.write(b"test")

        with open("/tmp/test_audio2.webm", "rb") as f:
            resp = self.client.post(self.url, {"audio": f})
        data = resp.json()
        self.assertEqual(data["success"], False)
        self.assertIn("语音识别失败", data.get("error", ""))


# ═══════════════════════════════════════════════════════════════
# Admin Tests
# ═══════════════════════════════════════════════════════════════


class MediaAdminTests(TestCase):
    def setUp(self):
        self.admin = create_admin()
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        self.user = create_student()
        # Create some log entries
        ImageGenerationLog.objects.create(user=self.user, prompt="test prompt")
        AudioSynthesisLog.objects.create(user=self.user, text="test text")
        AudioTranscriptionLog.objects.create(user=self.user, transcribed_text="test transcription")

    def test_admin_can_access_image_log_list(self):
        self.client.login(username="adminuser", password="adminpass123")
        url = reverse("admin:media_app_imagegenerationlog_changelist")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_access_image_log_detail(self):
        self.client.login(username="adminuser", password="adminpass123")
        log = ImageGenerationLog.objects.first()
        url = reverse("admin:media_app_imagegenerationlog_change", args=[log.image_id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_access_tts_log_list(self):
        self.client.login(username="adminuser", password="adminpass123")
        url = reverse("admin:media_app_audiosynthesislog_changelist")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_access_asr_log_list(self):
        self.client.login(username="adminuser", password="adminpass123")
        url = reverse("admin:media_app_audiotranscriptionlog_changelist")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_student_blocked_from_admin(self):
        self.client.login(username="testuser", password="testpass123")
        url = reverse("admin:media_app_imagegenerationlog_changelist")
        resp = self.client.get(url)
        self.assertNotEqual(resp.status_code, 200)

    def test_admin_logs_are_readonly(self):
        """Verify admin cannot add/change/delete media logs."""
        self.client.login(username="adminuser", password="adminpass123")
        # Add should redirect
        add_url = reverse("admin:media_app_imagegenerationlog_add")
        resp = self.client.get(add_url)
        self.assertNotEqual(resp.status_code, 200)


# ═══════════════════════════════════════════════════════════════
# Edge Case Tests
# ═══════════════════════════════════════════════════════════════


class ImageGenerationEdgeCaseTests(TestCase):
    """Edge cases for image generation."""

    def setUp(self):
        self.user = create_student()

    def test_multiple_logs_for_same_user(self):
        for i in range(3):
            ImageGenerationLog.objects.create(
                user=self.user,
                prompt=f"prompt {i}",
                source="teaching_scene",
            )
        count = ImageGenerationLog.objects.filter(user=self.user).count()
        self.assertEqual(count, 3)

    def test_log_ordering_newest_first(self):
        log1 = ImageGenerationLog.objects.create(user=self.user, prompt="older")
        log2 = ImageGenerationLog.objects.create(user=self.user, prompt="newer")
        logs = list(ImageGenerationLog.objects.all())
        self.assertEqual(logs[0], log2)
        self.assertEqual(logs[1], log1)

    def test_status_choices(self):
        for status, label in ImageGenerationLog.Status.choices:
            log = ImageGenerationLog.objects.create(
                user=self.user, prompt="test", status=status
            )
            self.assertEqual(log.status, status)
            self.assertEqual(log.get_status_display(), label)


class TTSLogEdgeCaseTests(TestCase):
    """Edge cases for TTS logging."""

    def setUp(self):
        self.user = create_student()

    def test_tts_log_with_message_link(self):
        from teaching.models import ChatMessage, TeachingSession
        session = TeachingSession.objects.create(user=self.user)
        msg = ChatMessage.objects.create(
            session=session, user=self.user, role="assistant",
            content="这是AI回复"
        )
        log = AudioSynthesisLog.objects.create(
            user=self.user, text="这是AI回复", message=msg
        )
        self.assertEqual(log.message, msg)

    def test_model_choices_preserved(self):
        log = AudioSynthesisLog.objects.create(
            user=self.user,
            text="test",
            model="volcengine-tts",
            voice="BV001_streaming",
        )
        self.assertEqual(log.model, "volcengine-tts")
        self.assertEqual(log.voice, "male_voice_1")


class ASRLogEdgeCaseTests(TestCase):
    """Edge cases for ASR logging."""

    def setUp(self):
        self.user = create_student()

    def test_long_transcription_stored(self):
        long_text = "今天我感到" + "非常" * 100 + "焦虑"
        log = AudioTranscriptionLog.objects.create(
            user=self.user,
            transcribed_text=long_text,
            audio_duration_ms=60000,
        )
        self.assertIn("焦虑", log.transcribed_text)
        self.assertEqual(log.audio_duration_ms, 60000)


# ═══════════════════════════════════════════════════════════════
# API Error Hierarchy Tests
# ═══════════════════════════════════════════════════════════════


class APIErrorHierarchyTests(TestCase):
    def test_configuration_error_is_runtime_error(self):
        self.assertTrue(issubclass(services.ConfigurationError, RuntimeError))

    def test_api_error_is_runtime_error(self):
        self.assertTrue(issubclass(services.APIError, RuntimeError))

    def test_errors_can_be_caught_separately(self):
        try:
            raise services.ConfigurationError("config")
        except services.ConfigurationError:
            pass

        try:
            raise services.APIError("api")
        except services.APIError:
            pass

        # Verify they are different types
        self.assertNotEqual(services.ConfigurationError, services.APIError)


# ═══════════════════════════════════════════════════════════════
# Test Database Isolation Tests
# ═══════════════════════════════════════════════════════════════


class TestDatabaseIsolationTests(TestCase):
    """Verify that Django tests NEVER touch the production database.

    These tests are the guardrails: if they fail, data loss is imminent.
    """

    def test_settings_have_test_name_prefix(self):
        """The configured TEST.NAME must contain 'test_' prefix."""
        import environ
        from django.conf import settings as s
        test_name = s.DATABASES["default"].get("TEST", {}).get("NAME", "")
        self.assertTrue(
            test_name.startswith("test_"),
            f"TEST.NAME='{test_name}' must start with 'test_'",
        )
        # Compare against the env value, not settings.DATABASES["NAME"]
        # (which the MongoDB test creation may have already changed to
        # the test database by the time this test runs).
        env = environ.Env()
        environ.Env.read_env()
        prod_db = env("MONGODB_NAME", default="dbt_platform")
        self.assertNotEqual(
            test_name,
            prod_db,
            f"TEST.NAME='{test_name}' must differ from production DB name '{prod_db}'",
        )

    def test_safety_runner_is_configured(self):
        """Verify SafetyTestRunner is the active test runner."""
        from django.conf import settings as s
        runner = getattr(s, "TEST_RUNNER", "")
        self.assertIn(
            "SafetyTestRunner",
            runner,
            f"TEST_RUNNER='{runner}' must be SafetyTestRunner",
        )

    def test_safety_runner_detects_bad_config(self):
        """SafetyTestRunner must raise TestDatabaseSafetyError for invalid TEST.NAME."""
        from dbt_platform.test_runner import SafetyTestRunner, TestDatabaseSafetyError

        runner = SafetyTestRunner()
        bad_db_settings = {
            "ENGINE": "django_mongodb_backend",
            "NAME": "production_db",
            "TEST": {"NAME": "production_db"},  # missing test_ prefix — DANGEROUS
        }

        with self.assertRaises(TestDatabaseSafetyError):
            runner._enforce_test_name_prefix(
                {"default": bad_db_settings}
            )

    def test_safety_runner_accepts_good_config(self):
        """SafetyTestRunner must NOT raise for a valid TEST.NAME."""
        from dbt_platform.test_runner import SafetyTestRunner

        runner = SafetyTestRunner()
        good_db_settings = {
            "ENGINE": "django_mongodb_backend",
            "NAME": "dbt_platform",
            "TEST": {"NAME": "test_dbt_platform"},
        }

        # Should not raise
        runner._enforce_test_name_prefix(
            {"default": good_db_settings}
        )

    def test_current_test_db_name_is_prefixed(self):
        """When running under Django test discovery, the active connection
        must use a test_* database.  Skip this check when running standalone
        (e.g. via python -c with TextTestRunner) because the test runner's
        database setup hasn't been invoked.
        """
        import sys
        from django.db import connections

        # Only check when running via manage.py test (which calls setup_databases)
        is_managed_test = any(
            arg for arg in sys.argv
            if arg.endswith("manage.py") or "test" in arg
        )
        if not is_managed_test:
            # Not running under manage.py test — skip the runtime DB check
            # but the config checks (above) still apply
            return

        db_name = connections["default"].settings_dict["NAME"]
        self.assertTrue(
            db_name.startswith("test_"),
            f"Active DB '{db_name}' must start with 'test_'. WARNING: tests might destroy production data!",
        )


class ImageGenerationRetryTests(TestCase):
    """Tests for generate_image retry logic on transient HTTP errors."""

    def setUp(self):
        self.user = create_student()

    @patch("media_app.services.requests.post")
    def test_retries_on_429_then_succeeds(self, mock_post):
        """429 rate-limit responses trigger retry with backoff, then succeed."""
        rate_limited = Mock()
        rate_limited.status_code = 429
        rate_limited.json.return_value = {"error": {"message": "rate limited"}}

        success = Mock()
        success.status_code = 200
        success.json.return_value = {
            "data": {"image_urls": ["https://cdn.example.com/img.png"]},
            "usage": {},
        }

        mock_post.side_effect = [rate_limited, rate_limited, success]

        result = services.generate_image("test prompt", max_retries=2, retry_base_delay=0.01)
        self.assertEqual(len(result["urls"]), 1)
        self.assertEqual(mock_post.call_count, 3)

    @patch("media_app.services.requests.post")
    def test_no_retry_on_400(self, mock_post):
        """400 (client error) should NOT retry — it's a permanent error."""
        mock_post.return_value.status_code = 400
        mock_post.return_value.json.return_value = {"error": {"message": "bad request"}}

        with self.assertRaises(services.APIError):
            services.generate_image("test prompt", max_retries=2, retry_base_delay=0.01)
        self.assertEqual(mock_post.call_count, 1)

    @patch("media_app.services.requests.post")
    def test_exhausts_retries_then_raises(self, mock_post):
        """When all retries return transient errors, eventually raise APIError."""
        mock_post.return_value.status_code = 503
        mock_post.return_value.json.return_value = {"error": {"message": "service unavailable"}}

        with self.assertRaises(services.APIError) as ctx:
            services.generate_image("test prompt", max_retries=2, retry_base_delay=0.01)
        self.assertIn("503", str(ctx.exception))
        self.assertEqual(mock_post.call_count, 3)  # 1 initial + 2 retries
