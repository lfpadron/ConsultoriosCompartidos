"""Admin configuration for finance."""

from django.contrib import admin

from apps.finance.models import Payment, RateRule, Settlement, Statement


@admin.register(RateRule)
class RateRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "room",
        "price_type",
        "amount",
        "currency",
        "priority",
        "is_active",
    )
    list_filter = ("currency", "price_type", "priority", "room__clinic", "is_active")
    search_fields = ("name", "room__name", "room__clinic__name")


@admin.register(Statement)
class StatementAdmin(admin.ModelAdmin):
    list_display = (
        "reservation",
        "version",
        "status",
        "subtotal",
        "total_doctor",
        "platform_commission",
        "owner_net",
        "currency",
    )
    list_filter = ("status", "currency")
    search_fields = (
        "reservation__room__name",
        "reservation__tenant_doctor__display_name",
        "calculation_hash",
    )


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "reservation",
        "statement",
        "tenant_doctor",
        "amount",
        "currency",
        "method",
        "status",
        "payment_date",
    )
    list_filter = (
        "status",
        "method",
        "currency",
        "reservation__room__clinic",
        "payment_date",
    )
    search_fields = (
        "reservation__room__name",
        "statement__calculation_hash",
        "tenant_doctor__display_name",
        "tenant_doctor__user__email",
        "reference",
    )


@admin.register(Settlement)
class SettlementAdmin(admin.ModelAdmin):
    list_display = (
        "reservation",
        "owner",
        "room",
        "owner_net",
        "currency",
        "status",
        "payment_reference",
        "payment_date",
    )
    list_filter = (
        "status",
        "currency",
        "room__clinic",
        "owner",
        "generated_at",
        "payment_date",
    )
    search_fields = (
        "reservation__room__name",
        "owner__display_name",
        "owner__user__email",
        "room__name",
        "statement__calculation_hash",
        "payment_reference",
    )
