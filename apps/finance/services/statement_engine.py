"""Statement generation for reservations."""

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.conf import settings
from django.db import transaction

from apps.finance.models import Statement, StatementStatus
from apps.finance.services.discount_service import calculate_discount_quote
from apps.finance.services.pricing_engine import BlockPrice, calculate_block_price
from apps.scheduling.models import Reservation

ZERO = Decimal("0.00")


class StatementGenerationError(Exception):
    """Raised when a statement cannot be generated for a reservation."""


@transaction.atomic
def generate_statement_for_reservation(reservation: Reservation) -> Statement:
    pricing = calculate_block_price(
        consulting_room=reservation.room,
        date=reservation.date,
        start_time=reservation.start_time,
        end_time=reservation.end_time,
    )
    return replace_statement_if_needed(reservation, pricing)


def replace_statement_if_needed(
    reservation: Reservation,
    pricing: BlockPrice,
) -> Statement:
    payload = _build_statement_payload(reservation, pricing)
    calculation_hash = calculate_statement_hash(payload)
    current_statement = (
        Statement.objects.filter(
            reservation=reservation,
            status=StatementStatus.CURRENT,
        )
        .order_by("-version")
        .first()
    )

    if current_statement and current_statement.calculation_hash == calculation_hash:
        return current_statement

    next_version = 1
    if current_statement:
        current_statement.status = StatementStatus.REPLACED
        current_statement.save(update_fields=["status", "updated_at"])
        next_version = current_statement.version + 1

    return Statement.objects.create(
        reservation=reservation,
        version=next_version,
        status=StatementStatus.CURRENT,
        currency=payload["currency"],
        duration_hours=payload["duration_hours"],
        subtotal=payload["subtotal"],
        discounts=payload["discounts"],
        room_discount_percentage=payload["room_discount_percentage"],
        tenant_discount_percentage=payload["tenant_discount_percentage"],
        tariff_total=payload["tariff_total"],
        tariff_final=payload["tariff_final"],
        taxes=payload["taxes"],
        total_doctor=payload["total_doctor"],
        platform_commission=payload["platform_commission"],
        commission_taxes=payload["commission_taxes"],
        owner_net=payload["owner_net"],
        applied_rate_rule=pricing.applied_rule,
        applied_room_discount_id=payload["applied_room_discount_id"],
        applied_tenant_discount_id=payload["applied_tenant_discount_id"],
        calculation_explanation=payload["calculation_explanation"],
        calculation_hash=calculation_hash,
    )


def calculate_statement_hash(payload: dict[str, Any]) -> str:
    serializable_payload = {
        key: str(value) if isinstance(value, Decimal) else value
        for key, value in payload.items()
    }
    canonical_payload = json.dumps(
        serializable_payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def _build_statement_payload(
    reservation: Reservation,
    pricing: BlockPrice,
) -> dict[str, Any]:
    if pricing.applied_rule is None or pricing.subtotal is None:
        msg = "No hay tarifa configurada para generar el estado de cuenta."
        raise StatementGenerationError(msg)

    subtotal = _money(pricing.subtotal)
    discount_quote = calculate_discount_quote(
        room=reservation.room,
        tenant_doctor=reservation.tenant_doctor,
        rate_rule=pricing.applied_rule,
        reservation_date=reservation.date,
        tariff_total=subtotal,
    )
    discounts = discount_quote.discount_amount
    taxes = ZERO
    total_doctor = _money(discount_quote.tariff_final + taxes)
    platform_commission = _money(
        discount_quote.tariff_final * Decimal(str(settings.PLATFORM_COMMISSION_RATE))
    )
    commission_taxes = ZERO
    owner_net = _money(
        discount_quote.tariff_final - platform_commission - commission_taxes
    )
    explanation = pricing.explanation
    if discount_quote.applied_percentage > ZERO:
        explanation = (
            f"{pricing.explanation}. Descuento aplicado: "
            f"{discount_quote.applied_percentage}%."
        )

    return {
        "reservation_id": str(reservation.pk),
        "room_id": str(reservation.room_id),
        "tenant_doctor_id": str(reservation.tenant_doctor_id),
        "date": reservation.date.isoformat(),
        "start_time": reservation.start_time.isoformat(),
        "end_time": reservation.end_time.isoformat(),
        "duration_hours": pricing.duration_hours,
        "rate_rule_id": str(pricing.applied_rule.pk),
        "currency": pricing.currency,
        "subtotal": subtotal,
        "discounts": discounts,
        "room_discount_percentage": discount_quote.room_discount_percentage,
        "tenant_discount_percentage": discount_quote.tenant_discount_percentage,
        "tariff_total": discount_quote.tariff_total,
        "tariff_final": discount_quote.tariff_final,
        "applied_room_discount_id": (
            str(discount_quote.room_discount.pk)
            if discount_quote.room_discount
            else None
        ),
        "applied_tenant_discount_id": (
            str(discount_quote.tenant_discount.pk)
            if discount_quote.tenant_discount
            else None
        ),
        "taxes": taxes,
        "total_doctor": total_doctor,
        "platform_commission": platform_commission,
        "commission_taxes": commission_taxes,
        "owner_net": owner_net,
        "calculation_explanation": explanation,
    }


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), ROUND_HALF_UP)
