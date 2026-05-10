from django.urls import path

from . import views

app_name = "risk"
urlpatterns = [
    path("popup/", views.risk_popup_view, name="popup"),
]
