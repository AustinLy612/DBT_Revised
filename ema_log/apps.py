from django.apps import AppConfig


class EMALogConfig(AppConfig):
    default_auto_field = "django_mongodb_backend.BigAutoField"
    name = "ema_log"
    verbose_name = "EMA日志"
