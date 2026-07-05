"""URL configuration for Consultorios Compartidos."""

from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path

from apps.identity.forms import EmailAuthenticationForm

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        LoginView.as_view(
            authentication_form=EmailAuthenticationForm,
            template_name="registration/login.html",
        ),
        name="login",
    ),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("", include("apps.presentation.urls")),
]
