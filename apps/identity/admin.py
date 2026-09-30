"""Admin configuration for users."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from apps.identity.models import (
    ApplicationScreen,
    CustomUser,
    RoleScreenPermission,
    UserRoleAssignment,
)


class UserRoleAssignmentInline(admin.TabularInline):
    model = UserRoleAssignment
    fk_name = "user"
    extra = 0
    fields = ("role", "is_active")


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
    inlines = (UserRoleAssignmentInline,)
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


@admin.register(ApplicationScreen)
class ApplicationScreenAdmin(admin.ModelAdmin):
    list_display = ("label", "key", "url_name", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("label", "key", "url_name")
    ordering = ("sort_order", "label")


@admin.register(RoleScreenPermission)
class RoleScreenPermissionAdmin(admin.ModelAdmin):
    list_display = ("screen", "role", "access_level", "is_active")
    list_filter = ("role", "access_level", "is_active")
    search_fields = ("screen__label", "screen__key")
    list_select_related = ("screen",)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_protected_permission:
            return False
        return super().has_delete_permission(request, obj)
