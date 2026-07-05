"""Manual owner settlement workflow services."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Model, QuerySet, Sum
from django.utils import timezone

from apps.astrotrace.services import record_event
from apps.catalog.models import OwnerProfile
from apps.finance.models import Settlement, SettlementStatus, Statement, StatementStatus
from apps.scheduling.models import Reservation, ReservationStatus


@dataclass(frozen=True)
class ReservationSettlementSummary:
    reservation: Reservation
    settlement: Settlement | None
    currency: str
    reservation_subtotal: Decimal
    platform_commission: Decimal
    commission_taxes: Decimal
    owner_net: Decimal
    status: str
    payment_reference: str


@dataclass(frozen=True)
class OwnerSettlementSummary:
    owner: OwnerProfile
    currency: str
    total_calculated: Decimal
    total_paid: Decimal
    total_pending: Decimal
    settlements: QuerySet[Settlement]


@transaction.atomic
def generate_settlement_for_reservation(
    *,
    reservation: Reservation,
    notes: str = "",
    actor: Model | None = None,
) -> Settlement:
    statement = _current_statement_for_reservation(reservation)
    if reservation.status not in {ReservationStatus.PAID, ReservationStatus.CONFIRMED}:
        raise ValidationError(
            {
                "reservation": (
                    "Sólo se puede liquidar una reservación pagada o confirmada."
                )
            }
        )
    if reservation.room.owner_id is None:
        raise ValidationError({"owner": "El consultorio no tiene propietario."})
    if _active_settlement_for_reservation(reservation).exists():
        raise ValidationError(
            {"reservation": "Ya existe una liquidación activa para esta reservación."}
        )

    settlement = Settlement(
        reservation=reservation,
        statement=statement,
        owner=reservation.room.owner,
        room=reservation.room,
        currency=statement.currency,
        reservation_subtotal=statement.subtotal,
        platform_commission=statement.platform_commission,
        commission_taxes=statement.commission_taxes,
        owner_net=statement.owner_net,
        status=SettlementStatus.CALCULATED,
        notes=notes,
    )
    if actor is not None:
        settlement.created_by = cast(Any, actor)
        settlement.updated_by = cast(Any, actor)
    settlement.save()

    record_event(
        event_type="settlement.generated",
        object_label=str(settlement),
        actor=actor,
        payload=_settlement_payload(settlement, actor=actor),
    )
    return settlement


@transaction.atomic
def mark_settlement_as_paid(
    *,
    settlement: Settlement,
    reference: str,
    payment_date: date | None = None,
    notes: str = "",
    actor: Model | None = None,
) -> Settlement:
    if settlement.status == SettlementStatus.CANCELLED:
        raise ValidationError(
            {"status": "No se puede pagar una liquidación cancelada."}
        )
    if not reference.strip():
        raise ValidationError(
            {
                "payment_reference": (
                    "La referencia de pago es obligatoria al marcar como pagada."
                )
            }
        )

    settlement.status = SettlementStatus.PAID
    settlement.payment_reference = reference
    settlement.payment_date = payment_date or timezone.localdate()
    settlement.paid_at = timezone.now()
    settlement.notes = notes or settlement.notes
    if actor is not None:
        settlement.paid_by = cast(Any, actor)
        settlement.updated_by = cast(Any, actor)
    settlement.save()

    record_event(
        event_type="settlement.paid",
        object_label=str(settlement),
        actor=actor,
        payload=_settlement_payload(settlement, actor=actor),
    )
    return settlement


@transaction.atomic
def cancel_settlement(
    *,
    settlement: Settlement,
    actor: Model | None = None,
) -> Settlement:
    if settlement.status == SettlementStatus.PAID:
        raise ValidationError(
            {"status": "No se puede cancelar una liquidación pagada."}
        )

    settlement.status = SettlementStatus.CANCELLED
    if actor is not None:
        settlement.updated_by = cast(Any, actor)
    settlement.save()

    record_event(
        event_type="settlement.cancelled",
        object_label=str(settlement),
        actor=actor,
        payload=_settlement_payload(settlement, actor=actor),
    )
    return settlement


def get_settlement_summary_for_owner(owner: OwnerProfile) -> OwnerSettlementSummary:
    settlements = Settlement.objects.filter(
        owner=owner,
        is_deleted=False,
    ).select_related("reservation", "room", "statement")
    currency = settlements.values_list("currency", flat=True).first() or "MXN"
    total_calculated = _settlement_total(
        settlements.exclude(status=SettlementStatus.CANCELLED)
    )
    total_paid = _settlement_total(settlements.filter(status=SettlementStatus.PAID))
    total_pending = _settlement_total(
        settlements.filter(
            status__in=(SettlementStatus.PENDING, SettlementStatus.CALCULATED)
        )
    )
    return OwnerSettlementSummary(
        owner=owner,
        currency=currency,
        total_calculated=total_calculated,
        total_paid=total_paid,
        total_pending=total_pending,
        settlements=settlements.order_by("-generated_at"),
    )


def get_settlement_summary_for_reservation(
    reservation: Reservation,
) -> ReservationSettlementSummary:
    settlement = _active_settlement_for_reservation(reservation).first()
    if settlement is not None:
        return ReservationSettlementSummary(
            reservation=reservation,
            settlement=settlement,
            currency=settlement.currency,
            reservation_subtotal=settlement.reservation_subtotal,
            platform_commission=settlement.platform_commission,
            commission_taxes=settlement.commission_taxes,
            owner_net=settlement.owner_net,
            status=settlement.get_status_display(),
            payment_reference=settlement.payment_reference,
        )

    statement = _current_statement_for_reservation(reservation)
    return ReservationSettlementSummary(
        reservation=reservation,
        settlement=None,
        currency=statement.currency,
        reservation_subtotal=statement.subtotal,
        platform_commission=statement.platform_commission,
        commission_taxes=statement.commission_taxes,
        owner_net=statement.owner_net,
        status="Sin liquidación",
        payment_reference="",
    )


def _current_statement_for_reservation(reservation: Reservation) -> Statement:
    statement = (
        reservation.statements.filter(status=StatementStatus.CURRENT)
        .order_by("-version")
        .first()
    )
    if statement is None:
        raise ValidationError(
            {"statement": "La reservación no tiene estado de cuenta vigente."}
        )
    return statement


def _active_settlement_for_reservation(
    reservation: Reservation,
) -> QuerySet[Settlement]:
    return (
        Settlement.objects.filter(reservation=reservation, is_deleted=False)
        .exclude(status=SettlementStatus.CANCELLED)
        .select_related("reservation", "statement", "owner", "room")
        .order_by("-generated_at")
    )


def _settlement_total(queryset: QuerySet[Settlement]) -> Decimal:
    return queryset.aggregate(total=Sum("owner_net"))["total"] or Decimal("0.00")


def _settlement_payload(
    settlement: Settlement,
    *,
    actor: Model | None,
) -> dict[str, str]:
    return {
        "model": settlement._meta.label,
        "id": str(settlement.pk),
        "level": "financiero",
        "reservation_id": str(settlement.reservation_id),
        "reservation": str(settlement.reservation),
        "owner_id": str(settlement.owner_id),
        "owner": str(settlement.owner),
        "room_id": str(settlement.room_id),
        "room": str(settlement.room),
        "subtotal": str(settlement.reservation_subtotal),
        "platform_commission": str(settlement.platform_commission),
        "commission_taxes": str(settlement.commission_taxes),
        "owner_net": str(settlement.owner_net),
        "currency": settlement.currency,
        "status": settlement.status,
        "reference": settlement.payment_reference,
        "actor_id": str(actor.pk) if actor is not None else "",
    }
