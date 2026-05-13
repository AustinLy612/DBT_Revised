from django.urls import path

from . import views

app_name = "testing"
urlpatterns = [
    path("start/<slug:session_id>/", views.start_test_view, name="start"),
    path("test/<slug:test_id>/", views.test_view, name="test"),
    path("test/<slug:test_id>/poll/", views.poll_questions_view, name="poll"),
    path("test/<slug:test_id>/answer/", views.answer_question_view, name="answer"),
    path("test/<slug:test_id>/finish/", views.finish_test_view, name="finish"),
    path("test/<slug:test_id>/retest/", views.retest_view, name="retest"),
    path("test/<slug:test_id>/terminate/", views.terminate_test_view, name="terminate"),
    path(
        "question/<slug:question_id>/generate-image/",
        views.generate_question_image_view,
        name="generate_question_image",
    ),
    path(
        "question/<slug:question_id>/image-status/",
        views.question_image_status_view,
        name="question_image_status",
    ),
]
