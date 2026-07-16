"""
Health-check endpoints for monitoring container readiness.
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.urls import path

logger = logging.getLogger("dbt_platform.health")

CELERY_QUEUE_WARNING = 10
CELERY_QUEUE_CRITICAL = 50
MONITORED_QUEUE_THRESHOLDS = {
    "celery": (CELERY_QUEUE_WARNING, CELERY_QUEUE_CRITICAL),
    "questions": (CELERY_QUEUE_WARNING, CELERY_QUEUE_CRITICAL),
    "interactive-images": (CELERY_QUEUE_WARNING, CELERY_QUEUE_CRITICAL),
    "batch-images": (CELERY_QUEUE_WARNING, CELERY_QUEUE_CRITICAL),
    "documents": (CELERY_QUEUE_WARNING, CELERY_QUEUE_CRITICAL),
}


def health_check(request: HttpRequest) -> JsonResponse:
    """Basic liveness check — returns 200 if the process is alive."""
    return JsonResponse({"status": "ok"})


def _check_backends() -> dict[str, str]:
    """Probe core backends and return per-service status."""
    checks: dict[str, str] = {}

    try:
        from django.db import connections

        connections["default"].cursor()
        checks["mongodb"] = "ok"
    except Exception as exc:
        logger.error("MongoDB health check failed: %s", exc)
        checks["mongodb"] = str(exc)

    try:
        import redis

        r = redis.from_url(settings.REDIS_URL)
        r.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.error("Redis health check failed: %s", exc)
        checks["redis"] = str(exc)

    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)
        client.get_collections()
        checks["qdrant"] = "ok"
    except Exception as exc:
        logger.error("Qdrant health check failed: %s", exc)
        checks["qdrant"] = str(exc)

    try:
        from minio import Minio

        client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        client.list_buckets()
        checks["minio"] = "ok"
    except Exception as exc:
        logger.error("MinIO health check failed: %s", exc)
        checks["minio"] = str(exc)

    return checks


def readiness_check(request: HttpRequest) -> JsonResponse:
    """Readiness check — returns 200 if core backends are reachable."""
    checks = _check_backends()
    overall = all(value == "ok" for value in checks.values())
    status = {"status": "ok" if overall else "degraded", "checks": checks}
    return JsonResponse(status, status=200 if overall else 503)


def _celery_metrics() -> dict[str, Any]:
    """Return Celery queue depths, active tasks, and image slot usage."""
    monitor_queues = getattr(settings, "CELERY_MONITOR_QUEUES", ("celery",))
    queues: dict[str, int] = {name: 0 for name in monitor_queues}

    try:
        import redis

        broker = redis.from_url(settings.CELERY_BROKER_URL)
        for name in monitor_queues:
            try:
                queues[name] = int(broker.llen(name))
            except Exception:
                queues[name] = 0
    except Exception as exc:
        logger.warning("Celery queue length check failed: %s", exc)

    metrics: dict[str, Any] = {
        "queues": queues,
        "queue_length": queues.get("celery", 0),
        "active_tasks": 0,
        "image_slots": {"interactive": 0, "batch": 0},
        "image_fail_count": 0,
        "image_slot_saturated_seconds": {"interactive": None, "batch": None},
    }

    try:
        from media_app.concurrency import (
            get_active_slot_count,
            get_image_fail_count,
            track_slot_saturation,
        )

        metrics["image_slots"] = {
            "interactive": get_active_slot_count("interactive"),
            "batch": get_active_slot_count("batch"),
        }
        metrics["image_fail_count"] = get_image_fail_count()
        metrics["image_slot_saturated_seconds"] = {
            "interactive": track_slot_saturation("interactive"),
            "batch": track_slot_saturation("batch"),
        }
    except Exception as exc:
        logger.debug("Image slot metrics skipped: %s", exc)

    try:
        from dbt_platform.celery import app

        inspect = app.control.inspect(timeout=1.0)
        active = inspect.active() or {}
        metrics["active_tasks"] = sum(len(tasks) for tasks in active.values())
    except Exception as exc:
        logger.debug("Celery active task inspect skipped: %s", exc)

    return metrics


def _build_alerts(checks: dict[str, str], celery: dict[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    if settings.DEBUG:
        alerts.append({
            "level": "warning",
            "metric": "django_debug",
            "message": "DJANGO_DEBUG is enabled",
            "value": True,
            "threshold": False,
        })

    degraded = [name for name, value in checks.items() if value != "ok"]
    if degraded:
        alerts.append({
            "level": "critical",
            "metric": "backend_health",
            "message": f"Backends degraded: {', '.join(degraded)}",
            "value": degraded,
            "threshold": "all ok",
        })

    queue_map = celery.get("queues", {})
    for queue_name, depth in queue_map.items():
        warning_at, critical_at = MONITORED_QUEUE_THRESHOLDS.get(
            queue_name, (CELERY_QUEUE_WARNING, CELERY_QUEUE_CRITICAL)
        )
        metric_id = f"celery_queue_{queue_name.replace('-', '_')}"
        if depth >= critical_at:
            alerts.append({
                "level": "critical",
                "metric": metric_id,
                "message": f"Celery queue '{queue_name}' backlog is critical",
                "value": depth,
                "threshold": critical_at,
            })
        elif depth >= warning_at:
            alerts.append({
                "level": "warning",
                "metric": metric_id,
                "message": f"Celery queue '{queue_name}' backlog is elevated",
                "value": depth,
                "threshold": warning_at,
            })

    legacy_images = queue_map.get("images", 0)
    if legacy_images > 0:
        alerts.append({
            "level": "warning",
            "metric": "celery_queue_images_legacy",
            "message": "Legacy 'images' queue still draining — migrate workers to interactive-images",
            "value": legacy_images,
            "threshold": 0,
        })

    interactive_saturated = celery.get("image_slot_saturated_seconds", {}).get("interactive")
    interactive_depth = queue_map.get("interactive-images", 0)
    interactive_slots = celery.get("image_slots", {}).get("interactive", 0)
    interactive_max = int(getattr(settings, "IMAGE_INTERACTIVE_MAX_CONCURRENT", 3))
    if (
        interactive_slots >= interactive_max
        and interactive_depth > 0
        and interactive_saturated is not None
        and interactive_saturated >= 60
    ):
        alerts.append({
            "level": "warning",
            "metric": "image_interactive_saturated",
            "message": "Interactive image slots saturated with backlog for >= 60s",
            "value": {
                "slots": interactive_slots,
                "queue_depth": interactive_depth,
                "saturated_seconds": round(interactive_saturated, 1),
            },
            "threshold": 60,
        })

    fail_count = int(celery.get("image_fail_count", 0) or 0)
    if fail_count >= 20:
        alerts.append({
            "level": "warning",
            "metric": "image_fail_count",
            "message": "Elevated image generation failures in the last 24h",
            "value": fail_count,
            "threshold": 20,
        })

    # Backward-compatible alert id for default celery queue
    celery_depth = queue_map.get("celery", celery.get("queue_length", 0))
    if celery_depth >= CELERY_QUEUE_CRITICAL:
        alerts.append({
            "level": "critical",
            "metric": "celery_queue_length",
            "message": "Celery queue backlog is critical",
            "value": celery_depth,
            "threshold": CELERY_QUEUE_CRITICAL,
        })
    elif celery_depth >= CELERY_QUEUE_WARNING:
        alerts.append({
            "level": "warning",
            "metric": "celery_queue_length",
            "message": "Celery queue backlog is elevated",
            "value": celery_depth,
            "threshold": CELERY_QUEUE_WARNING,
        })

    return alerts


def metrics_check(request: HttpRequest) -> JsonResponse:
    """Operational metrics for monitoring and alerting."""
    checks = _check_backends()
    celery = _celery_metrics()
    alerts = _build_alerts(checks, celery)

    if any(alert["level"] == "critical" for alert in alerts):
        status = "critical"
        http_status = 503
    elif alerts:
        status = "warning"
        http_status = 200
    else:
        status = "ok"
        http_status = 200

    payload = {
        "status": status,
        "application": {
            "debug": settings.DEBUG,
            "allowed_hosts": settings.ALLOWED_HOSTS,
        },
        "checks": checks,
        "celery": celery,
        "alerts": alerts,
        "thresholds": {
            "celery_queue_length_warning": CELERY_QUEUE_WARNING,
            "celery_queue_length_critical": CELERY_QUEUE_CRITICAL,
        },
    }
    return JsonResponse(payload, status=http_status)


urlpatterns = [
    path("", health_check, name="health-check"),
    path("ready/", readiness_check, name="readiness-check"),
    path("metrics/", metrics_check, name="metrics-check"),
]
