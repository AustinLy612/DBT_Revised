#!/usr/bin/env python3
"""
DBT Platform — 15-user realistic load test.

Simulates PRD usage: 15 students online, mixed workload (not strict sync):
  - Phase A: 15-way concurrent light browsing (health + authenticated pages)
  - Phase B: Peak mixed snapshot — 7 browse / 5 teaching SSE / 2 poll / 1 report
  - Phase C: Image generation burst — 3 concurrent async scene images (Celery)
  - Phase D: 15 simultaneous teaching SSE (barrier sync)
  - Phase E: 5 SSE + 5 TTS + 5 browse (barrier sync)
  - Phase F: 5- and 10-way concurrent scene images (Celery images queue)
  - Phase G: 15 simultaneous test starts → question gen + batch illustration Celery

Run inside web container (recommended):
  python scripts/loadtest_15users.py --base-url https://genaidbt.top
  python scripts/loadtest_15users.py --extended-only   # D–G only

For Gunicorn-only (no nginx), add http://127.0.0.1:8000 to CSRF_TRUSTED_ORIGINS first:
  python scripts/loadtest_15users.py --base-url http://127.0.0.1:8000

Do NOT use http://nginx — it redirects to the public HTTPS host and breaks CSRF origin checks.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import django
import requests

# ── Django bootstrap (must run before ORM imports) ──
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dbt_platform.settings")
django.setup()

from django.contrib.auth import get_user_model, login  # noqa: E402
from django.contrib.sessions.backends.db import SessionStore  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from questionnaire.models import UserProfile  # noqa: E402
from teaching.models import TeachingSession  # noqa: E402
from testing.models import Test, TestQuestion  # noqa: E402

User = get_user_model()

LOADTEST_PASSWORD = "LoadTest15!"
LOADTEST_PREFIX = "loadtest15_"
USER_COUNT = 15
SCENE_IMAGE_CACHE_PREFIX = "dbt:teaching:scene_image:"
SCENE_ACTIVE_JOB_PREFIX = "dbt:teaching:scene_active_job:"
SCENE_IMAGE_PROMPT = "青少年心理教育场景：学生在安静教室里做深呼吸练习，温暖插画风格"
TTS_SAMPLE_TEXT = "你好，今天我们一起来学习正念呼吸，慢慢吸气，再缓缓呼出。"

MOCK_TEACHING_PLAN = {
    "module": "正念",
    "skill": "观察呼吸",
    "plan_steps": [
        {"step_number": 1, "title": "导入", "content": "介绍正念概念", "estimated_minutes": 5},
        {"step_number": 2, "title": "演示", "content": "示范呼吸方法", "estimated_minutes": 10},
        {"step_number": 3, "title": "练习", "content": "带领呼吸练习", "estimated_minutes": 10},
    ],
    "step_contexts": [[], [], []],
    "estimated_total_minutes": 25,
    "prerequisites": [],
    "source_chunk_ids": ["chunk_loadtest"],
}


@dataclass
class RequestResult:
    label: str
    user_id: int
    status_code: int
    elapsed_ms: float
    ok: bool
    error: str = ""


@dataclass
class PhaseReport:
    name: str
    duration_s: float
    total_requests: int
    success_count: int
    error_count: int
    status_codes: dict[str, int] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        lat = sorted(self.latencies_ms)

        def pct(p: int) -> float | None:
            if not lat:
                return None
            idx = max(0, min(len(lat) - 1, int(len(lat) * p / 100)))
            return lat[idx]

        return {
            "name": self.name,
            "duration_s": round(self.duration_s, 2),
            "total_requests": self.total_requests,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "success_rate_pct": round(100 * self.success_count / max(self.total_requests, 1), 2),
            "latency_ms": {
                "min": round(min(lat), 1) if lat else None,
                "p50": round(statistics.median(lat), 1) if lat else None,
                "p95": round(pct(95), 1) if lat else None,
                "p99": round(pct(99), 1) if lat else None,
                "max": round(max(lat), 1) if lat else None,
                "mean": round(statistics.mean(lat), 1) if lat else None,
            },
            "status_codes": self.status_codes,
            "sample_errors": self.errors[:10],
            "notes": self.notes,
            "extra": self.extra,
        }


def _session_key_for_user(username: str) -> str:
    """Create a persisted Django session for load-test HTTP clients."""
    user = User.objects.get(username=username)
    request = RequestFactory().get("/")
    request.META["HTTP_HOST"] = "127.0.0.1"
    request.session = SessionStore()
    login(request, user)
    request.session.save()
    return request.session.session_key


class LoadTestClient:
    """HTTP session with Django login + CSRF handling."""

    def __init__(self, base_url: str, username: str, password: str, verify_ssl: bool = True):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.csrf_token = ""
        self._bootstrap_session(password)

    def _bootstrap_session(self, password: str) -> None:
        """Authenticate via Django session store (avoids CSRF on HTTP when DEBUG=False)."""
        user = User.objects.filter(username=self.username).first()
        if not user or not user.check_password(password):
            raise RuntimeError(f"Invalid credentials for {self.username}")

        session_key = _session_key_for_user(self.username)
        self.session.cookies.set("sessionid", session_key)

        verify_path = "/reports/" if self.username == "admin" else "/teaching/"
        check = self.session.get(f"{self.base_url}{verify_path}", timeout=30)
        if "accounts/login" in check.url:
            raise RuntimeError(f"Login session not established for {self.username}")
        self.csrf_token = self._extract_csrf(check.text) or self.session.cookies.get("csrftoken", "")

    def refresh_csrf(self, path: str = "/teaching/") -> str:
        page = self.session.get(f"{self.base_url}{path}", timeout=30)
        token = self._extract_csrf(page.text) or self.session.cookies.get("csrftoken", "")
        if token:
            self.csrf_token = token
        return self.csrf_token

    @staticmethod
    def _extract_csrf(html: str) -> str:
        marker = 'name="csrfmiddlewaretoken" value="'
        if marker not in html:
            return ""
        start = html.index(marker) + len(marker)
        end = html.index('"', start)
        return html[start:end]

    @staticmethod
    def _page_ok(resp: requests.Response, path: str) -> bool:
        if resp.status_code != 200:
            return False
        if "accounts/login" in resp.url:
            return False
        if path.startswith("/health/"):
            return True
        if path.startswith("/reports"):
            return resp.status_code == 200 and "报告" in resp.text
        return 'name="username"' not in resp.text or "教学" in resp.text or "心情" in resp.text

    def get(self, path: str, label: str, user_id: int, timeout: float = 30) -> RequestResult:
        url = f"{self.base_url}{path}"
        t0 = time.perf_counter()
        try:
            resp = self.session.get(url, timeout=timeout)
            elapsed = (time.perf_counter() - t0) * 1000
            ok = self._page_ok(resp, path)
            return RequestResult(label, user_id, resp.status_code, elapsed, ok)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return RequestResult(label, user_id, 0, elapsed, False, str(exc))

    def _csrf_token(self) -> str:
        return self.session.cookies.get("csrftoken") or self.csrf_token

    def _post_headers(self, referer: str) -> dict[str, str]:
        token = self._csrf_token()
        return {
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRFToken": token,
        }

    def post_sse(self, path: str, data: dict, label: str, user_id: int, timeout: float = 120) -> RequestResult:
        url = f"{self.base_url}{path}"
        session_path = path.rsplit("/stream/", 1)[0] + "/"
        self.refresh_csrf(session_path)
        referer = f"{self.base_url}{session_path}"
        payload = {**data, "csrfmiddlewaretoken": self._csrf_token()}
        t0 = time.perf_counter()
        try:
            resp = self.session.post(
                url,
                data=payload,
                headers=self._post_headers(referer),
                timeout=timeout,
                stream=True,
            )
            chunks = 0
            saw_done = False
            saw_token = False
            err_msg = ""
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                chunks += 1
                body = line[5:].strip()
                if '"type": "token"' in body or '"type":"token"' in body:
                    saw_token = True
                if '"type": "done"' in body or '"type":"done"' in body:
                    saw_done = True
                if '"type": "error"' in body or '"type":"error"' in body:
                    err_msg = body[:200]
            elapsed = (time.perf_counter() - t0) * 1000
            ok = resp.status_code == 200 and saw_done and (saw_token or elapsed > 2000)
            return RequestResult(
                label, user_id, resp.status_code, elapsed, ok,
                "" if ok else (err_msg or "sse_incomplete"),
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return RequestResult(label, user_id, 0, elapsed, False, str(exc))

    def post_scene_image_and_wait(
        self,
        session_id: str,
        prompt: str,
        label: str,
        user_id: int,
        poll_timeout: float = 180,
    ) -> RequestResult:
        """Dispatch async scene image via Celery and poll until image URL appears."""
        dispatch_url = f"{self.base_url}/teaching/session/{session_id}/generate-scene-image/"
        session_path = f"/teaching/session/{session_id}/"
        self.refresh_csrf(session_path)
        referer = f"{self.base_url}{session_path}"
        t0 = time.perf_counter()
        try:
            resp = self.session.post(
                dispatch_url,
                data={"prompt": prompt, "csrfmiddlewaretoken": self._csrf_token()},
                headers=self._post_headers(referer),
                timeout=30,
            )
            if resp.status_code != 200:
                elapsed = (time.perf_counter() - t0) * 1000
                return RequestResult(
                    label, user_id, resp.status_code, elapsed, False,
                    f"dispatch_failed: {resp.text[:120]}",
                )

            job_id = ""
            match = re.search(r'data-job-id="([^"]+)"', resp.text)
            if match:
                job_id = match.group(1)
            if not job_id:
                match = re.search(r"job_id=([a-f0-9-]{36})", resp.text)
                if match:
                    job_id = match.group(1)
            if not job_id:
                elapsed = (time.perf_counter() - t0) * 1000
                return RequestResult(label, user_id, resp.status_code, elapsed, False, "missing_job_id")

            status_url = (
                f"{self.base_url}/teaching/session/{session_id}/scene-image-status/"
                f"?job_id={job_id}"
            )

            deadline = time.perf_counter() + poll_timeout
            while time.perf_counter() < deadline:
                poll = self.session.get(
                    status_url,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                    timeout=30,
                )
                if poll.status_code == 200 and '<img' in poll.text and 'src="' in poll.text:
                    elapsed = (time.perf_counter() - t0) * 1000
                    return RequestResult(label, user_id, poll.status_code, elapsed, True)
                time.sleep(3)

            elapsed = (time.perf_counter() - t0) * 1000
            return RequestResult(label, user_id, 0, elapsed, False, "image_poll_timeout")
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return RequestResult(label, user_id, 0, elapsed, False, str(exc))

    def post_tts(self, text: str, label: str, user_id: int, timeout: float = 60) -> RequestResult:
        """POST /media/tts/synthesize/ — blocks Gunicorn thread until audio returns."""
        url = f"{self.base_url}/media/tts/synthesize/"
        t0 = time.perf_counter()
        try:
            resp = self.session.post(url, data={"text": text}, timeout=timeout)
            elapsed = (time.perf_counter() - t0) * 1000
            ctype = resp.headers.get("Content-Type", "")
            ok = resp.status_code == 200 and (
                ctype.startswith("audio/") or len(resp.content) > 500
            )
            err = "" if ok else (resp.text[:120] if resp.text else f"HTTP {resp.status_code}")
            return RequestResult(label, user_id, resp.status_code, elapsed, ok, err)
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return RequestResult(label, user_id, 0, elapsed, False, str(exc))

    def start_test_and_wait_questions(
        self,
        session_id: str,
        label: str,
        user_id: int,
        poll_timeout: float = 300,
    ) -> RequestResult:
        """POST start test, poll until 5 questions are generated (Celery celery queue)."""
        start_url = f"{self.base_url}/testing/start/{session_id}/"
        self.refresh_csrf("/teaching/")
        referer = f"{self.base_url}/teaching/"
        t0 = time.perf_counter()
        try:
            resp = self.session.post(
                start_url,
                data={"csrfmiddlewaretoken": self._csrf_token()},
                headers=self._post_headers(referer),
                allow_redirects=False,
                timeout=30,
            )
            if resp.status_code not in (302, 303):
                elapsed = (time.perf_counter() - t0) * 1000
                return RequestResult(
                    label, user_id, resp.status_code, elapsed, False,
                    f"start_test_failed: {resp.text[:120]}",
                )

            location = resp.headers.get("Location", "")
            match = re.search(r"/testing/test/([^/]+)/", location)
            if not match:
                elapsed = (time.perf_counter() - t0) * 1000
                return RequestResult(label, user_id, resp.status_code, elapsed, False, "no_test_id")
            test_id = match.group(1)
            poll_url = f"{self.base_url}/testing/test/{test_id}/poll/"
            deadline = time.perf_counter() + poll_timeout
            while time.perf_counter() < deadline:
                poll = self.session.get(poll_url, timeout=30)
                if poll.status_code == 204:
                    elapsed = (time.perf_counter() - t0) * 1000
                    q_count = TestQuestion.objects.filter(test_id=test_id).count()
                    ok = q_count >= 5
                    return RequestResult(
                        label, user_id, poll.status_code, elapsed, ok,
                        "" if ok else f"questions={q_count}",
                    )
                time.sleep(2)

            elapsed = (time.perf_counter() - t0) * 1000
            q_count = TestQuestion.objects.filter(test_id=test_id).count()
            return RequestResult(
                label, user_id, 0, elapsed, False,
                f"question_poll_timeout count={q_count}",
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return RequestResult(label, user_id, 0, elapsed, False, str(exc))


def _redis_client():
    import redis
    from django.conf import settings

    return redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD or None,
    )


def celery_queue_depths() -> dict[str, int]:
    """Sample Redis Celery broker queue lengths."""
    names = (
        "celery", "questions", "documents",
        "interactive-images", "batch-images", "images",
    )
    depths = {name: 0 for name in names}
    try:
        client = _redis_client()
        for name in names:
            depths[name] = int(client.llen(name))
    except Exception:
        pass
    return depths


def wait_for_images_queue(max_wait_s: float = 300, target: int = 2) -> dict[str, int]:
    """Block until interactive image queue depth drops to target or timeout."""
    deadline = time.perf_counter() + max_wait_s
    last = celery_queue_depths()
    while time.perf_counter() < deadline:
        last = celery_queue_depths()
        interactive = last.get("interactive-images", 0) + last.get("images", 0)
        if interactive <= target:
            break
        time.sleep(5)
    return last


def clear_scene_image_cache(users: list[dict]) -> int:
    """Remove cached scene-image URLs so Phase F measures fresh generation."""
    try:
        client = _redis_client()
    except Exception:
        return 0
    cleared = 0
    for meta in users:
        session_id = meta["session_id"]
        # Legacy single-key cache
        if client.delete(f"{SCENE_IMAGE_CACHE_PREFIX}{session_id}"):
            cleared += 1
        client.delete(f"{SCENE_ACTIVE_JOB_PREFIX}{session_id}")
        pattern = f"{SCENE_IMAGE_CACHE_PREFIX}{session_id}:*"
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                cleared += int(client.delete(*keys))
            if cursor == 0:
                break
    return cleared


def mark_sessions_completed(users: list[dict]) -> None:
    """Mark load-test teaching sessions completed so tests can be started."""
    for meta in users:
        session = TeachingSession.objects.get(session_id=meta["session_id"])
        session.status = TeachingSession.Status.COMPLETED
        session.selected_module = session.selected_module or "正念"
        session.selected_skill = session.selected_skill or "观察呼吸"
        if not session.teaching_summary:
            session.teaching_summary = "学习了正念呼吸，掌握了观察呼吸的基本方法。"
        session.save()


def _run_barrier_workers(
    items: list[dict],
    worker_count: int,
    fn: Callable[[LoadTestClient, dict], RequestResult | list[RequestResult]],
    base_url: str,
    verify_ssl: bool,
) -> list[RequestResult]:
    """Run workers synchronized on a threading barrier before hitting the server."""
    barrier = threading.Barrier(worker_count)
    results: list[RequestResult] = []
    lock = threading.Lock()

    def worker(meta: dict) -> None:
        client = LoadTestClient(base_url, meta["username"], LOADTEST_PASSWORD, verify_ssl)
        try:
            barrier.wait(timeout=60)
        except threading.BrokenBarrierError:
            with lock:
                results.append(RequestResult("barrier", meta["user_id"], 0, 0, False, "barrier_broken"))
            return
        out = fn(client, meta)
        with lock:
            if isinstance(out, list):
                results.extend(out)
            else:
                results.append(out)

    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        list(pool.map(worker, items))
    return results


def ensure_admin_password(password: str) -> None:
    """Ensure admin account exists with a known password for report-dashboard load."""
    admin, _ = User.objects.get_or_create(
        username="admin",
        defaults={"role": User.Role.ADMIN, "is_staff": True, "is_superuser": True},
    )
    admin.role = User.Role.ADMIN
    admin.is_staff = True
    admin.is_superuser = True
    admin.set_password(password)
    admin.save()


def assert_loadtest_allowed(base_url: str) -> None:
    """Refuse to run against production unless LOADTEST_ALLOW_RUN=1."""
    if os.environ.get("LOADTEST_ALLOW_RUN", "").strip() == "1":
        return
    blocked = os.environ.get("LOADTEST_BLOCKED_HOSTS", "genaidbt.top,www.genaidbt.top")
    blocked_hosts = [h.strip().lower() for h in blocked.split(",") if h.strip()]
    url_lower = base_url.lower()
    for host in blocked_hosts:
        if host in url_lower:
            print(
                f"ERROR: Load test blocked for production host '{host}' in {base_url}.\n"
                "Set LOADTEST_ALLOW_RUN=1 to override, or use a staging/local base URL.",
                file=sys.stderr,
            )
            sys.exit(1)


def provision_users(count: int = USER_COUNT) -> list[dict[str, Any]]:
    """Create or reuse load-test students with completed profiles + teaching sessions."""
    users_meta: list[dict[str, Any]] = []
    for i in range(1, count + 1):
        username = f"{LOADTEST_PREFIX}{i:02d}"
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "role": User.Role.STUDENT,
                "profile_completed": True,
            },
        )
        if created:
            user.set_password(LOADTEST_PASSWORD)
            user.profile_completed = True
            user.save()
        else:
            user.set_password(LOADTEST_PASSWORD)
            user.profile_completed = True
            user.save(update_fields=["password", "profile_completed"])

        UserProfile.objects.get_or_create(
            user=user,
            defaults={
                "gender": UserProfile.Gender.OTHER,
                "age": 15,
                "grade": "grade_9",
                "hobby_tags": ["阅读"],
                "concern_tags": ["学业压力"],
            },
        )

        session, _ = TeachingSession.objects.get_or_create(
            user=user,
            status=TeachingSession.Status.ONGOING,
            phase=TeachingSession.Phase.TEACHING,
            defaults={
                "selected_module": "正念",
                "selected_skill": "观察呼吸",
                "teaching_plan": MOCK_TEACHING_PLAN,
            },
        )
        session.phase = TeachingSession.Phase.TEACHING
        session.status = TeachingSession.Status.ONGOING
        session.selected_module = "正念"
        session.selected_skill = "观察呼吸"
        session.teaching_plan = MOCK_TEACHING_PLAN
        session.save()

        users_meta.append({
            "username": username,
            "user_id": i,
            "session_id": session.session_id,
        })
    return users_meta


def _aggregate(
    phase_name: str,
    results: list[RequestResult],
    duration: float,
    notes: str = "",
    extra: dict | None = None,
) -> PhaseReport:
    report = PhaseReport(
        name=phase_name,
        duration_s=duration,
        total_requests=len(results),
        success_count=sum(1 for r in results if r.ok),
        error_count=sum(1 for r in results if not r.ok),
        notes=notes,
        extra=extra or {},
    )
    for r in results:
        report.latencies_ms.append(r.elapsed_ms)
        code = str(r.status_code)
        report.status_codes[code] = report.status_codes.get(code, 0) + 1
        if not r.ok and r.error:
            report.errors.append(f"{r.label} u{r.user_id}: {r.error}")
        elif not r.ok:
            report.errors.append(f"{r.label} u{r.user_id}: HTTP {r.status_code}")
    return report


def phase_a_browse(base_url: str, users: list[dict], verify_ssl: bool) -> PhaseReport:
    """15 users × 25 mixed GETs — simulates class online browsing."""
    paths = [
        ("/health/ready/", "health_ready"),
        ("/teaching/", "teaching_home"),
        ("/mood/", "mood_home"),
        ("/mood/achievements/", "achievements"),
        ("/", "index"),
    ]
    results: list[RequestResult] = []
    lock = threading.Lock()

    def worker(meta: dict) -> None:
        client = LoadTestClient(base_url, meta["username"], LOADTEST_PASSWORD, verify_ssl)
        local: list[RequestResult] = []
        for j in range(25):
            path, label = paths[j % len(paths)]
            local.append(client.get(path, label, meta["user_id"]))
            time.sleep(0.05)
        with lock:
            results.extend(local)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=USER_COUNT) as pool:
        list(pool.map(worker, users))
    return _aggregate(
        "Phase A — 15人并发浏览 (15×25 GET)",
        results,
        time.perf_counter() - t0,
        "模拟 15 人同时在线刷页面、看成就、进教学首页",
    )


def phase_b_mixed_peak(base_url: str, users: list[dict], verify_ssl: bool) -> PhaseReport:
    """Peak mixed snapshot: 7 browse + 5 SSE teaching + 2 poll + 1 report viewer path."""
    results: list[RequestResult] = []
    lock = threading.Lock()

    browse_users = users[:7]
    teach_users = users[7:12]
    poll_users = users[12:14]
    report_user = users[14]

    def browse_worker(meta: dict) -> None:
        client = LoadTestClient(base_url, meta["username"], LOADTEST_PASSWORD, verify_ssl)
        local = []
        for _ in range(10):
            local.append(client.get("/teaching/", "browse_teaching", meta["user_id"]))
            local.append(client.get("/mood/achievements/", "browse_achievements", meta["user_id"]))
            time.sleep(0.1)
        with lock:
            results.extend(local)

    def teach_worker(meta: dict) -> None:
        client = LoadTestClient(base_url, meta["username"], LOADTEST_PASSWORD, verify_ssl)
        sid = meta["session_id"]
        r = client.post_sse(
            f"/teaching/session/{sid}/stream/",
            {"message": "我今天感觉有点紧张，想学学怎么放松。"},
            "teaching_sse",
            meta["user_id"],
            timeout=120,
        )
        with lock:
            results.append(r)

    def poll_worker(meta: dict) -> None:
        client = LoadTestClient(base_url, meta["username"], LOADTEST_PASSWORD, verify_ssl)
        local = []
        for _ in range(15):
            local.append(client.get("/health/ready/", "poll_ready", meta["user_id"], timeout=15))
            time.sleep(2)
        with lock:
            results.extend(local)

    def report_worker(meta: dict) -> None:
        client = LoadTestClient(
            base_url, "admin", os.environ.get("LOADTEST_ADMIN_PASS", "admin123456"), verify_ssl
        )
        local = []
        for _ in range(5):
            local.append(client.get("/reports/", "report_dashboard", meta["user_id"]))
            time.sleep(0.5)
        with lock:
            results.extend(local)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=USER_COUNT) as pool:
        futs = []
        futs += [pool.submit(browse_worker, u) for u in browse_users]
        futs += [pool.submit(teach_worker, u) for u in teach_users]
        futs += [pool.submit(poll_worker, u) for u in poll_users]
        futs += [pool.submit(report_worker, report_user)]
        for f in as_completed(futs):
            f.result()

    return _aggregate(
        "Phase B — 峰值混合 (7浏览+5教学SSE+2轮询+1报告)",
        results,
        time.perf_counter() - t0,
        "5 路真实 DeepSeek SSE + 7 路浏览；最接近课间同时使用峰值",
        extra={
            "sse_requests": sum(1 for r in results if r.label == "teaching_sse"),
            "sse_ok": sum(1 for r in results if r.label == "teaching_sse" and r.ok),
            "sse_latencies_ms": [round(r.elapsed_ms, 1) for r in results if r.label == "teaching_sse"],
        },
    )


def phase_c_image_burst(base_url: str, users: list[dict], verify_ssl: bool) -> PhaseReport:
    """3 concurrent async scene image generations (Celery images queue)."""
    results: list[RequestResult] = []
    lock = threading.Lock()
    image_users = users[:3]
    prompt = "青少年心理教育场景：学生在安静教室里做深呼吸练习，温暖插画风格"

    def worker(meta: dict) -> None:
        client = LoadTestClient(base_url, meta["username"], LOADTEST_PASSWORD, verify_ssl)
        r = client.post_scene_image_and_wait(
            meta["session_id"],
            prompt,
            "scene_image_async",
            meta["user_id"],
            poll_timeout=180,
        )
        with lock:
            results.append(r)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(worker, image_users))
    return _aggregate(
        "Phase C — 3人同时配图 (Seedream 异步)",
        results,
        time.perf_counter() - t0,
        "模拟 3 人同时触发教学配图（Celery dispatch + 轮询，不阻塞 Gunicorn）",
        extra={
            "image_latencies_ms": [round(r.elapsed_ms, 1) for r in results],
        },
    )


def phase_d_sse_15(base_url: str, users: list[dict], verify_ssl: bool) -> PhaseReport:
    """15 simultaneous teaching SSE streams with barrier sync."""
    def work(client: LoadTestClient, meta: dict) -> RequestResult:
        return client.post_sse(
            f"/teaching/session/{meta['session_id']}/stream/",
            {"message": "我现在有点焦虑，请带我练习放松。"},
            "sse_15",
            meta["user_id"],
            timeout=120,
        )

    t0 = time.perf_counter()
    results = _run_barrier_workers(users, USER_COUNT, work, base_url, verify_ssl)
    sse_results = [r for r in results if r.label == "sse_15"]
    return _aggregate(
        "Phase D — 15路同时 SSE",
        results,
        time.perf_counter() - t0,
        "15 人 barrier 同步后同时发起教学 SSE，压满 Gunicorn 长连接槽位",
        extra={
            "sse_requests": len(sse_results),
            "sse_ok": sum(1 for r in sse_results if r.ok),
            "sse_latencies_ms": [round(r.elapsed_ms, 1) for r in sse_results],
        },
    )


def phase_e_voice_mixed(base_url: str, users: list[dict], verify_ssl: bool) -> PhaseReport:
    """5 SSE + 5 TTS + 5 browse, all starting together via barrier."""
    sse_users = users[:5]
    tts_users = users[5:10]
    browse_users = users[10:15]
    workload: list[tuple[dict, str]] = (
        [(u, "sse") for u in sse_users]
        + [(u, "tts") for u in tts_users]
        + [(u, "browse") for u in browse_users]
    )

    def work(client: LoadTestClient, meta: dict) -> RequestResult | list[RequestResult]:
        kind = meta["_kind"]
        if kind == "sse":
            return client.post_sse(
                f"/teaching/session/{meta['session_id']}/stream/",
                {"message": "我想学习怎么缓解考试紧张。"},
                "mixed_sse",
                meta["user_id"],
                timeout=120,
            )
        if kind == "tts":
            return client.post_tts(TTS_SAMPLE_TEXT, "mixed_tts", meta["user_id"], timeout=60)
        local = []
        for _ in range(8):
            local.append(client.get("/teaching/", "mixed_browse", meta["user_id"]))
            local.append(client.get("/mood/", "mixed_browse", meta["user_id"]))
            time.sleep(0.08)
        return local

    items = []
    for meta, kind in workload:
        items.append({**meta, "_kind": kind})

    t0 = time.perf_counter()
    results = _run_barrier_workers(items, 15, work, base_url, verify_ssl)
    sse = [r for r in results if r.label == "mixed_sse"]
    tts = [r for r in results if r.label == "mixed_tts"]
    browse = [r for r in results if r.label == "mixed_browse"]
    return _aggregate(
        "Phase E — 5 SSE + 5 TTS + 5 浏览",
        results,
        time.perf_counter() - t0,
        "barrier 同步后混合长 I/O：DeepSeek SSE + 火山 TTS + 轻量页面浏览",
        extra={
            "sse_ok": sum(1 for r in sse if r.ok),
            "sse_latencies_ms": [round(r.elapsed_ms, 1) for r in sse],
            "tts_ok": sum(1 for r in tts if r.ok),
            "tts_latencies_ms": [round(r.elapsed_ms, 1) for r in tts],
            "browse_ok": sum(1 for r in browse if r.ok),
        },
    )


def _phase_image_concurrent(
    base_url: str,
    users: list[dict],
    verify_ssl: bool,
    count: int,
    phase_name: str,
) -> PhaseReport:
    """Run N concurrent scene-image requests after clearing cache."""
    subset = users[:count]
    clear_scene_image_cache(subset)

    results: list[RequestResult] = []
    lock = threading.Lock()
    barrier = threading.Barrier(count)

    def run_one(meta: dict) -> None:
        client = LoadTestClient(base_url, meta["username"], LOADTEST_PASSWORD, verify_ssl)
        try:
            barrier.wait(timeout=60)
        except threading.BrokenBarrierError:
            with lock:
                results.append(RequestResult("scene_image", meta["user_id"], 0, 0, False, "barrier_broken"))
            return
        r = client.post_scene_image_and_wait(
            meta["session_id"],
            SCENE_IMAGE_PROMPT,
            "scene_image",
            meta["user_id"],
            poll_timeout=300,
        )
        with lock:
            results.append(r)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(run_one, subset))
    return _aggregate(
        phase_name,
        results,
        time.perf_counter() - t0,
        f"{count} 人 barrier 同步触发场景配图（Celery images 队列，并发上限 4）",
        extra={"image_latencies_ms": [round(r.elapsed_ms, 1) for r in results], "concurrency": count},
    )


def phase_f_images_5_10(base_url: str, users: list[dict], verify_ssl: bool) -> list[PhaseReport]:
    """5-way then 10-way concurrent scene images."""
    return [
        _phase_image_concurrent(
            base_url, users, verify_ssl, 5,
            "Phase F1 — 5路同时配图",
        ),
        _phase_image_concurrent(
            base_url, users, verify_ssl, 10,
            "Phase F2 — 10路同时配图",
        ),
    ]


def phase_g_test_burst(base_url: str, users: list[dict], verify_ssl: bool) -> PhaseReport:
    """15 simultaneous test starts → Celery question gen + staggered test illustrations."""
    mark_sessions_completed(users)
    queues_before = celery_queue_depths()

    def work(client: LoadTestClient, meta: dict) -> RequestResult:
        return client.start_test_and_wait_questions(
            meta["session_id"],
            "test_start",
            meta["user_id"],
            poll_timeout=300,
        )

    t0 = time.perf_counter()
    results = _run_barrier_workers(users, USER_COUNT, work, base_url, verify_ssl)
    queues_after_questions = celery_queue_depths()

    # Allow illustration tasks (5×15, staggered) to progress
    time.sleep(120)
    queues_after_wait = celery_queue_depths()

    test_ids = []
    for meta in users:
        latest = (
            Test.objects.filter(user__username=meta["username"], session_id=meta["session_id"])
            .order_by("-created_at")
            .first()
        )
        if latest:
            test_ids.append(latest.test_id)

    questions_total = TestQuestion.objects.filter(test_id__in=test_ids).count() if test_ids else 0
    images_ready = (
        TestQuestion.objects.filter(test_id__in=test_ids)
        .exclude(temporary_image_url="")
        .count()
        if test_ids
        else 0
    )
    with_image_prompt = (
        TestQuestion.objects.filter(test_id__in=test_ids)
        .exclude(image_prompt="")
        .count()
        if test_ids
        else 0
    )

    return _aggregate(
        "Phase G — 15人同时开始测试 (出题+配图 Celery)",
        results,
        time.perf_counter() - t0,
        "15 人 barrier 同步 POST 开始测试；出题走 celery 队列，配图走 images 队列",
        extra={
            "queues_before": queues_before,
            "queues_after_questions": queues_after_questions,
            "queues_after_45s": queues_after_wait,
            "tests_started": len(test_ids),
            "questions_total": questions_total,
            "questions_with_image_prompt": with_image_prompt,
            "question_images_ready": images_ready,
            "question_gen_latencies_ms": [round(r.elapsed_ms, 1) for r in results if r.label == "test_start"],
        },
    )


def _verdict(phases: list[PhaseReport]) -> dict[str, Any]:
    """Acceptance thresholds from concurrency-analysis.md mixed-load targets."""
    checks = []
    for p in phases:
        s = p.summary()
        name = p.name
        if "Phase A" in name or ("浏览" in name and "Phase E" not in name and "15×25" in name):
            checks.append({
                "check": "browse_success_rate",
                "phase": name,
                "pass": s["success_rate_pct"] >= 99.0,
                "value": s["success_rate_pct"],
                "threshold": ">= 99%",
            })
            p95 = s["latency_ms"].get("p95")
            checks.append({
                "check": "browse_p95_ms",
                "phase": name,
                "pass": p95 is not None and p95 < 3000,
                "value": p95,
                "threshold": "< 3000ms",
            })
        if "Phase B" in name or ("混合" in name and "Phase E" not in name):
            checks.append({
                "check": "mixed_success_rate",
                "phase": name,
                "pass": s["success_rate_pct"] >= 90.0,
                "value": s["success_rate_pct"],
                "threshold": ">= 90% (含真实 LLM)",
            })
            sse_ok = p.extra.get("sse_ok", 0)
            checks.append({
                "check": "sse_completed",
                "phase": name,
                "pass": sse_ok >= 3,
                "value": sse_ok,
                "threshold": ">= 3 of 5 SSE completed",
            })
        if "Phase C" in name:
            checks.append({
                "check": "image_success_rate",
                "phase": name,
                "pass": s["success_rate_pct"] >= 66.0,
                "value": s["success_rate_pct"],
                "threshold": ">= 66%",
            })
            p95 = s["latency_ms"].get("p95")
            checks.append({
                "check": "image_p95_ms",
                "phase": name,
                "pass": p95 is not None and p95 < 120000,
                "value": p95,
                "threshold": "< 120000ms",
            })
        if "Phase D" in name:
            sse_ok = p.extra.get("sse_ok", 0)
            checks.append({
                "check": "sse_15_completed",
                "phase": name,
                "pass": sse_ok >= 12,
                "value": sse_ok,
                "threshold": ">= 12 of 15 SSE",
            })
            p95 = s["latency_ms"].get("p95")
            checks.append({
                "check": "sse_15_p95_ms",
                "phase": name,
                "pass": p95 is not None and p95 < 120000,
                "value": p95,
                "threshold": "< 120000ms",
            })
        if "Phase E" in name:
            checks.append({
                "check": "mixed_e_sse",
                "phase": name,
                "pass": p.extra.get("sse_ok", 0) >= 4,
                "value": p.extra.get("sse_ok", 0),
                "threshold": ">= 4 of 5 SSE",
            })
            checks.append({
                "check": "mixed_e_tts",
                "phase": name,
                "pass": p.extra.get("tts_ok", 0) >= 4,
                "value": p.extra.get("tts_ok", 0),
                "threshold": ">= 4 of 5 TTS",
            })
            checks.append({
                "check": "mixed_e_browse",
                "phase": name,
                "pass": p.extra.get("browse_ok", 0) >= 70,
                "value": p.extra.get("browse_ok", 0),
                "threshold": ">= 70 browse OK (of 80)",
            })
        if "Phase F1" in name:
            checks.append({
                "check": "image_5_success",
                "phase": name,
                "pass": s["success_rate_pct"] >= 80.0,
                "value": s["success_rate_pct"],
                "threshold": ">= 80%",
            })
        if "Phase F2" in name:
            checks.append({
                "check": "image_10_success",
                "phase": name,
                "pass": s["success_rate_pct"] >= 70.0,
                "value": s["success_rate_pct"],
                "threshold": ">= 70%",
            })
            p95 = s["latency_ms"].get("p95")
            checks.append({
                "check": "image_10_p95_ms",
                "phase": name,
                "pass": p95 is not None and p95 < 180000,
                "value": p95,
                "threshold": "< 180000ms",
            })
        if "Phase G" in name:
            checks.append({
                "check": "test_questions_ready",
                "phase": name,
                "pass": s["success_count"] >= 12,
                "value": s["success_count"],
                "threshold": ">= 12 of 15 tests with 5 questions",
            })
            checks.append({
                "check": "test_images_progress",
                "phase": name,
                "pass": p.extra.get("question_images_ready", 0) >= 20,
                "value": p.extra.get("question_images_ready", 0),
                "threshold": ">= 20 images ready within 120s after questions",
            })
    overall = all(c["pass"] for c in checks) if checks else False
    return {"checks": checks, "overall_pass": overall}


def main() -> int:
    parser = argparse.ArgumentParser(description="DBT 15-user load test")
    parser.add_argument("--base-url", default=os.environ.get("LOADTEST_BASE_URL", "https://genaidbt.top"))
    parser.add_argument("--no-verify-ssl", action="store_true")
    parser.add_argument("--skip-phase-b", action="store_true", help="Skip real LLM SSE (cost saving)")
    parser.add_argument("--skip-phase-c", action="store_true", help="Skip real image generation")
    parser.add_argument(
        "--extended-only",
        action="store_true",
        help="Run only extended phases D–G (skip A–C)",
    )
    parser.add_argument(
        "--skip-original",
        action="store_true",
        help="Skip original phases A–C",
    )
    parser.add_argument(
        "--all-phases",
        action="store_true",
        help="Run original A–C and extended D–G",
    )
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    verify_ssl = not args.no_verify_ssl
    assert_loadtest_allowed(args.base_url)
    print(f"=== DBT 15-user load test @ {args.base_url} ===")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")

    if os.environ.get("LOADTEST_RESET_ADMIN", "").strip() == "1":
        admin_pass = os.environ.get("LOADTEST_ADMIN_PASS", "admin123456")
        ensure_admin_password(admin_pass)
        print("Admin password reset (LOADTEST_RESET_ADMIN=1)")
    users = provision_users(USER_COUNT)
    print(f"Provisioned {len(users)} load-test users ({LOADTEST_PREFIX}*)")

    phases: list[PhaseReport] = []
    run_original = (not args.extended_only) and (args.all_phases or not args.skip_original)
    run_extended = args.extended_only or args.all_phases or args.skip_original

    if run_original:
        phases.append(phase_a_browse(args.base_url, users, verify_ssl))
        print(
            f"Phase A done: {phases[-1].success_count}/{phases[-1].total_requests} OK, "
            f"p95={phases[-1].summary()['latency_ms']['p95']}ms"
        )

        if not args.skip_phase_b:
            phases.append(phase_b_mixed_peak(args.base_url, users, verify_ssl))
            print(
                f"Phase B done: {phases[-1].success_count}/{phases[-1].total_requests} OK, "
                f"p95={phases[-1].summary()['latency_ms']['p95']}ms"
            )

        if not args.skip_phase_c:
            phases.append(phase_c_image_burst(args.base_url, users, verify_ssl))
            print(
                f"Phase C done: {phases[-1].success_count}/{phases[-1].total_requests} OK, "
                f"p95={phases[-1].summary()['latency_ms']['p95']}ms"
            )

    if run_extended:
        if args.extended_only or args.skip_original:
            print("--- Extended load test phases D–G ---")

        phases.append(phase_d_sse_15(args.base_url, users, verify_ssl))
        print(
            f"Phase D done: SSE {phases[-1].extra.get('sse_ok', 0)}/15, "
            f"p95={phases[-1].summary()['latency_ms']['p95']}ms"
        )

        phases.append(phase_e_voice_mixed(args.base_url, users, verify_ssl))
        print(
            f"Phase E done: SSE {phases[-1].extra.get('sse_ok', 0)}/5, "
            f"TTS {phases[-1].extra.get('tts_ok', 0)}/5, "
            f"browse {phases[-1].extra.get('browse_ok', 0)}/80"
        )

        drained = wait_for_images_queue(max_wait_s=300, target=3)
        print(f"Images queue drained before Phase F: {drained}")

        for phase in phase_f_images_5_10(args.base_url, users, verify_ssl):
            phases.append(phase)
            print(
                f"{phase.name} done: {phase.success_count}/{phase.total_requests} OK, "
                f"p95={phase.summary()['latency_ms']['p95']}ms"
            )

        phases.append(phase_g_test_burst(args.base_url, users, verify_ssl))
        print(
            f"Phase G done: {phases[-1].success_count}/{phases[-1].total_requests} tests ready, "
            f"images={phases[-1].extra.get('question_images_ready', 0)}, "
            f"queues={phases[-1].extra.get('queues_after_45s', {})}"
        )

    from django.conf import settings as dj_settings

    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "user_count": USER_COUNT,
        "image_max_concurrent": int(getattr(dj_settings, "IMAGE_MAX_CONCURRENT", 4)),
        "phases": [p.summary() for p in phases],
        "verdict": _verdict(phases),
    }

    default_name = (
        "loadtest_extended_report.json"
        if args.extended_only
        else "loadtest_15users_report.json"
    )
    out_path = args.output or str(Path(__file__).resolve().parent / default_name)
    Path(out_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nReport saved: {out_path}")
    return 0 if report["verdict"]["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
