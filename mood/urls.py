from django.urls import path

from . import views

app_name = "mood"
urlpatterns = [
    path("", views.mood_home_view, name="home"),
    path("record/", views.record_mood_view, name="record"),
    path("post_teaching/<str:session_id>/", views.record_post_teaching_mood_view, name="post_teaching"),
    path("post_test/<str:test_id>/", views.record_post_test_mood_view, name="post_test"),
    path("achievements/", views.achievements_view, name="achievements"),
    path("stats/", views.mood_stats_view, name="stats"),
]
