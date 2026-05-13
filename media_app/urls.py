from django.urls import path

from . import views

app_name = "media"

urlpatterns = [
    path("image/generate/", views.generate_image_view, name="generate_image"),
    path("tts/synthesize/", views.synthesize_speech_view, name="synthesize_speech"),
    path("tts/stream/", views.stream_speech_view, name="stream_speech"),
    path("asr/transcribe/", views.transcribe_audio_view, name="transcribe_audio"),
]
