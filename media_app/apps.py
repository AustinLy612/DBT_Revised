from django.apps import AppConfig


class MediaAppConfig(AppConfig):
    default_auto_field = "django_mongodb_backend.fields.ObjectIdAutoField"
    name = "media_app"
    verbose_name = "媒体服务"
