"""Admin registrations for commercial configuration."""

from django.contrib import admin

from apps.billing.models import (
    CancellationPenaltyRule,
    CancellationPolicy,
    OwnerCommissionRule,
    OwnerPayoutSchedule,
    OwnerSubscription,
    RoomFixedFeeRule,
    RoomMonthlyFee,
    TenantSubscription,
)


class VersionedConfigurationAdmin(admin.ModelAdmin):
    list_display = ("description", "version", "start_date", "end_date", "is_active")
    list_filter = ("is_active",)
    readonly_fields = ("version", "created_at", "updated_at")

    @admin.display(description="Configuración")
    def description(self, obj: object) -> str:
        return str(obj)


admin.site.register(OwnerSubscription, VersionedConfigurationAdmin)
admin.site.register(TenantSubscription, VersionedConfigurationAdmin)
admin.site.register(OwnerCommissionRule, VersionedConfigurationAdmin)
admin.site.register(OwnerPayoutSchedule, VersionedConfigurationAdmin)
admin.site.register(RoomFixedFeeRule, VersionedConfigurationAdmin)
admin.site.register(RoomMonthlyFee, VersionedConfigurationAdmin)
admin.site.register(CancellationPolicy, VersionedConfigurationAdmin)
admin.site.register(CancellationPenaltyRule)
