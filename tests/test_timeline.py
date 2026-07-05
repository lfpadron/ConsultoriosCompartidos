from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.astrotrace.models import TraceEvent
from apps.astrotrace.services import record_event
from apps.astrotrace.services.timeline_service import (
    get_global_timeline,
    get_timeline_for_consulting_room,
    get_timeline_for_reservation,
)
from apps.finance.models import PaymentMethod
from apps.finance.services.payment_service import register_payment, validate_payment
from apps.finance.services.settlement_service import generate_settlement_for_reservation
from apps.scheduling.models import AvailabilityRule, Weekday
from apps.vault.models import DocumentType
from apps.vault.services.document_service import upload_document
from tests.test_finance import create_rate_rule
from tests.test_reservations import (
    create_user,
    create_valid_reservation,
)
from tests.test_vault import uploaded_file


def event_ids(items: list[Any]) -> set[str]:
    return {str(item.event.pk) for item in items}


@pytest.mark.django_db
def test_global_timeline_returns_events_ordered() -> None:
    older = record_event(
        event_type="timeline.old",
        object_label="Viejo",
        payload={"level": "informativo"},
    )
    newer = record_event(
        event_type="timeline.new",
        object_label="Nuevo",
        payload={"level": "operativo"},
    )
    TraceEvent.objects.filter(pk=older.pk).update(
        occurred_at=timezone.now() - timedelta(days=1)
    )
    TraceEvent.objects.filter(pk=newer.pk).update(occurred_at=timezone.now())

    items = get_global_timeline()

    assert items[0].event.pk == newer.pk
    assert items[1].event.pk == older.pk


@pytest.mark.django_db
def test_reservation_timeline_includes_reservation_events() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Timeline Reserva")

    items = get_timeline_for_reservation(reservation)

    assert any(item.event.event_type == "reservation.requested" for item in items)


@pytest.mark.django_db
def test_reservation_timeline_includes_payment_events() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Timeline Pago")
    payment = register_payment(
        reservation=reservation,
        amount=Decimal("100.00"),
        method=PaymentMethod.TRANSFER,
        reference="SPEI-TL",
    )

    items = get_timeline_for_reservation(reservation)

    assert str(payment.pk) in {
        str(item.event.payload.get("id")) for item in items if item.event.payload
    }
    assert any(item.event.event_type == "payment.registered" for item in items)


@pytest.mark.django_db
def test_reservation_timeline_includes_settlement_events() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Timeline Liq")
    payment = register_payment(
        reservation=reservation,
        amount=Decimal("375.00"),
        method=PaymentMethod.TRANSFER,
        reference="SPEI-LIQ",
    )
    validate_payment(payment=payment)
    reservation.refresh_from_db()
    settlement = generate_settlement_for_reservation(reservation=reservation)

    items = get_timeline_for_reservation(reservation)

    assert settlement.pk is not None
    assert any(item.event.event_type == "settlement.generated" for item in items)


@pytest.mark.django_db
def test_reservation_timeline_includes_document_events() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Timeline Doc")
    document = upload_document(
        title="Documento reserva",
        document_type=DocumentType.PAYMENT_RECEIPT,
        file=uploaded_file(content=b"timeline-doc"),
        reservation=reservation,
    )

    items = get_timeline_for_reservation(reservation)

    assert str(document.pk) in {
        str(item.event.payload.get("id")) for item in items if item.event.payload
    }
    assert any(item.event.event_type == "document.received" for item in items)


@pytest.mark.django_db
def test_room_timeline_includes_availability_and_rate_events() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Timeline Room")
    room = reservation.room
    availability_rule = AvailabilityRule.objects.filter(room=room).first()
    assert availability_rule is not None
    rate_rule = create_rate_rule(
        room,
        name="Tarifa Timeline",
        weekdays=[Weekday.MONDAY],
        amount=Decimal("95.00"),
        priority=3,
    )
    record_event(
        event_type="availability_rule.created",
        object_label=str(availability_rule),
        payload={
            "model": availability_rule._meta.label,
            "id": str(availability_rule.pk),
            "level": "operativo",
        },
    )
    record_event(
        event_type="rate_rule.created",
        object_label=str(rate_rule),
        payload={
            "model": rate_rule._meta.label,
            "id": str(rate_rule.pk),
            "level": "financiero",
        },
    )

    items = get_timeline_for_consulting_room(room)

    assert "availability_rule.created" in {item.event.event_type for item in items}
    assert "rate_rule.created" in {item.event.event_type for item in items}


@pytest.mark.django_db
def test_global_timeline_responds_200(client: Any) -> None:
    user = create_user("timeline-global@example.com")
    client.force_login(user)

    response = client.get("/timeline/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_event_detail_responds_200(client: Any) -> None:
    user = create_user("timeline-event@example.com")
    event = record_event(
        event_type="payment.registered",
        object_label="Pago",
        payload={"level": "financiero", "clave": "valor"},
    )
    client.force_login(user)

    response = client.get(f"/timeline/eventos/{event.pk}/")

    assert response.status_code == 200
    assert "clave" in response.content.decode()


@pytest.mark.django_db
def test_reservation_timeline_responds_200(client: Any) -> None:
    user = create_user("timeline-reservation@example.com")
    reservation = create_valid_reservation(room_name="Consultorio Timeline UI")
    client.force_login(user)

    response = client.get(f"/reservaciones/{reservation.pk}/timeline/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_room_timeline_responds_200(client: Any) -> None:
    user = create_user("timeline-room@example.com")
    reservation = create_valid_reservation(room_name="Consultorio Timeline Room UI")
    client.force_login(user)

    response = client.get(f"/consultorios/{reservation.room.pk}/timeline/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_reservation_detail_shows_timeline_link(client: Any) -> None:
    user = create_user("timeline-link-reservation@example.com")
    reservation = create_valid_reservation(room_name="Consultorio Timeline Link")
    client.force_login(user)

    response = client.get(f"/reservaciones/{reservation.pk}/")

    assert response.status_code == 200
    assert f"/reservaciones/{reservation.pk}/timeline/" in response.content.decode()


@pytest.mark.django_db
def test_room_detail_shows_timeline_link(client: Any) -> None:
    user = create_user("timeline-link-room@example.com")
    reservation = create_valid_reservation(room_name="Consultorio Timeline Link Room")
    client.force_login(user)

    response = client.get(f"/consultorios/{reservation.room.pk}/")

    assert response.status_code == 200
    assert f"/consultorios/{reservation.room.pk}/timeline/" in response.content.decode()


@pytest.mark.django_db
def test_financial_event_shows_financial_level(client: Any) -> None:
    user = create_user("timeline-financial@example.com")
    record_event(
        event_type="payment.registered",
        object_label="Pago",
        payload={"level": "financiero"},
    )
    client.force_login(user)

    response = client.get("/timeline/?level=financiero")

    assert response.status_code == 200
    assert "financiero" in response.content.decode()


@pytest.mark.django_db
def test_legal_event_shows_legal_level(client: Any) -> None:
    user = create_user("timeline-legal@example.com")
    record_event(
        event_type="document.received",
        object_label="Documento",
        payload={"level": "legal", "document_type": DocumentType.CONTRACT},
    )
    client.force_login(user)

    response = client.get("/timeline/?level=legal")

    assert response.status_code == 200
    assert "legal" in response.content.decode()


@pytest.mark.django_db
def test_metadata_json_does_not_break_template(client: Any) -> None:
    user = create_user("timeline-metadata@example.com")
    event = record_event(
        event_type="document.received",
        object_label="Documento",
        payload={"level": "legal", "metadata_json": {"nested": ["ok"]}},
    )
    client.force_login(user)

    response = client.get(f"/timeline/eventos/{event.pk}/")

    content = response.content.decode()
    assert response.status_code == 200
    assert "metadata_json" in content
    assert "nested" in content
