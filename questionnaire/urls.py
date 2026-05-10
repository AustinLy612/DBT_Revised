from django.urls import path

from . import views

app_name = "questionnaire"
urlpatterns = [
    path("profile/", views.profile_view, name="profile"),
]
