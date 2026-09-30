"""Admin configuration for scheduling."""

from django.contrib import admin

from apps.scheduling.models import (
    AvailabilityException,
    AvailabilityRule,
    PaymentDeadlineException,
    Reservation,
    ReservationBatch,
    ReservationPaymentPolicy,
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


@admin.register(ReservationBatch)
class ReservationBatchAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "room",
        "tenant_doctor",
        "batch_type",
        "occurrence_count",
        "tariff_final",
        "currency",
        "status",
    )
    list_filter = ("batch_type", "status", "room__clinic")
    search_fields = (
        "reference",
        "room__name",
        "tenant_doctor__display_name",
        "tenant_doctor__user__email",
    )


@admin.register(ReservationPaymentPolicy)
class ReservationPaymentPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "clinic",
        "room",
        "hours_before_start",
        "advance_rule_enabled",
        "automatic_cancellation",
        "start_date",
        "end_date",
        "version",
        "is_active",
    )
    list_filter = ("clinic", "advance_rule_enabled", "is_active")
    search_fields = ("clinic__name", "room__name", "notes")


@admin.register(PaymentDeadlineException)
class PaymentDeadlineExceptionAdmin(admin.ModelAdmin):
    list_display = (
        "batch",
        "exception_type",
        "previous_deadline_at",
        "replacement_deadline_at",
        "authorized_by",
        "applied_at",
    )
    list_filter = ("exception_type", "batch__room__clinic")
    search_fields = ("batch__reference", "reason", "authorized_by__email")
