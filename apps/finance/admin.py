"""Admin configuration for finance."""

from django.contrib import admin

from apps.finance.models import (
    AccountPayment,
    AccountStatement,
    AccountStatementLine,
    CancellationCase,
    CancellationItem,
    OwnerPayout,
    Payment,
    PaymentAllocation,
    RateRule,
    RoomRateDiscount,
    Settlement,
    Statement,
    TenantCredit,
    TenantCreditApplication,
    TenantDoctorDiscount,
)


@admin.register(AccountStatement)
class AccountStatementAdmin(admin.ModelAdmin):
    list_display = (
        "party_type",
        "owner",
        "tenant_doctor",
        "period_start",
        "period_end",
        "balance_due",
        "payout_due",
        "status",
    )
    list_filter = ("party_type", "status", "currency", "period_end")
    search_fields = (
        "owner__display_name",
        "owner__user__email",
        "tenant_doctor__display_name",
        "tenant_doctor__user__email",
    )


@admin.register(AccountStatementLine)
class AccountStatementLineAdmin(admin.ModelAdmin):
    list_display = (
        "account_statement",
        "line_type",
        "effective_date",
        "amount",
    )
    list_filter = ("line_type", "effective_date")


@admin.register(AccountPayment)
class AccountPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "account_statement",
        "category",
        "amount",
        "currency",
        "status",
        "payment_date",
    )
    list_filter = ("category", "status", "currency", "payment_date")


@admin.register(OwnerPayout)
class OwnerPayoutAdmin(admin.ModelAdmin):
    list_display = (
        "account_statement",
        "amount",
        "deducted_fees",
        "currency",
        "payment_date",
        "reference",
    )
    list_filter = ("currency", "payment_date")
    search_fields = (
        "account_statement__owner__display_name",
        "account_statement__owner__user__email",
        "reference",
    )


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


@admin.register(RoomRateDiscount)
class RoomRateDiscountAdmin(admin.ModelAdmin):
    list_display = (
        "room",
        "rate_rule",
        "percentage",
        "start_date",
        "end_date",
        "is_active",
    )
    list_filter = ("is_active", "room__clinic", "start_date")
    search_fields = ("room__name", "room__clinic__name", "rate_rule__name")


@admin.register(TenantDoctorDiscount)
class TenantDoctorDiscountAdmin(admin.ModelAdmin):
    list_display = (
        "tenant_doctor",
        "percentage",
        "start_date",
        "end_date",
        "is_active",
    )
    list_filter = ("is_active", "start_date")
    search_fields = ("tenant_doctor__display_name", "tenant_doctor__user__email")


@admin.register(Statement)
class StatementAdmin(admin.ModelAdmin):
    list_display = (
        "reservation",
        "version",
        "status",
        "subtotal",
        "discounts",
        "tariff_final",
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
        "batch",
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
        "batch__reference",
        "batch__room__name",
        "reservation__room__name",
        "statement__calculation_hash",
        "tenant_doctor__display_name",
        "tenant_doctor__user__email",
        "reference",
    )


@admin.register(PaymentAllocation)
class PaymentAllocationAdmin(admin.ModelAdmin):
    list_display = ("payment", "reservation", "statement", "amount")
    list_filter = ("payment__status", "reservation__room__clinic")
    search_fields = (
        "payment__batch__reference",
        "payment__reference",
        "reservation__room__name",
    )


@admin.register(CancellationCase)
class CancellationCaseAdmin(admin.ModelAdmin):
    list_display = (
        "batch",
        "tenant_doctor",
        "status",
        "resolution_method",
        "penalty_amount",
        "refundable_amount",
        "requested_at",
    )
    list_filter = ("status", "resolution_method", "currency")
    search_fields = ("batch__reference", "tenant_doctor__display_name")


@admin.register(CancellationItem)
class CancellationItemAdmin(admin.ModelAdmin):
    list_display = (
        "reservation",
        "days_before",
        "penalty_percentage",
        "paid_amount",
        "refundable_amount",
    )


@admin.register(TenantCredit)
class TenantCreditAdmin(admin.ModelAdmin):
    list_display = (
        "tenant_doctor",
        "original_amount",
        "remaining_amount",
        "currency",
        "status",
    )
    list_filter = ("status", "currency")
    search_fields = ("tenant_doctor__display_name", "tenant_doctor__user__email")


@admin.register(TenantCreditApplication)
class TenantCreditApplicationAdmin(admin.ModelAdmin):
    list_display = ("credit", "payment", "amount", "status")
    list_filter = ("status",)


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
