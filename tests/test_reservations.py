from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.astrotrace.models import TraceEvent
from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    OwnerProfile,
    TenantDoctorProfile,
    TenantDoctorStatus,
)
from apps.finance.models import PriceType, RateRule, Statement, StatementStatus
from apps.finance.services.statement_engine import calculate_statement_hash
from apps.scheduling.models import (
    AvailabilityException,
    AvailabilityRule,
    ReservationStatus,
    Weekday,
)
from apps.scheduling.services.reservation_service import (
    cancel_reservation,
    confirm_reservation,
    create_reservation,
)


def create_user(email: str) -> Any:
    user_model = get_user_model()
    return user_model.objects.create_user(
        email=email,
        password="segura-123",
        first_name="Reserva",
        last_name="Usuario",
    )


def create_room(name: str = "Consultorio Reserva") -> ConsultingRoom:
    owner_user = create_user(f"{name.lower().replace(' ', '-')}@owner.example.com")
    clinic = Clinic.objects.create(name=f"Clínica {name}")
    owner = OwnerProfile.objects.create(user=owner_user)
    return ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        name=name,
        capacity=1,
    )


def create_tenant_doctor(
    email: str = "doctor@example.com",
    status: str = TenantDoctorStatus.AUTHORIZED,
) -> TenantDoctorProfile:
    user = create_user(email)
    return TenantDoctorProfile.objects.create(user=user, status=status)


def create_availability(room: ConsultingRoom) -> AvailabilityRule:
    return AvailabilityRule.objects.create(
        room=room,
        name="Lunes disponible",
        weekday=Weekday.MONDAY,
        start_time=time(8, 0),
        end_time=time(13, 0),
        start_date=date(2026, 6, 29),
    )


def create_rate(
    room: ConsultingRoom,
    *,
    price_type: str = PriceType.HOURLY,
    amount: Decimal = Decimal("75.00"),
) -> RateRule:
    return RateRule.objects.create(
        room=room,
        name="Tarifa reserva",
        weekdays=[Weekday.MONDAY],
        start_time=time(8, 0),
        end_time=time(13, 0),
        start_date=date(2026, 6, 29),
        price_type=price_type,
        amount=amount,
        currency="MXN",
        priority=1,
    )


def create_valid_reservation(
    *,
    room_name: str = "Consultorio Reserva",
    price_type: str = PriceType.HOURLY,
    amount: Decimal = Decimal("75.00"),
) -> Any:
    room = create_room(room_name)
    doctor = create_tenant_doctor(f"{room_name.lower().replace(' ', '-')}@doctor.test")
    create_availability(room)
    create_rate(room, price_type=price_type, amount=amount)
    reservation = create_reservation(
        room=room,
        tenant_doctor=doctor,
        reservation_date=date(2026, 6, 29),
        start_time=time(8, 0),
        end_time=time(13, 0),
    )
    return reservation


@pytest.mark.django_db
def test_create_valid_reservation_generates_statement_and_events() -> None:
    reservation = create_valid_reservation()

    statement = reservation.statements.get()
    assert reservation.status == ReservationStatus.REQUESTED
    assert statement.version == 1
    assert statement.status == StatementStatus.CURRENT
    assert TraceEvent.objects.filter(event_type="reservation.requested").exists()
    assert TraceEvent.objects.filter(event_type="statement.generated").exists()


@pytest.mark.django_db
def test_reject_reservation_outside_availability() -> None:
    room = create_room()
    doctor = create_tenant_doctor()
    create_rate(room)

    with pytest.raises(ValidationError):
        create_reservation(
            room=room,
            tenant_doctor=doctor,
            reservation_date=date(2026, 6, 29),
            start_time=time(8, 0),
            end_time=time(13, 0),
        )


@pytest.mark.django_db
def test_reject_reservation_in_exception() -> None:
    room = create_room()
    doctor = create_tenant_doctor()
    create_availability(room)
    create_rate(room)
    AvailabilityException.objects.create(
        room=room,
        date=date(2026, 6, 29),
        reason="Mantenimiento",
    )

    with pytest.raises(ValidationError):
        create_reservation(
            room=room,
            tenant_doctor=doctor,
            reservation_date=date(2026, 6, 29),
            start_time=time(8, 0),
            end_time=time(13, 0),
        )


@pytest.mark.django_db
def test_reject_overlapping_reservation() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Traslape")
    doctor = create_tenant_doctor("otro-doctor@example.com")

    with pytest.raises(ValidationError):
        create_reservation(
            room=reservation.room,
            tenant_doctor=doctor,
            reservation_date=reservation.date,
            start_time=reservation.start_time,
            end_time=reservation.end_time,
        )


@pytest.mark.django_db
def test_reject_non_authorized_tenant_doctor() -> None:
    room = create_room()
    doctor = create_tenant_doctor(
        "pendiente@example.com",
        status=TenantDoctorStatus.PENDING,
    )
    create_availability(room)
    create_rate(room)

    with pytest.raises(ValidationError):
        create_reservation(
            room=room,
            tenant_doctor=doctor,
            reservation_date=date(2026, 6, 29),
            start_time=time(8, 0),
            end_time=time(13, 0),
        )


@pytest.mark.django_db
def test_cancel_reservation() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Cancelar")

    cancel_reservation(reservation=reservation, reason="Solicitud del médico")
    reservation.refresh_from_db()

    assert reservation.status == ReservationStatus.CANCELLED
    assert reservation.cancelled_at is not None
    assert TraceEvent.objects.filter(event_type="reservation.cancelled").exists()


@pytest.mark.django_db
def test_confirm_reservation() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Confirmar")

    confirm_reservation(reservation=reservation)
    reservation.refresh_from_db()

    assert reservation.status == ReservationStatus.CONFIRMED
    assert reservation.confirmed_at is not None
    assert TraceEvent.objects.filter(event_type="reservation.confirmed").exists()


@pytest.mark.django_db
def test_hourly_statement_values() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Hora")

    statement = reservation.statements.get()

    assert statement.duration_hours == Decimal("5.00")
    assert statement.subtotal == Decimal("375.00")
    assert statement.total_doctor == Decimal("375.00")
    assert statement.platform_commission == Decimal("37.50")
    assert statement.owner_net == Decimal("337.50")
    assert "Tarifa reserva" in statement.calculation_explanation


@pytest.mark.django_db
def test_block_statement_values() -> None:
    reservation = create_valid_reservation(
        room_name="Consultorio Bloque",
        price_type=PriceType.BLOCK,
        amount=Decimal("150.00"),
    )

    statement = reservation.statements.get()

    assert statement.subtotal == Decimal("150.00")
    assert statement.platform_commission == Decimal("15.00")
    assert statement.owner_net == Decimal("135.00")


def test_statement_hash_is_consistent() -> None:
    payload = {"subtotal": Decimal("375.00"), "currency": "MXN", "version": 1}

    assert calculate_statement_hash(payload) == calculate_statement_hash(payload)
    assert len(calculate_statement_hash(payload)) == 64


@pytest.mark.django_db
def test_reservation_list_responds_200(client: Any) -> None:
    user = create_user("viewer-reservas@example.com")
    client.force_login(user)

    response = client.get("/reservaciones/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_reservation_detail_shows_statement(client: Any) -> None:
    user = create_user("detail-reservas@example.com")
    reservation = create_valid_reservation(room_name="Consultorio Detalle")
    client.force_login(user)

    response = client.get(f"/reservaciones/{reservation.pk}/")

    content = response.content.decode()
    assert response.status_code == 200
    assert "Estado de Cuenta" in content
    assert "337.50 MXN" in content


@pytest.mark.django_db
def test_calendar_shows_reservation_request_button(client: Any) -> None:
    user = create_user("calendar-reserva@example.com")
    room = create_room("Consultorio Botón")
    create_availability(room)
    create_rate(room)
    client.force_login(user)

    response = client.get(f"/calendario/?week=2026-07-06&room={room.pk}")

    assert response.status_code == 200
    assert "Solicitar reservación" in response.content.decode()


@pytest.mark.django_db
def test_calendar_groups_days_by_monday_weeks(client: Any) -> None:
    user = create_user("calendar-semanas@example.com")
    room = create_room("Consultorio Semanas")
    create_availability(room)
    create_rate(room)
    client.force_login(user)

    response = client.get(
        f"/calendario/?date_from=2026-07-08&date_to=2026-07-15&room={room.pk}"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Semana 06/07/2026 - 12/07/2026" in content
    assert "Semana 13/07/2026 - 19/07/2026" in content


@pytest.mark.django_db
def test_calendar_shows_tenant_doctor_filter(client: Any) -> None:
    user = create_user("calendar-filtro-medico@example.com")
    client.force_login(user)

    response = client.get("/calendario/")

    assert response.status_code == 200
    assert 'name="tenant_doctor"' in response.content.decode()


@pytest.mark.django_db
def test_quick_calendar_shows_free_day_and_reservation_action(client: Any) -> None:
    user = create_user("vista-rapida-libre@example.com")
    room = create_room("Consultorio Vista Rápida Libre")
    create_availability(room)
    create_rate(room)
    client.force_login(user)

    response = client.get(
        f"/calendario/vista-rapida/?week=2026-07-06"
        f"&room={room.pk}&selected_date=2026-07-06"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Vista rápida" in content
    assert "bi-check-lg" in content
    assert "Libre" in content
    assert "Reservar" in content
    assert 'name="tenant_doctor"' in content


@pytest.mark.django_db
def test_quick_calendar_shows_reserved_day_when_no_free_blocks(client: Any) -> None:
    user = create_user("vista-rapida-reservado@example.com")
    room = create_room("Consultorio Vista Rápida Reservado")
    doctor = create_tenant_doctor("doctor-vista-rapida@example.com")
    create_availability(room)
    create_rate(room)
    create_reservation(
        room=room,
        tenant_doctor=doctor,
        reservation_date=date(2026, 7, 6),
        start_time=time(8, 0),
        end_time=time(13, 0),
    )
    client.force_login(user)

    response = client.get(
        f"/calendario/vista-rapida/?week=2026-07-06"
        f"&room={room.pk}&selected_date=2026-07-06"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "bi-exclamation-triangle" in content
    assert "Reservado" in content


@pytest.mark.django_db
def test_calendar_past_days_are_read_only(client: Any) -> None:
    user = create_user("calendar-pasado@example.com")
    room = create_room("Consultorio Pasado")
    create_availability(room)
    create_rate(room)
    client.force_login(user)

    response = client.get(f"/calendario/?week=2026-06-29&room={room.pk}")

    content = response.content.decode()
    assert response.status_code == 200
    assert "Pasado" in content
    assert "Sólo consulta." in content
    assert "Solicitar reservación" not in content


@pytest.mark.django_db
def test_calendar_uses_clinic_hour_format(client: Any) -> None:
    user = create_user("calendar-formato@example.com")
    room = create_room("Consultorio AMPM")
    room.clinic.hour_format = "12h"
    room.clinic.save(update_fields=["hour_format"])
    create_availability(room)
    create_rate(room)
    client.force_login(user)

    response = client.get(f"/calendario/?week=2026-07-06&room={room.pk}")

    assert response.status_code == 200
    assert "08:00 AM - 01:00 PM" in response.content.decode()


@pytest.mark.django_db
def test_reservation_list_filters_by_tenant_doctor(client: Any) -> None:
    user = create_user("reservas-filtro-medico@example.com")
    first = create_valid_reservation(room_name="Consultorio Filtro Médico A")
    second = create_valid_reservation(room_name="Consultorio Filtro Médico B")
    client.force_login(user)

    response = client.get(
        f"/reservaciones/?tenant_doctor={first.tenant_doctor.pk}"
        "&date_from=2026-06-29&date_to=2026-06-29"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert f"/reservaciones/{first.pk}/" in content
    assert f"/reservaciones/{second.pk}/" not in content


@pytest.mark.django_db
def test_create_reservation_from_ui(client: Any) -> None:
    user = create_user("ui-reserva@example.com")
    room = create_room("Consultorio UI")
    doctor = create_tenant_doctor("doctor-ui@example.com")
    create_availability(room)
    create_rate(room)
    client.force_login(user)

    response = client.post(
        "/reservaciones/solicitar/",
        {
            "room": str(room.pk),
            "tenant_doctor": str(doctor.pk),
            "date": "2026-06-29",
            "start_time": "08:00",
            "end_time": "13:00",
            "notes": "Solicitud desde UI",
        },
    )

    assert response.status_code == 302
    assert Statement.objects.filter(reservation__notes="Solicitud desde UI").exists()
