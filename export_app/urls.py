from django.urls import path

from . import views

app_name = "export_app"
urlpatterns = [
    path("", views.export_page_view, name="export_page"),
    path("user/<str:user_id>/json/", views.export_user_json_view, name="export_user_json"),
    path("user/<str:user_id>/csv/", views.export_user_csv_view, name="export_user_csv"),
    path("users/json/", views.export_users_json_view, name="export_users_json"),
    path("users/csv/", views.export_users_csv_view, name="export_users_csv"),
]
