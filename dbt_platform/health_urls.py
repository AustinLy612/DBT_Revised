"""
Health-check endpoints for monitoring container readiness.
"""
import logging

from django.http import JsonResponse
from django.urls import path

logger = logging.getLogger("dbt_platform.health")


def health_check(request):
    """Basic liveness check — returns 200 if the process is alive."""
    return JsonResponse({"status": "ok"})


def readiness_check(request):
    """Readiness check — returns 200 if core backends are reachable."""
    status = {"status": "ok", "checks": {}}
    overall = True

    # MongoDB
    try:
        from django.db import connections

        connections["default"].cursor()
        status["checks"]["mongodb"] = "ok"
    except Exception as exc:
        logger.error("MongoDB health check failed: %s", exc)
        status["checks"]["mongodb"] = str(exc)
        overall = False

    # Redis
    try:
        import redis

        from django.conf import settings

        r = redis.from_url(settings.REDIS_URL)
        r.ping()
        status["checks"]["redis"] = "ok"
    except Exception as exc:
        logger.error("Redis health check failed: %s", exc)
        status["checks"]["redis"] = str(exc)
        overall = False

    # Qdrant
    try:
        from qdrant_client import QdrantClient

        from django.conf import settings

        client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)
        client.get_collections()
        status["checks"]["qdrant"] = "ok"
    except Exception as exc:
        logger.error("Qdrant health check failed: %s", exc)
        status["checks"]["qdrant"] = str(exc)
        overall = False

    # MinIO
    try:
        from minio import Minio

        from django.conf import settings

        client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        client.list_buckets()
        status["checks"]["minio"] = "ok"
    except Exception as exc:
        logger.error("MinIO health check failed: %s", exc)
        status["checks"]["minio"] = str(exc)
        overall = False

    if not overall:
        status["status"] = "degraded"
    return JsonResponse(status, status=200 if overall else 503)


urlpatterns = [
    path("", health_check, name="health-check"),
    path("ready/", readiness_check, name="readiness-check"),
]
