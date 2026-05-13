"""Safety test runner for Django + MongoDB.

Guarantees tests never touch the production database by:
1. Enforcing TEST.NAME in DATABASES config
2. Disconnecting create_permissions signal during test DB migration to work
   around a django-mongodb-backend 6.0.3 compatibility issue where ContentType
   instances lack primary keys during test setup
3. Restoring the original DB name after tests complete

If any safety check fails, the test run is aborted immediately — no data loss.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import connections
from django.db.models import signals
from django.test.runner import DiscoverRunner

logger = logging.getLogger("dbt_platform.test_runner")

TEST_NAME_PREFIX = "test_"

CREATE_PERMISSIONS_UID = "django.contrib.auth.management.create_permissions"


class TestDatabaseSafetyError(RuntimeError):
    """Raised when tests would operate on a non-test database."""


class SafetyTestRunner(DiscoverRunner):
    """Django test runner that ensures MongoDB test isolation.

    Before any tests run, this runner:
    - Disconnects the create_permissions post_migrate signal handler to avoid
      a django-mongodb-backend bug where ContentType instances lack PKs during
      test migrations
    - Verifies the TEST.NAME settings contain the test_ prefix
    - Aborts immediately if safety checks fail

    After tests:
    - Reconnects the create_permissions signal handler
    - Confirms the settings database name is restored to production
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._original_db_name = None

    def setup_databases(self, **kwargs):
        """Validate test isolation, silence create_permissions, then create
        test databases."""
        self._enforce_test_name_prefix()
        self._original_db_name = settings.DATABASES["default"]["NAME"]

        # Disconnect create_permissions to work around a django-mongodb-backend
        # bug: ContentType instances returned by get_for_models() during test
        # migrations lack primary keys, causing TypeError/hash failures and
        # ValueError/"must be saved" errors in create_permissions.
        self._permissions_disconnected = signals.post_migrate.disconnect(
            dispatch_uid=CREATE_PERMISSIONS_UID
        )
        if self._permissions_disconnected:
            logger.info(
                "Disconnected create_permissions signal for test migration"
            )

        try:
            return super().setup_databases(**kwargs)
        except Exception:
            self._reconnect_permissions()
            raise

    def teardown_databases(self, old_config, **kwargs):
        """Restore database config, verify restoration, and reconnect signals."""
        try:
            result = super().teardown_databases(old_config, **kwargs)
            self._verify_name_restored()
            return result
        finally:
            self._reconnect_permissions()

    def _reconnect_permissions(self):
        """Reconnect create_permissions post_migrate signal after test setup."""
        if getattr(self, "_permissions_disconnected", False):
            from django.contrib.auth.management import create_permissions

            signals.post_migrate.connect(
                create_permissions,
                dispatch_uid=CREATE_PERMISSIONS_UID,
            )
            logger.info("Reconnected create_permissions signal")
            self._permissions_disconnected = False

    # ── Safety checks ──

    def _enforce_test_name_prefix(self, databases: dict | None = None) -> None:
        """Verify that every configured database will use a test_* database.

        Args:
            databases: Optional DATABASES dict for direct testing. When None,
                       reads from Django settings.
        """
        if databases is None:
            databases = settings.DATABASES
        for alias, db_settings in databases.items():
            engine = db_settings.get("ENGINE", "")
            if "mongo" not in engine.lower():
                continue

            test_name = self._get_test_database_name(alias, db_settings)

            if not test_name.startswith(TEST_NAME_PREFIX):
                raise TestDatabaseSafetyError(
                    f"SAFETY BLOCK: Database '{alias}' has TEST.NAME='{test_name}' "
                    f"which does NOT start with '{TEST_NAME_PREFIX}'. "
                    f"Tests would destroy production data. "
                    f"Add TEST.NAME='{TEST_NAME_PREFIX}{db_settings['NAME']}' to DATABASES['{alias}']."
                )

            logger.info(
                "Test isolation verified: alias=%s, test_db=%s, prod_db=%s",
                alias,
                test_name,
                db_settings["NAME"],
            )

    def _get_test_database_name(self, alias: str, db_settings: dict) -> str:
        """Resolve the test database name, respecting explicit TEST.NAME."""
        test_settings = db_settings.get("TEST", {})
        if "NAME" in test_settings:
            return test_settings["NAME"]
        return TEST_NAME_PREFIX + db_settings["NAME"]

    def _verify_name_restored(self) -> None:
        """After tests, confirm DB name is restored to production."""
        current_name = settings.DATABASES["default"]["NAME"]
        if current_name == self._original_db_name:
            logger.info("Database name restored: %s", current_name)
        elif self._original_db_name and current_name != self._original_db_name:
            logger.error(
                "Database name NOT restored! Current: %s, expected: %s",
                current_name,
                self._original_db_name,
            )
            # Force-restore
            settings.DATABASES["default"]["NAME"] = self._original_db_name
            connections["default"].settings_dict["NAME"] = self._original_db_name
            logger.warning("Forced database name restoration to: %s", self._original_db_name)
