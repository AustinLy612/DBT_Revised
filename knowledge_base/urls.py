from django.urls import path

from . import views

app_name = "knowledge_base"
urlpatterns = [
    path("search/", views.search_view, name="search"),
]
