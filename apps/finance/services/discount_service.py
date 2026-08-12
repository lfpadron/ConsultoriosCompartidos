"""Discount resolution for reservation pricing."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q

from apps.catalog.models import ConsultingRoom, TenantDoctorProfile
from apps.finance.models import RateRule, RoomRateDiscount, TenantDoctorDiscount

ZERO_MONEY = Decimal("0.00")
ZERO_PERCENT = Decimal("0.0")
BEST_DISCOUNT_MESSAGE = "Se aplica únicamente el mejor descuento"


@dataclass(frozen=True)
class DiscountQuote:
    room_discount: RoomRateDiscount | None
    tenant_discount: TenantDoctorDiscount | None
    room_discount_percentage: Decimal
    tenant_discount_percentage: Decimal
    applied_percentage: Decimal
    discount_amount: Decimal
    tariff_total: Decimal
    tariff_final: Decimal
    message: str


def calculate_discount_quote(
    *,
    room: ConsultingRoom,
    tenant_doctor: TenantDoctorProfile | None,
    rate_rule: RateRule,
    reservation_date: date,
    tariff_total: Decimal,
) -> DiscountQuote:
    room_discount = active_room_rate_discount(
        room=room,
        rate_rule=rate_rule,
        reservation_date=reservation_date,
    )
    tenant_discount = active_tenant_doctor_discount(
        tenant_doctor=tenant_doctor,
        reservation_date=reservation_date,
    )
    room_percentage = (
        _percent(room_discount.percentage) if room_discount else ZERO_PERCENT
    )
    tenant_percentage = (
        _percent(tenant_discount.percentage) if tenant_discount else ZERO_PERCENT
    )
    applied_percentage = max(room_percentage, tenant_percentage)
    normalized_total = _money(tariff_total)
    discount_amount = _money(normalized_total * applied_percentage / Decimal("100"))
    tariff_final = _money(normalized_total - discount_amount)
    message = BEST_DISCOUNT_MESSAGE if room_discount and tenant_discount else ""

    return DiscountQuote(
        room_discount=room_discount,
        tenant_discount=tenant_discount,
        room_discount_percentage=room_percentage,
        tenant_discount_percentage=tenant_percentage,
        applied_percentage=applied_percentage,
        discount_amount=discount_amount,
        tariff_total=normalized_total,
        tariff_final=tariff_final,
        message=message,
    )


def active_room_rate_discount(
    *,
    room: ConsultingRoom,
    rate_rule: RateRule,
    reservation_date: date,
) -> RoomRateDiscount | None:
    return (
        RoomRateDiscount.objects.filter(
            room=room,
            rate_rule=rate_rule,
            is_active=True,
            is_deleted=False,
            start_date__lte=reservation_date,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=reservation_date))
        .order_by("-percentage", "-start_date", "-created_at")
        .first()
    )


def active_tenant_doctor_discount(
    *,
    tenant_doctor: TenantDoctorProfile | None,
    reservation_date: date,
) -> TenantDoctorDiscount | None:
    if tenant_doctor is None:
        return None
    return (
        TenantDoctorDiscount.objects.filter(
            tenant_doctor=tenant_doctor,
            is_active=True,
            is_deleted=False,
            start_date__lte=reservation_date,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=reservation_date))
        .order_by("-percentage", "-start_date", "-created_at")
        .first()
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), ROUND_HALF_UP)


def _percent(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.1"), ROUND_HALF_UP)
