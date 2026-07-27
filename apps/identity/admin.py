"""Admin configuration for users."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from apps.identity.models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    ordering = ("email",)
    list_display = (
        "email",
        "first_name",
        "last_name",
        "role",
        "phone",
        "must_change_password",
        "is_active",
    )
    list_filter = (
        "role",
        "is_staff",
        "is_superuser",
        "must_change_password",
        "is_active",
    )
    search_fields = ("email", "first_name", "last_name", "phone", "secondary_email")
    filter_horizontal = (
        "groups",
        "user_permissions",
        "assigned_clinics",
        "assigned_owners",
    )
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            _("Datos personales"),
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "phone",
                    "secondary_email",
                    "secondary_phone",
                    "role",
                )
            },
        ),
        (
            _("Asignaciones"),
            {"fields": ("assigned_clinics", "assigned_owners")},
        ),
        (
            _("Invitación"),
            {"fields": ("must_change_password", "invitation_sent_at")},
        ),
        (
            _("Permisos"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (_("Fechas importantes"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "first_name",
                    "last_name",
                    "phone",
                    "role",
                    "password1",
                    "password2",
                    "must_change_password",
                    "is_staff",
                    "is_active",
                ),
            },
        ),
    )
