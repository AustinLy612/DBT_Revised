"""Tests for platform-level infrastructure: health checks, readiness, logging."""
import logging
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class HealthCheckTests(TestCase):
    """Test the /health/ liveness endpoint."""

    def test_liveness_returns_200(self):
        resp = self.client.get(reverse("health-check"))
        self.assertEqual(resp.status_code, 200)
        self.assertJSONEqual(resp.content, {"status": "ok"})


class ReadinessCheckDegradedTests(TestCase):
    """Test /health/ready/ returns 503 when backends are unreachable."""

    @patch("django.db.connections")
    def test_mongodb_unavailable_returns_degraded(self, mock_connections):
        from django.db import DatabaseError
        mock_connections.__getitem__.return_value.cursor.side_effect = DatabaseError("Connection refused")

        resp = self.client.get(reverse("readiness-check"))
        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertEqual(data["status"], "degraded")
        self.assertIn("mongodb", data["checks"])
        self.assertNotEqual(data["checks"]["mongodb"], "ok")

    @patch("redis.from_url")
    def test_redis_unavailable_returns_degraded(self, mock_from_url):
        mock_from_url.side_effect = ConnectionError("Redis unreachable")

        resp = self.client.get(reverse("readiness-check"))
        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertEqual(data["status"], "degraded")
        self.assertNotEqual(data["checks"]["redis"], "ok")

    @patch("qdrant_client.QdrantClient")
    def test_qdrant_unavailable_returns_degraded(self, mock_qdrant_cls):
        mock_qdrant_cls.return_value.get_collections.side_effect = ConnectionError("Qdrant unreachable")

        resp = self.client.get(reverse("readiness-check"))
        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertEqual(data["status"], "degraded")
        self.assertNotEqual(data["checks"]["qdrant"], "ok")

    @patch("minio.Minio")
    def test_minio_unavailable_returns_degraded(self, mock_minio_cls):
        mock_minio_cls.return_value.list_buckets.side_effect = ConnectionError("MinIO unreachable")

        resp = self.client.get(reverse("readiness-check"))
        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertEqual(data["status"], "degraded")
        self.assertNotEqual(data["checks"]["minio"], "ok")

    def test_all_backends_up_returns_200(self):
        """When all backends are running, readiness should return 200."""
        resp = self.client.get(reverse("readiness-check"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["checks"]["mongodb"], "ok")
        self.assertEqual(data["checks"]["redis"], "ok")


class ReadinessCheckLoggingTests(TestCase):
    """Test that health check failures are logged."""

    @patch("django.db.connections")
    def test_mongodb_failure_logs_error(self, mock_connections):
        from django.db import DatabaseError
        mock_connections.__getitem__.return_value.cursor.side_effect = DatabaseError("Connection refused")

        health_logger = logging.getLogger("dbt_platform.health")
        with self.assertLogs(health_logger, level="ERROR") as log_cm:
            self.client.get(reverse("readiness-check"))

        self.assertTrue(
            any("MongoDB health check failed" in msg for msg in log_cm.output),
            f"Expected 'MongoDB health check failed' in log output, got: {log_cm.output}"
        )

    @patch("minio.Minio")
    def test_minio_failure_logs_error(self, mock_minio_cls):
        mock_minio_cls.return_value.list_buckets.side_effect = ConnectionError("MinIO unreachable")

        health_logger = logging.getLogger("dbt_platform.health")
        with self.assertLogs(health_logger, level="ERROR") as log_cm:
            self.client.get(reverse("readiness-check"))

        self.assertTrue(
            any("MinIO health check failed" in msg for msg in log_cm.output),
            f"Expected 'MinIO health check failed' in log output, got: {log_cm.output}"
        )


class MetricsCheckTests(TestCase):
    """Test /health/metrics/ operational monitoring endpoint."""

    def test_metrics_returns_payload(self):
        resp = self.client.get(reverse("metrics-check"))
        self.assertIn(resp.status_code, (200, 503))
        data = resp.json()
        self.assertIn(data["status"], ("ok", "warning", "critical"))
        self.assertIn("application", data)
        self.assertIn("checks", data)
        self.assertIn("celery", data)
        self.assertIn("queues", data["celery"])
        self.assertIn("image_slots", data["celery"])
        self.assertIn("alerts", data)
        self.assertIn("thresholds", data)

    @patch("redis.from_url")
    def test_metrics_critical_when_queue_backlog_high(self, mock_from_url):
        mock_redis = mock_from_url.return_value
        mock_redis.ping.return_value = True
        mock_redis.llen.side_effect = lambda name: 60 if name == "celery" else 0

        resp = self.client.get(reverse("metrics-check"))
        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertEqual(data["status"], "critical")
        self.assertTrue(
            any(alert["metric"] == "celery_queue_length" for alert in data["alerts"]),
        )

    @patch("redis.from_url")
    def test_metrics_critical_when_interactive_images_backlog_high(self, mock_from_url):
        mock_redis = mock_from_url.return_value
        mock_redis.ping.return_value = True
        mock_redis.llen.side_effect = lambda name: 55 if name == "interactive-images" else 0

        resp = self.client.get(reverse("metrics-check"))
        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertTrue(
            any(alert["metric"] == "celery_queue_interactive_images" for alert in data["alerts"]),
        )
        self.assertEqual(data["celery"]["queues"].get("interactive-images"), 55)

    @patch("django.db.connections")
    def test_metrics_critical_when_backend_degraded(self, mock_connections):
        from django.db import DatabaseError

        mock_connections.__getitem__.return_value.cursor.side_effect = DatabaseError("down")

        resp = self.client.get(reverse("metrics-check"))
        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertEqual(data["status"], "critical")
        self.assertNotEqual(data["checks"]["mongodb"], "ok")
