"""Admin configuration for integrations."""

from django.contrib import admin

from apps.integration.models import (
    AccessCredential,
    ExternalSystem,
    IntegrationEndpoint,
)


@admin.register(ExternalSystem)
class ExternalSystemAdmin(admin.ModelAdmin):
    list_display = ("name", "system_type", "is_enabled", "is_active")
    list_filter = ("system_type", "is_enabled")
    search_fields = ("name", "base_url")


@admin.register(IntegrationEndpoint)
class IntegrationEndpointAdmin(admin.ModelAdmin):
    list_display = ("name", "system", "method", "path", "is_active")
    list_filter = ("method", "system__system_type")
    search_fields = ("name", "path", "system__name")


@admin.register(AccessCredential)
class AccessCredentialAdmin(admin.ModelAdmin):
    list_display = (
        "simulated_code",
        "reservation",
        "tenant_doctor",
        "room",
        "status",
        "valid_from",
        "valid_until",
    )
    list_filter = ("status", "room__clinic", "room")
    search_fields = (
        "simulated_code",
        "reservation__room__name",
        "tenant_doctor__display_name",
        "tenant_doctor__user__email",
        "room__name",
    )
    readonly_fields = (
        "simulated_code",
        "valid_from",
        "valid_until",
        "enabled_at",
        "used_at",
        "revoked_at",
        "expired_at",
    )
