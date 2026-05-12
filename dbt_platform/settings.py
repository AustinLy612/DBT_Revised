"""
Django settings for DBT Platform.

Uses MongoDB via django-mongodb-backend as the primary database,
with Redis, MinIO, and Qdrant for async tasks, file storage, and vector search.
"""

import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Environment (.env) loading ──
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# ── Security ──
# DJANGO_DEBUG=True is for development only. Set to False in production.
# ALLOWED_HOSTS must include the external domain (not localhost) for cross-device access.
# CSRF_TRUSTED_ORIGINS must include https://<domain>:10443 in production.
SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-in-production")
DEBUG = env.bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# ── Applications ──
INSTALLED_APPS = [
    # Django built-in (MongoDB-compatible AppConfigs)
    "dbt_platform.apps.MongoAdminConfig",
    "dbt_platform.apps.MongoAuthConfig",
    "dbt_platform.apps.MongoContentTypesConfig",
    "dbt_platform.apps.MongoSessionsConfig",
    "dbt_platform.apps.MongoMessagesConfig",
    "dbt_platform.apps.MongoStaticFilesConfig",
    # Third-party
    "django_htmx",
    # DBT Platform apps
    "accounts",
    "questionnaire",
    "teaching",
    "testing",
    "mood",
    "risk",
    "knowledge_base",
    "export_app",
    "reports",
    "media_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.AdminAccessMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "dbt_platform.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "dbt_platform.wsgi.application"

# ── Database: MongoDB via django-mongodb-backend ──
# When MONGODB_HOST is set, use MongoDB; otherwise fall back to SQLite for
# quick local demos where MongoDB isn't available yet.
DATABASES = {
    "default": {
        "ENGINE": "django_mongodb_backend",
        "NAME": env("MONGODB_NAME", default="dbt_platform"),
        "HOST": env("MONGODB_HOST", default="localhost"),
        "PORT": env.int("MONGODB_PORT", default=27017),
        "USER": env("MONGODB_USER", default=""),
        "PASSWORD": env("MONGODB_PASSWORD", default=""),
        "OPTIONS": {
            "authSource": env("MONGODB_NAME", default="dbt_platform"),
        },
    }
}

# ── Password validation ──
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── Internationalization ──
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

# ── Static files ──
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# ── Media (user uploads, MinIO-backed in production) ──
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ── Default primary key field ──
DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"

# ── Authentication ──
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# ── External base URL for absolute-URL generation ──
EXTERNAL_BASE_URL = env("EXTERNAL_BASE_URL", default="http://localhost:8000")

# ── Redis ──
REDIS_HOST = env("REDIS_HOST", default="localhost")
REDIS_PORT = env.int("REDIS_PORT", default=6379)
REDIS_DB = env.int("REDIS_DB", default=0)
REDIS_PASSWORD = env("REDIS_PASSWORD", default="")
_REDIS_PASS_PART = f":{REDIS_PASSWORD}@" if REDIS_PASSWORD else ""
REDIS_URL = f"redis://{_REDIS_PASS_PART}{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"

# ── Celery ──
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Asia/Shanghai"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60

# ── MinIO ──
MINIO_ENDPOINT = env("MINIO_ENDPOINT", default="localhost:9000")
MINIO_ACCESS_KEY = env("MINIO_ACCESS_KEY", default="minioadmin")
MINIO_SECRET_KEY = env("MINIO_SECRET_KEY", default="minioadmin")
MINIO_BUCKET = env("MINIO_BUCKET", default="dbt-platform")
MINIO_SECURE = env.bool("MINIO_SECURE", default=False)

# ── Qdrant ──
QDRANT_HOST = env("QDRANT_HOST", default="localhost")
QDRANT_PORT = env.int("QDRANT_PORT", default=6333)
QDRANT_COLLECTION = env("QDRANT_COLLECTION", default="dbt_knowledge")

# ── MiniMax API ──
MINIMAX_API_KEY = env("MINIMAX_API_KEY", default="")
MINIMAX_BASE_URL = env("MINIMAX_BASE_URL", default="https://api.minimaxi.com")

# ── Volcengine (火山引擎) ASR — optional, for voice input ──
VOLCENGINE_API_KEY = env("VOLCENGINE_API_KEY", default="")

# ── Session / CSRF for cross-device consistency ──
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Allow the session cookie to be sent over HTTP in dev (HTTPS-only in prod)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
# Ensure admin is accessible on non-standard port
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ── Logging ──
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {module}: {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": BASE_DIR / "logs" / "dbt.log",
            "maxBytes": 10 * 1024 * 1024,  # 10 MB
            "backupCount": 5,
            "formatter": "verbose",
        },
    },
    "loggers": {
        "pymongo": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "httpcore": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "sentence_transformers": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "INFO" if not DEBUG else "DEBUG",
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "dbt_platform": {
            "handlers": ["console", "file"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}

# ── Fail-safe: warn if SECRET_KEY is the dev default in non-debug mode ──
if not DEBUG and SECRET_KEY == "insecure-dev-key-change-in-production":
    raise RuntimeError("DJANGO_SECRET_KEY must be set in production (see .env)")
