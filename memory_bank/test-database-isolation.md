# Test Database Isolation — Safety Design

## Problem

Django's test runner can inadvertently run `manage.py test` against the production
MongoDB database, **destroying all production data**. This is a real risk with
django-mongodb-backend because:

1. The default test database name is `test_<production_db_name>`, but if
   `TEST.NAME` is not explicitly set, some configurations silently fall back
   to the production database name.
2. The MongoDB backend's `_destroy_test_db` method **drops all collections**
   in the target database before running migrations. If the target is the
   production database, all data is permanently destroyed.

## Solution: SafetyTestRunner

`dbt_platform/test_runner.py` — a custom Django test runner that **guarantees**
tests never touch the production database.

### Three-Layer Defense

| Layer | Mechanism | When | Failure Mode |
|-------|-----------|------|-------------|
| 1. Config check | `TEST.NAME` must contain `test_` prefix | Before any DB connection | `TestDatabaseSafetyError` raised, **no tests run** |
| 2. Settings enforcement | `settings.py` explicitly sets `TEST.NAME` and `TEST_RUNNER` | At Django config load time | Misconfiguration is visible in code review |
| 3. Restoration verification | After tests, validates settings DB name is restored | During teardown | Force-restores if needed + logs warning |

### MongoDB Compatibility

The runner also works around a django-mongodb-backend 6.0.3 compatibility issue:
during test database migration, `ContentType` instances returned by
`get_for_models()` lack primary keys, which crashes Django's `create_permissions`
signal handler. The runner disconnects this signal during test setup and
reconnects it after (permission auto-creation is unnecessary during tests).

## Configuration

### settings.py (required)

```python
DATABASES = {
    "default": {
        "ENGINE": "django_mongodb_backend",
        "NAME": env("MONGODB_NAME", default="dbt_platform"),
        # ...
        "TEST": {
            "NAME": f"test_{env('MONGODB_NAME', default='dbt_platform')}",
        },
    }
}

TEST_RUNNER = "dbt_platform.test_runner.SafetyTestRunner"
```

### How to run tests

```bash
# Standard Django test command — SafetyTestRunner protects automatically
python manage.py test

# Run specific app tests
python manage.py test media_app

# Run specific test class
python manage.py test media_app.tests.TestDatabaseIsolationTests
```

## How it prevents data loss

When `manage.py test` is invoked:

1. `SafetyTestRunner.setup_databases()` is called
2. `_enforce_test_name_prefix()` checks `TEST.NAME` starts with `test_`
3. If not → `TestDatabaseSafetyError` halts everything **before any DB access**
4. If yes → disconnects `create_permissions` signal (MongoDB workaround)
5. Django creates `test_dbt_platform` database and runs migrations on it
6. Tests execute against the test database
7. `teardown_databases()` drops the test database
8. `_verify_name_restored()` confirms production DB name is back in settings

### What happens if someone removes TEST.NAME?

The runner detects the missing/incorrect prefix and aborts immediately:

```
TestDatabaseSafetyError: SAFETY BLOCK: Database 'default' has TEST.NAME='dbt_platform'
which does NOT start with 'test_'. Tests would destroy production data.
```

### What happens if someone changes TEST_RUNNER?

The isolation tests in `media_app/tests.py` (`TestDatabaseIsolationTests`)
verify the configuration. If `TEST_RUNNER` is changed, `test_safety_runner_is_configured`
will fail and alert the developer.

## Test coverage

`TestDatabaseIsolationTests` in `media_app/tests.py` provides 5 guardrail tests:

| Test | What it verifies |
|------|-----------------|
| `test_settings_have_test_name_prefix` | `TEST.NAME` contains `test_` and differs from production DB name |
| `test_safety_runner_is_configured` | `TEST_RUNNER` points to `SafetyTestRunner` |
| `test_safety_runner_detects_bad_config` | Runner raises `TestDatabaseSafetyError` for non-prefixed config |
| `test_safety_runner_accepts_good_config` | Runner accepts correctly prefixed config |
| `test_current_test_db_name_is_prefixed` | Runtime check — active DB name has `test_` prefix |

## Files involved

```
dbt_platform/
├── test_runner.py          # SafetyTestRunner implementation
├── settings.py             # TEST.NAME + TEST_RUNNER configuration
media_app/
└── tests.py                # TestDatabaseIsolationTests (guardrail tests)
```
