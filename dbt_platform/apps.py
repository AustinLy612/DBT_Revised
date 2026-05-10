"""Custom AppConfigs to make Django built-in apps compatible with MongoDB."""
from django.contrib.admin.apps import AdminConfig
from django.contrib.auth.apps import AuthConfig
from django.contrib.contenttypes.apps import ContentTypesConfig
from django.contrib.messages.apps import MessagesConfig
from django.contrib.sessions.apps import SessionsConfig
from django.contrib.staticfiles.apps import StaticFilesConfig

_AUTO = "django_mongodb_backend.fields.ObjectIdAutoField"


class MongoAdminConfig(AdminConfig):
    default_auto_field = _AUTO


class MongoAuthConfig(AuthConfig):
    default_auto_field = _AUTO


class MongoContentTypesConfig(ContentTypesConfig):
    default_auto_field = _AUTO


class MongoMessagesConfig(MessagesConfig):
    default_auto_field = _AUTO


class MongoSessionsConfig(SessionsConfig):
    default_auto_field = _AUTO


class MongoStaticFilesConfig(StaticFilesConfig):
    default_auto_field = _AUTO
