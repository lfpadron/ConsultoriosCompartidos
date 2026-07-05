"""Admin configuration for scheduling."""

from django.contrib import admin

from apps.scheduling.models import (
    AvailabilityException,
    AvailabilityRule,
    Reservation,
    Weekday,
    rule_weekdays,
)


@admin.register(AvailabilityRule)
class AvailabilityRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "room",
        "display_weekdays",
        "start_time",
        "end_time",
        "start_date",
        "end_date",
        "is_active",
    )
    list_filter = ("room__clinic", "weekday", "is_active")
    search_fields = ("name", "room__name", "room__clinic__name")

    @admin.display(description="Días")
    def display_weekdays(self, obj: AvailabilityRule) -> str:
        labels = dict(Weekday.choices)
        return ", ".join(str(labels[day]) for day in rule_weekdays(obj))


@admin.register(AvailabilityException)
class AvailabilityExceptionAdmin(admin.ModelAdmin):
    list_display = (
        "room",
        "date",
        "start_time",
        "end_time",
        "exception_type",
        "is_active",
    )
    list_filter = ("room__clinic", "exception_type", "is_active")
    search_fields = ("reason", "room__name", "room__clinic__name")


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = (
        "room",
        "tenant_doctor",
        "date",
        "start_time",
        "end_time",
        "status",
    )
    list_filter = ("status", "room__clinic")
    search_fields = ("room__name", "tenant_doctor__display_name", "notes")
