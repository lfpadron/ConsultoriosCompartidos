import importlib
import re
from datetime import date, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.astrotrace.models import TraceEvent
from apps.billing.models import CancellationPenaltyRule, CancellationPolicy
from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    OwnerProfile,
    TenantDoctorProfile,
    TenantDoctorStatus,
)
from apps.finance.models import (
    PriceType,
    RateRule,
    RoomRateDiscount,
    Statement,
    StatementStatus,
    TenantDoctorDiscount,
)
from apps.finance.services.statement_engine import calculate_statement_hash
from apps.scheduling.models import (
    AvailabilityException,
    AvailabilityRule,
    Reservation,
    ReservationBatch,
    ReservationBatchStatus,
    ReservationBatchType,
    ReservationStatus,
    Weekday,
)
from apps.scheduling.services.reservation_service import (
    cancel_reservation,
    confirm_reservation,
    create_reservation,
    create_reservation_batch,
    generate_weekly_occurrence_dates,
    preview_reservation_batch,
)


def create_user(email: str) -> Any:
    user_model = get_user_model()
    return user_model.objects.create_user(
        email=email,
        password="segura-123",
        first_name="Reserva",
        last_name="Usuario",
    )


def future_monday() -> date:
    today = timezone.localdate()
    days_until_monday = (7 - today.weekday()) % 7 or 7
    return today + timedelta(days=days_until_monday + 28)


def create_room(name: str = "Consultorio Reserva") -> ConsultingRoom:
    owner_user = create_user(f"{name.lower().replace(' ', '-')}@owner.example.com")
    clinic = Clinic.objects.create(name=f"Clínica {name}")
    owner = OwnerProfile.objects.create(user=owner_user)
    return ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        number="101",
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


def test_generate_weekly_occurrence_dates_includes_each_week() -> None:
    assert generate_weekly_occurrence_dates(
        start_date=date(2026, 6, 29),
        end_date=date(2026, 7, 20),
    ) == [
        date(2026, 6, 29),
        date(2026, 7, 6),
        date(2026, 7, 13),
        date(2026, 7, 20),
    ]


@pytest.mark.django_db
def test_create_reservation_batch_creates_all_occurrences_and_totals() -> None:
    room = create_room("Consultorio Repetitivo")
    doctor = create_tenant_doctor("doctor-repetitivo@example.com")
    create_availability(room)
    create_rate(room)

    batch = create_reservation_batch(
        room=room,
        tenant_doctor=doctor,
        start_date=date(2026, 6, 29),
        start_time=time(8, 0),
        end_time=time(13, 0),
        recurrence_end_date=date(2026, 7, 13),
    )

    assert batch.batch_type == ReservationBatchType.RECURRING
    assert batch.occurrence_count == 3
    assert batch.tariff_total == Decimal("1125.00")
    assert batch.tariff_final == Decimal("1125.00")
    assert list(batch.reservations.values_list("date", flat=True)) == [
        date(2026, 6, 29),
        date(2026, 7, 6),
        date(2026, 7, 13),
    ]
    assert Statement.objects.filter(reservation__batch=batch).count() == 3
    assert TraceEvent.objects.filter(
        event_type="reservation_batch.created",
        object_label__contains=batch.reference,
    ).exists()


@pytest.mark.django_db
def test_reservation_batch_is_all_or_nothing_when_one_date_conflicts() -> None:
    room = create_room("Consultorio Conflicto Grupo")
    first_doctor = create_tenant_doctor("doctor-conflicto-previo@example.com")
    recurring_doctor = create_tenant_doctor("doctor-conflicto-grupo@example.com")
    create_availability(room)
    create_rate(room)
    create_reservation(
        room=room,
        tenant_doctor=first_doctor,
        reservation_date=date(2026, 7, 6),
        start_time=time(8, 0),
        end_time=time(13, 0),
    )
    existing_batches = ReservationBatch.objects.count()
    existing_reservations = Reservation.objects.count()

    preview = preview_reservation_batch(
        room=room,
        tenant_doctor=recurring_doctor,
        start_date=date(2026, 6, 29),
        start_time=time(8, 0),
        end_time=time(13, 0),
        recurrence_end_date=date(2026, 7, 13),
    )
    with pytest.raises(ValidationError):
        create_reservation_batch(
            room=room,
            tenant_doctor=recurring_doctor,
            start_date=date(2026, 6, 29),
            start_time=time(8, 0),
            end_time=time(13, 0),
            recurrence_end_date=date(2026, 7, 13),
        )

    assert preview.has_conflicts
    assert preview.occurrences[1].conflict
    assert ReservationBatch.objects.count() == existing_batches
    assert Reservation.objects.count() == existing_reservations


@pytest.mark.django_db
def test_reservation_batch_snapshots_active_cancellation_policy() -> None:
    room = create_room("Consultorio Política Grupo")
    doctor = create_tenant_doctor("doctor-politica-grupo@example.com")
    create_availability(room)
    create_rate(room)
    policy = CancellationPolicy.objects.create(
        clinic=room.clinic,
        room=room,
        name="Cancelación consultorio",
        start_date=date(2026, 1, 1),
    )
    CancellationPenaltyRule.objects.create(
        policy=policy,
        days_before=0,
        percentage=Decimal("100.0"),
    )

    batch = create_reservation_batch(
        room=room,
        tenant_doctor=doctor,
        start_date=date(2026, 6, 29),
        start_time=time(8, 0),
        end_time=time(13, 0),
    )

    assert batch.cancellation_policy == policy
    assert batch.cancellation_terms_accepted_at is not None
    assert batch.cancellation_policy_snapshot["name"] == "Cancelación consultorio"
    assert batch.cancellation_policy_snapshot["penalties"] == [
        {"days_before": 0, "percentage": "100.0"}
    ]


@pytest.mark.django_db
def test_cancelling_occurrences_updates_batch_status() -> None:
    room = create_room("Consultorio Estado Grupo")
    doctor = create_tenant_doctor("doctor-estado-grupo@example.com")
    create_availability(room)
    create_rate(room)
    batch = create_reservation_batch(
        room=room,
        tenant_doctor=doctor,
        start_date=date(2026, 6, 29),
        start_time=time(8, 0),
        end_time=time(13, 0),
        recurrence_end_date=date(2026, 7, 6),
    )
    reservations = list(batch.reservations.order_by("date"))

    cancel_reservation(reservation=reservations[0], reason="Primera fecha")
    batch.refresh_from_db()
    assert batch.status == ReservationBatchStatus.PARTIALLY_CANCELLED

    cancel_reservation(reservation=reservations[1], reason="Segunda fecha")
    batch.refresh_from_db()
    assert batch.status == ReservationBatchStatus.CANCELLED
    assert (
        TraceEvent.objects.filter(event_type="reservation_batch.status_changed").count()
        == 2
    )


@pytest.mark.django_db
def test_reservation_batch_data_migration_is_reversible() -> None:
    room = create_room("Consultorio Histórico")
    doctor = create_tenant_doctor("doctor-historico@example.com")
    reservation = Reservation.objects.create(
        room=room,
        tenant_doctor=doctor,
        date=date(2026, 6, 29),
        start_time=time(8, 0),
        end_time=time(9, 0),
        tariff_total=Decimal("75.00"),
        tariff_final=Decimal("75.00"),
    )
    migration = importlib.import_module(
        "apps.scheduling.migrations.0007_backfill_reservation_batches"
    )

    migration.backfill_reservation_batches(django_apps, None)
    reservation.refresh_from_db()

    assert reservation.batch is not None
    assert reservation.batch.batch_type == ReservationBatchType.SINGLE
    assert reservation.batch.recurrence_rule["legacy"] is True
    batch_id = reservation.batch_id

    migration.reverse_backfill_reservation_batches(django_apps, None)
    reservation.refresh_from_db()

    assert reservation.batch_id is None
    assert not ReservationBatch.objects.filter(pk=batch_id).exists()


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

    target_date = future_monday()
    response = client.get(f"/calendario/?week={target_date.isoformat()}&room={room.pk}")

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
def test_calendar_room_dropdown_filters_by_tenant_doctor(client: Any) -> None:
    user = create_user("calendar-consultorio-arrendatario@example.com")
    assigned_room = create_room("Consultorio Asignado")
    other_room = create_room("Consultorio No Asignado")
    doctor = create_tenant_doctor("doctor-asignado@example.com")
    doctor.assigned_rooms.add(assigned_room)
    client.force_login(user)

    response = client.get(f"/calendario/?tenant_doctor={doctor.pk}")

    content = response.content.decode()
    assert response.status_code == 200
    assert str(assigned_room.pk) in content
    assert str(other_room.pk) not in content


@pytest.mark.django_db
def test_quick_calendar_shows_free_day_and_reservation_action(client: Any) -> None:
    user = create_user("vista-rapida-libre@example.com")
    room = create_room("Consultorio Vista Rápida Libre")
    create_availability(room)
    create_rate(room)
    client.force_login(user)

    target_date = future_monday()
    response = client.get(
        f"/calendario/vista-rapida/?week={target_date.isoformat()}"
        f"&room={room.pk}&selected_date={target_date.isoformat()}"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Vista rápida" in content
    assert "bi-check-lg" in content
    assert "Libre" in content
    assert "Reservar" in content
    assert 'name="tenant_doctor"' in content


@pytest.mark.django_db
def test_quick_calendar_has_four_week_navigation(client: Any) -> None:
    user = create_user("vista-rapida-navegacion@example.com")
    client.force_login(user)

    response = client.get(
        "/calendario/vista-rapida/?week=2026-08-10&selected_date=2026-08-10"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "date_from=2026-07-13" in content
    assert "date_from=2026-09-07" in content
    assert "Anterior" in content
    assert "Siguiente" in content


@pytest.mark.django_db
def test_calendar_and_quick_clinic_change_refreshes_room_dropdown(
    client: Any,
) -> None:
    user = create_user("calendar-clinica-refresco@example.com")
    client.force_login(user)

    calendar_response = client.get("/calendario/")
    quick_response = client.get("/calendario/vista-rapida/")

    assert calendar_response.status_code == 200
    assert quick_response.status_code == 200
    assert "querySelector(&#x27;[name=room]&#x27;).value=&#x27;&#x27;" in (
        calendar_response.content.decode()
    )
    assert "querySelector(&#x27;[name=room]&#x27;).value=&#x27;&#x27;" in (
        quick_response.content.decode()
    )


@pytest.mark.django_db
def test_quick_calendar_defaults_logged_tenant_and_warns_without_rooms(
    client: Any,
) -> None:
    room = create_room("Consultorio Vista Rápida Sin Asignación")
    doctor = create_tenant_doctor("doctor-sin-consultorio@example.com")
    create_availability(room)
    create_rate(room)
    client.force_login(doctor.user)

    response = client.get(
        "/calendario/vista-rapida/?week=2026-08-10&selected_date=2026-08-10"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert str(doctor.pk) in content
    assert "El usuario registrado no está asignado a ningún consultorio." in content
    assert room.name in content


@pytest.mark.django_db
def test_quick_calendar_shows_reserved_day_when_no_free_blocks(client: Any) -> None:
    user = create_user("vista-rapida-reservado@example.com")
    room = create_room("Consultorio Vista Rápida Reservado")
    doctor = create_tenant_doctor("doctor-vista-rapida@example.com")
    create_availability(room)
    create_rate(room)
    target_date = future_monday()
    create_reservation(
        room=room,
        tenant_doctor=doctor,
        reservation_date=target_date,
        start_time=time(8, 0),
        end_time=time(13, 0),
    )
    client.force_login(user)

    response = client.get(
        f"/calendario/vista-rapida/?week={target_date.isoformat()}"
        f"&room={room.pk}&selected_date={target_date.isoformat()}"
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
def test_reservation_request_prefills_context_and_pricing(client: Any) -> None:
    user = create_user("solicitud-contexto@example.com")
    room = create_room("Consultorio Contexto")
    room.clinic.schedule_text = "Presentarse 10 minutos antes."
    room.clinic.save(update_fields=["schedule_text"])
    doctor = create_tenant_doctor("doctor-contexto@example.com")
    create_availability(room)
    create_rate(room)
    client.force_login(user)

    response = client.get(
        "/reservaciones/solicitar/"
        f"?source=quick&room={room.pk}&tenant_doctor={doctor.pk}"
        "&date=2026-08-10&start_time=08:00&end_time=13:00"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Volver a la vista rápida" in content
    assert "Presentarse 10 minutos antes." in content
    assert "Tipo de tarifa" in content
    assert "Tarifa total" in content
    assert "75.00 MXN/h" in content
    assert "375.00 MXN" in content
    assert f'value="{room.pk}" selected' in content
    assert f'value="{doctor.pk}" selected' in content


@pytest.mark.django_db
def test_reservation_request_shows_best_discount_and_final_rate(
    client: Any,
) -> None:
    user = create_user("solicitud-descuento@example.com")
    room = create_room("Consultorio Descuento UI")
    doctor = create_tenant_doctor("doctor-descuento-ui@example.com")
    create_availability(room)
    rule = create_rate(room)
    RoomRateDiscount.objects.create(
        room=room,
        rate_rule=rule,
        percentage=Decimal("10.0"),
        start_date=date(2026, 6, 29),
    )
    TenantDoctorDiscount.objects.create(
        tenant_doctor=doctor,
        percentage=Decimal("20.0"),
        start_date=date(2026, 6, 29),
    )
    client.force_login(user)

    response = client.get(
        "/reservaciones/solicitar/"
        f"?room={room.pk}&tenant_doctor={doctor.pk}"
        "&date=2026-08-10&start_time=08:00&end_time=13:00"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Descuento consultorio" in content
    assert "Descuento médico arrendatario" in content
    assert "10.0%" in content
    assert "20.0%" in content
    assert "Tarifa final" in content
    assert "300.00 MXN" in content
    assert "Se aplica únicamente el mejor descuento" in content


@pytest.mark.django_db
def test_reservation_request_hourly_time_dropdowns_keep_one_hour_margin(
    client: Any,
) -> None:
    user = create_user("solicitud-horas@example.com")
    room = create_room("Consultorio Horas")
    doctor = create_tenant_doctor("doctor-horas@example.com")
    create_availability(room)
    create_rate(room)
    client.force_login(user)

    response = client.get(
        "/reservaciones/solicitar/"
        f"?room={room.pk}&tenant_doctor={doctor.pk}"
        "&date=2026-08-10&start_time=08:00&end_time=13:00"
    )

    content = response.content.decode()
    start_select = re.search(
        r'<select[^>]*name="start_time"[^>]*>(.*?)</select>',
        content,
        re.DOTALL,
    )
    end_select = re.search(
        r'<select[^>]*name="end_time"[^>]*>(.*?)</select>',
        content,
        re.DOTALL,
    )
    assert response.status_code == 200
    assert start_select is not None
    assert end_select is not None
    assert 'value="08:00"' in start_select.group(1)
    assert 'value="12:00"' in start_select.group(1)
    assert 'value="12:30"' not in start_select.group(1)
    assert 'value="08:30"' not in end_select.group(1)
    assert 'value="09:00"' in end_select.group(1)
    assert 'value="13:00"' in end_select.group(1)


@pytest.mark.django_db
def test_reservation_request_hourly_start_dropdown_stays_inside_selected_block(
    client: Any,
) -> None:
    user = create_user("solicitud-horas-bloque@example.com")
    room = create_room("Consultorio Horas Bloque")
    doctor = create_tenant_doctor("doctor-horas-bloque@example.com")
    AvailabilityRule.objects.create(
        room=room,
        name="Lunes temprano",
        weekday=Weekday.MONDAY,
        start_time=time(8, 0),
        end_time=time(9, 0),
        start_date=date(2026, 6, 29),
    )
    AvailabilityRule.objects.create(
        room=room,
        name="Lunes seleccionado",
        weekday=Weekday.MONDAY,
        start_time=time(10, 0),
        end_time=time(13, 0),
        start_date=date(2026, 6, 29),
    )
    create_rate(room)
    client.force_login(user)

    response = client.get(
        "/reservaciones/solicitar/"
        f"?room={room.pk}&tenant_doctor={doctor.pk}"
        "&date=2026-08-10&start_time=10:00&end_time=13:00"
    )

    content = response.content.decode()
    start_select = re.search(
        r'<select[^>]*name="start_time"[^>]*>(.*?)</select>',
        content,
        re.DOTALL,
    )
    assert response.status_code == 200
    assert start_select is not None
    assert 'value="08:00"' not in start_select.group(1)
    assert 'value="08:30"' not in start_select.group(1)
    assert 'value="09:00"' not in start_select.group(1)
    assert 'value="10:00"' in start_select.group(1)
    assert 'value="12:00"' in start_select.group(1)
    assert 'value="12:30"' not in start_select.group(1)


@pytest.mark.django_db
def test_reservation_request_shows_block_dropdown(client: Any) -> None:
    user = create_user("solicitud-bloque@example.com")
    room = create_room("Consultorio Bloque UI")
    doctor = create_tenant_doctor("doctor-bloque-ui@example.com")
    create_availability(room)
    create_rate(room, price_type=PriceType.BLOCK, amount=Decimal("150.00"))
    client.force_login(user)

    response = client.get(
        "/reservaciones/solicitar/"
        f"?room={room.pk}&tenant_doctor={doctor.pk}"
        "&date=2026-08-10&start_time=08:00&end_time=13:00"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert 'name="block_slot"' in content
    assert "Bloque disponible" in content
    assert "150.00 MXN" in content


@pytest.mark.django_db
def test_reservation_request_back_buttons_disable_htmx_boost(client: Any) -> None:
    user = create_user("solicitud-volver@example.com")
    room = create_room("Consultorio Volver")
    doctor = create_tenant_doctor("doctor-volver@example.com")
    create_availability(room)
    create_rate(room)
    client.force_login(user)

    response = client.get(
        "/reservaciones/solicitar/"
        f"?source=quick&room={room.pk}&tenant_doctor={doctor.pk}"
        "&date=2026-08-10&start_time=08:00&end_time=13:00"
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert "Volver a la vista rápida" in content
    assert content.count('hx-boost="false" href="/calendario/vista-rapida/"') == 2


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


@pytest.mark.django_db
def test_reservation_request_previews_and_creates_weekly_batch(client: Any) -> None:
    user = create_user("ui-grupo@example.com")
    room = create_room("Consultorio Grupo UI")
    doctor = create_tenant_doctor("doctor-grupo-ui@example.com")
    create_availability(room)
    create_rate(room)
    client.force_login(user)
    payload = {
        "room": str(room.pk),
        "tenant_doctor": str(doctor.pk),
        "date": "2026-06-29",
        "start_time": "08:00",
        "end_time": "13:00",
        "booking_type": "weekly",
        "recurrence_end_date": "2026-07-13",
        "notes": "Todos los lunes",
    }

    preview_response = client.post(
        "/reservaciones/solicitar/",
        {**payload, "action": "preview"},
    )

    preview_content = preview_response.content.decode()
    assert preview_response.status_code == 200
    assert "Previsualización" in preview_content
    assert "29/06/2026" in preview_content
    assert "06/07/2026" in preview_content
    assert "13/07/2026" in preview_content
    assert "1125.00 MXN" in preview_content
    assert not ReservationBatch.objects.filter(notes="Todos los lunes").exists()

    create_response = client.post(
        "/reservaciones/solicitar/",
        {**payload, "action": "create"},
    )

    batch = ReservationBatch.objects.get(notes="Todos los lunes")
    assert create_response.status_code == 302
    assert create_response.url == f"/reservaciones/grupos/{batch.pk}/"
    assert batch.reservations.count() == 3


@pytest.mark.django_db
def test_reservation_batch_detail_lists_occurrences(client: Any) -> None:
    user = create_user("detalle-grupo@example.com")
    room = create_room("Consultorio Detalle Grupo")
    doctor = create_tenant_doctor("doctor-detalle-grupo@example.com")
    create_availability(room)
    create_rate(room)
    batch = create_reservation_batch(
        room=room,
        tenant_doctor=doctor,
        start_date=date(2026, 6, 29),
        start_time=time(8, 0),
        end_time=time(13, 0),
        recurrence_end_date=date(2026, 7, 6),
    )
    client.force_login(user)

    response = client.get(f"/reservaciones/grupos/{batch.pk}/")

    content = response.content.decode()
    assert response.status_code == 200
    assert batch.reference in content
    assert "29/06/2026" in content
    assert "06/07/2026" in content
    assert "750.00 MXN" in content


@pytest.mark.django_db
def test_create_reservation_applies_best_discount_to_statement_and_reservation() -> (
    None
):
    room = create_room("Consultorio Descuento Guardado")
    doctor = create_tenant_doctor("doctor-descuento-guardado@example.com")
    create_availability(room)
    rule = create_rate(room)
    RoomRateDiscount.objects.create(
        room=room,
        rate_rule=rule,
        percentage=Decimal("10.0"),
        start_date=date(2026, 6, 29),
    )
    TenantDoctorDiscount.objects.create(
        tenant_doctor=doctor,
        percentage=Decimal("20.0"),
        start_date=date(2026, 6, 29),
    )

    reservation = create_reservation(
        room=room,
        tenant_doctor=doctor,
        reservation_date=date(2026, 6, 29),
        start_time=time(8, 0),
        end_time=time(13, 0),
    )

    statement = reservation.statements.get()
    reservation.refresh_from_db()
    assert statement.tariff_total == Decimal("375.00")
    assert statement.room_discount_percentage == Decimal("10.0")
    assert statement.tenant_discount_percentage == Decimal("20.0")
    assert statement.discounts == Decimal("75.00")
    assert statement.tariff_final == Decimal("300.00")
    assert statement.total_doctor == Decimal("300.00")
    assert statement.platform_commission == Decimal("30.00")
    assert statement.owner_net == Decimal("270.00")
    assert reservation.tariff_total == Decimal("375.00")
    assert reservation.tariff_final == Decimal("300.00")
    assert reservation.room_discount_percentage == Decimal("10.0")
    assert reservation.tenant_discount_percentage == Decimal("20.0")


@pytest.mark.django_db
def test_create_reservation_from_ui_sends_registration_email(
    client: Any,
    settings: Any,
) -> None:
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox = []
    user = create_user("ui-correo@example.com")
    room = create_room("Consultorio Correo")
    doctor = create_tenant_doctor("doctor-correo@example.com")
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
            "notes": "",
        },
    )

    assert response.status_code == 302
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.subject == "Consultorio 101, reservación registrada"
    assert set(message.to) == {doctor.user.email, room.owner.user.email}
    assert "Estimado Dr." in message.body
    assert "29/06/2026" in message.body
    assert "08:00 hasta las 13:00" in message.body
