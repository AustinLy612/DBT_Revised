from django.urls import path

from . import views

app_name = "ema_log"
urlpatterns = [
    path("", views.ema_log_view, name="log"),
]
