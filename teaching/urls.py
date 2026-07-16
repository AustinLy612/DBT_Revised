from django.urls import path

from . import views

app_name = "teaching"
urlpatterns = [
    path("", views.teaching_home_view, name="home"),
    path("start/", views.start_session_view, name="start"),
    path("session/<str:session_id>/", views.session_view, name="session"),
    path("session/<str:session_id>/pre_mood/", views.record_pre_mood_view, name="record_pre_mood"),
    path("session/<str:session_id>/personal_inquiry/", views.personal_inquiry_view, name="personal_inquiry"),
    path("session/<str:session_id>/skill/", views.confirm_skill_view, name="confirm_skill"),
    path("session/<str:session_id>/message/", views.send_message_view, name="send_message"),
    path("session/<str:session_id>/stream/", views.stream_message_view, name="stream_message"),
    path("message/<str:message_id>/image-status/", views.message_image_status_view, name="message_image_status"),
    path("message/<str:message_id>/retry-image/", views.retry_message_image_view, name="retry_message_image"),
    path("session/<str:session_id>/generate-scene-image/", views.generate_scene_image_view, name="generate_scene_image"),
    path("session/<str:session_id>/scene-image-status/", views.scene_image_status_view, name="scene_image_status"),
    path("session/<str:session_id>/retry-scene-image/", views.retry_scene_image_view, name="retry_scene_image"),
    path("session/<str:session_id>/end/", views.end_session_view, name="end_session"),
    path("session/<str:session_id>/terminate/", views.terminate_session_view, name="terminate"),
]
