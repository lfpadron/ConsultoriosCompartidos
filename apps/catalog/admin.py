"""Admin configuration for business catalogs."""

from django.contrib import admin

from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    Equipment,
    OwnerProfile,
    Specialty,
    TenantDoctorProfile,
)


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "timezone", "hour_format", "is_active")
    list_filter = ("timezone", "hour_format", "is_active")
    search_fields = ("name", "phone", "email", "address")


@admin.register(ConsultingRoom)
class ConsultingRoomAdmin(admin.ModelAdmin):
    list_display = ("name", "clinic", "owner", "status", "capacity", "is_active")
    list_filter = ("clinic", "status", "is_active")
    search_fields = (
        "name",
        "clinic__name",
        "owner__display_name",
        "owner__user__email",
    )
    filter_horizontal = ("allowed_specialties", "excluded_specialties", "equipment")


@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "description")


@admin.register(OwnerProfile)
class OwnerProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "user", "phone", "tax_id", "is_active")
    list_filter = ("is_active",)
    search_fields = ("display_name", "user__email", "tax_id", "phone")


@admin.register(TenantDoctorProfile)
class TenantDoctorProfileAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "user",
        "professional_license",
        "status",
        "is_active",
    )
    list_filter = ("status", "is_active", "specialties")
    search_fields = ("display_name", "professional_license", "user__email", "tax_id")
    filter_horizontal = ("specialties",)


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "description")
