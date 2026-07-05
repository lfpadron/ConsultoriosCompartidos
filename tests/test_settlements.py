from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.astrotrace.models import TraceEvent
from apps.finance.models import PaymentMethod, Settlement, SettlementStatus
from apps.finance.services.payment_service import register_payment, validate_payment
from apps.finance.services.settlement_service import (
    cancel_settlement,
    generate_settlement_for_reservation,
    get_settlement_summary_for_owner,
    get_settlement_summary_for_reservation,
    mark_settlement_as_paid,
)
from apps.scheduling.models import ReservationStatus
from apps.scheduling.services.reservation_service import confirm_reservation
from tests.test_reservations import create_user, create_valid_reservation


def create_paid_reservation(room_name: str = "Consultorio Liquidación") -> Any:
    reservation = create_valid_reservation(room_name=room_name)
    payment = register_payment(
        reservation=reservation,
        amount=Decimal("375.00"),
        method=PaymentMethod.TRANSFER,
        reference=f"SPEI-{room_name}",
    )
    validate_payment(payment=payment)
    reservation.refresh_from_db()
    return reservation


def create_direct_settlement(reservation: Any) -> Settlement:
    statement = reservation.statements.get()
    return Settlement.objects.create(
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
    )


@pytest.mark.django_db
def test_valid_settlement_for_paid_reservation() -> None:
    reservation = create_paid_reservation("Consultorio Liquidación Válida")

    settlement = create_direct_settlement(reservation)

    assert settlement.status == SettlementStatus.CALCULATED
    assert settlement.owner_net == Decimal("337.50")


@pytest.mark.django_db
def test_reject_settlement_for_requested_reservation() -> None:
    reservation = create_valid_reservation(
        room_name="Consultorio Liquidación Solicitada"
    )
    statement = reservation.statements.get()

    with pytest.raises(ValidationError):
        Settlement.objects.create(
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
        )


@pytest.mark.django_db
def test_reject_duplicate_active_settlement() -> None:
    reservation = create_paid_reservation("Consultorio Liquidación Duplicada")
    generate_settlement_for_reservation(reservation=reservation)

    with pytest.raises(ValidationError):
        generate_settlement_for_reservation(reservation=reservation)


@pytest.mark.django_db
def test_reference_required_when_marking_paid() -> None:
    reservation = create_paid_reservation("Consultorio Liquidación Sin Referencia")
    settlement = generate_settlement_for_reservation(reservation=reservation)

    with pytest.raises(ValidationError):
        mark_settlement_as_paid(settlement=settlement, reference="")


@pytest.mark.django_db
def test_cannot_pay_cancelled_settlement() -> None:
    reservation = create_paid_reservation("Consultorio Liquidación Cancelada")
    settlement = generate_settlement_for_reservation(reservation=reservation)
    cancel_settlement(settlement=settlement)

    with pytest.raises(ValidationError):
        mark_settlement_as_paid(settlement=settlement, reference="LIQ-CANCELADA")


@pytest.mark.django_db
def test_cannot_cancel_paid_settlement() -> None:
    reservation = create_paid_reservation("Consultorio Liquidación Pagada")
    settlement = generate_settlement_for_reservation(reservation=reservation)
    mark_settlement_as_paid(settlement=settlement, reference="LIQ-PAGADA")

    with pytest.raises(ValidationError):
        cancel_settlement(settlement=settlement)


@pytest.mark.django_db
def test_generate_settlement_copies_statement_values() -> None:
    reservation = create_paid_reservation("Consultorio Liquidación Copia")
    statement = reservation.statements.get()

    settlement = generate_settlement_for_reservation(reservation=reservation)

    assert settlement.reservation_subtotal == statement.subtotal
    assert settlement.platform_commission == statement.platform_commission
    assert settlement.commission_taxes == statement.commission_taxes
    assert settlement.owner_net == statement.owner_net
    assert TraceEvent.objects.filter(event_type="settlement.generated").exists()


@pytest.mark.django_db
def test_mark_settlement_as_paid_saves_reference_and_user() -> None:
    user = create_user("liquidador@example.com")
    reservation = create_paid_reservation("Consultorio Liquidación Usuario")
    settlement = generate_settlement_for_reservation(reservation=reservation)

    mark_settlement_as_paid(
        settlement=settlement,
        reference="LIQ-001",
        payment_date=date(2026, 6, 30),
        actor=user,
    )
    settlement.refresh_from_db()

    assert settlement.status == SettlementStatus.PAID
    assert settlement.payment_reference == "LIQ-001"
    assert settlement.payment_date == date(2026, 6, 30)
    assert settlement.paid_by == user
    assert TraceEvent.objects.filter(event_type="settlement.paid").exists()


@pytest.mark.django_db
def test_cancel_settlement_changes_status() -> None:
    reservation = create_paid_reservation("Consultorio Liquidación Cancelar")
    settlement = generate_settlement_for_reservation(reservation=reservation)

    cancel_settlement(settlement=settlement)
    settlement.refresh_from_db()

    assert settlement.status == SettlementStatus.CANCELLED
    assert TraceEvent.objects.filter(event_type="settlement.cancelled").exists()


@pytest.mark.django_db
def test_owner_summary() -> None:
    reservation = create_paid_reservation("Consultorio Liquidación Resumen Owner")
    settlement = generate_settlement_for_reservation(reservation=reservation)

    summary = get_settlement_summary_for_owner(settlement.owner)

    assert summary.total_calculated == Decimal("337.50")
    assert summary.total_pending == Decimal("337.50")
    assert summary.total_paid == Decimal("0.00")


@pytest.mark.django_db
def test_reservation_summary() -> None:
    reservation = create_paid_reservation("Consultorio Liquidación Resumen Reserva")
    settlement = generate_settlement_for_reservation(reservation=reservation)

    summary = get_settlement_summary_for_reservation(reservation)

    assert summary.settlement == settlement
    assert summary.owner_net == Decimal("337.50")
    assert summary.status == "Calculada"


@pytest.mark.django_db
def test_generate_settlement_for_confirmed_reservation() -> None:
    reservation = create_valid_reservation(
        room_name="Consultorio Liquidación Confirmada"
    )
    confirm_reservation(reservation=reservation)
    reservation.refresh_from_db()

    settlement = generate_settlement_for_reservation(reservation=reservation)

    assert reservation.status == ReservationStatus.CONFIRMED
    assert settlement.status == SettlementStatus.CALCULATED


@pytest.mark.django_db
def test_reservation_detail_shows_settlement_section(client: Any) -> None:
    user = create_user("detalle-liquidacion@example.com")
    reservation = create_paid_reservation("Consultorio UI Liquidación Detalle")
    client.force_login(user)

    response = client.get(f"/reservaciones/{reservation.pk}/")

    content = response.content.decode()
    assert response.status_code == 200
    assert "Liquidación" in content
    assert "Neto propietario" in content


@pytest.mark.django_db
def test_settlement_list_responds_200(client: Any) -> None:
    user = create_user("listado-liquidaciones@example.com")
    client.force_login(user)

    response = client.get("/liquidaciones/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_generate_settlement_from_ui(client: Any) -> None:
    user = create_user("generar-liquidacion-ui@example.com")
    reservation = create_paid_reservation("Consultorio UI Generar Liquidación")
    client.force_login(user)

    response = client.post(
        f"/reservaciones/{reservation.pk}/liquidaciones/generar/",
        {"notes": "Generada desde UI"},
    )

    assert response.status_code == 302
    assert Settlement.objects.filter(notes="Generada desde UI").exists()


@pytest.mark.django_db
def test_mark_settlement_paid_from_ui(client: Any) -> None:
    user = create_user("pagar-liquidacion-ui@example.com")
    reservation = create_paid_reservation("Consultorio UI Pagar Liquidación")
    settlement = generate_settlement_for_reservation(reservation=reservation)
    client.force_login(user)

    response = client.post(
        f"/liquidaciones/{settlement.pk}/pagar/",
        {
            "reference": "LIQ-UI",
            "payment_date": "2026-06-30",
            "notes": "Pagada desde UI",
        },
    )

    settlement.refresh_from_db()
    assert response.status_code == 302
    assert settlement.status == SettlementStatus.PAID
    assert settlement.payment_reference == "LIQ-UI"
