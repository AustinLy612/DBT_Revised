"""Cross-process guards for expensive teaching operations."""

from __future__ import annotations

import logging
import threading
import uuid
from contextlib import contextmanager
from typing import Iterator

import redis
from django.conf import settings

logger = logging.getLogger("dbt_platform.teaching")

_local_locks: dict[str, threading.Lock] = {}
_local_locks_guard = threading.Lock()
_redis_client: redis.Redis | None | bool = None

_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


def _get_local_lock(key: str) -> threading.Lock:
    with _local_locks_guard:
        return _local_locks.setdefault(key, threading.Lock())


def _get_redis_client() -> redis.Redis | None:
    global _redis_client
    if _redis_client is None:
        try:
            client = redis.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            client.ping()
            _redis_client = client
        except Exception:
            logger.warning(
                "Redis unavailable; teaching operation locks are process-local only",
                exc_info=True,
            )
            _redis_client = False
    return _redis_client if _redis_client is not False else None


@contextmanager
def session_operation_lock(
    session_id: str,
    operation: str,
    *,
    ttl_seconds: int | None = None,
) -> Iterator[bool]:
    """Try to acquire a non-blocking session/operation lock.

    The local lock prevents duplicate work inside one Gunicorn worker. Redis
    extends that guarantee across all workers. If Redis is unavailable, the
    guard degrades to process-local protection instead of failing the request.
    """

    key = f"teaching:operation:{session_id}:{operation}"
    local_lock = _get_local_lock(key)
    local_acquired = local_lock.acquire(blocking=False)
    if not local_acquired:
        yield False
        return

    client = _get_redis_client()
    token = uuid.uuid4().hex
    redis_acquired = False
    try:
        if client is not None:
            ttl = ttl_seconds or getattr(
                settings, "TEACHING_OPERATION_LOCK_TTL_SECONDS", 600
            )
            try:
                redis_acquired = bool(client.set(key, token, nx=True, ex=ttl))
            except Exception:
                logger.warning(
                    "Redis lock acquisition failed for %s; using local lock",
                    key,
                    exc_info=True,
                )
                client = None
                redis_acquired = False
            if not redis_acquired:
                if client is not None:
                    yield False
                    return

        yield True
    finally:
        if client is not None and redis_acquired:
            try:
                client.eval(_RELEASE_SCRIPT, 1, key, token)
            except Exception:
                logger.warning(
                    "Failed to release teaching operation lock %s",
                    key,
                    exc_info=True,
                )
        local_lock.release()
