from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.astrotrace.models import TraceEvent
from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    OwnerProfile,
    TenantDoctorProfile,
    TenantDoctorStatus,
)
from apps.finance.models import Payment, PriceType, RateRule, StatementStatus
from apps.identity.models import UserRole
from apps.scheduling.models import (
    AvailabilityRule,
    PaymentDeadlineException,
    PaymentDeadlineExceptionType,
    ReservationBatchStatus,
    ReservationDeadlinePolicy,
    ReservationPaymentPolicy,
    ReservationStatus,
    Weekday,
)
from apps.scheduling.services.deadline_service import (
    apply_hours_deadline_exception,
    calculate_payment_deadline,
    expire_overdue_reservation_batches,
    resolve_payment_policy,
)
from apps.scheduling.services.reservation_service import create_reservation_batch

MEXICO_CITY = ZoneInfo("America/Mexico_City")
RESERVATION_DATE = date(2026, 6, 29)


def create_user(email: str, role: str = UserRole.ADMIN) -> Any:
    return get_user_model().objects.create_user(
        email=email,
        password="Segura-12345",
        first_name="Plazos",
        last_name="Pruebas",
        role=role,
    )


def create_catalog(prefix: str) -> tuple[
    Clinic,
    ConsultingRoom,
    TenantDoctorProfile,
]:
    clinic = Clinic.objects.create(
        name=f"Clínica {prefix}",
        timezone="America/Mexico_City",
    )
    owner = OwnerProfile.objects.create(
        user=create_user(f"owner-{prefix.lower()}@example.com", UserRole.OWNER),
        display_name=f"Propietario {prefix}",
    )
    room = ConsultingRoom.objects.create(
        clinic=clinic,
        owner=owner,
        number="401",
        name=f"Consultorio {prefix}",
    )
    tenant = TenantDoctorProfile.objects.create(
        user=create_user(
            f"tenant-{prefix.lower()}@example.com",
            UserRole.TENANT_DOCTOR,
        ),
        display_name=f"Arrendatario {prefix}",
        status=TenantDoctorStatus.AUTHORIZED,
    )
    tenant.assigned_rooms.add(room)
    return clinic, room, tenant


def configure_reservation(room: ConsultingRoom) -> None:
    AvailabilityRule.objects.create(
        room=room,
        name="Lunes disponible",
        weekday=Weekday.MONDAY,
        start_time=time(8, 0),
        end_time=time(13, 0),
        start_date=RESERVATION_DATE,
    )
    RateRule.objects.create(
        room=room,
        name="Tarifa por hora",
        weekdays=[Weekday.MONDAY],
        start_time=time(8, 0),
        end_time=time(13, 0),
        start_date=RESERVATION_DATE,
        price_type=PriceType.HOURLY,
        amount=Decimal("100.00"),
        currency="MXN",
        priority=1,
    )


def create_policy(
    clinic: Clinic,
    *,
    room: ConsultingRoom | None = None,
    hours: int = 4,
    advance_rule_enabled: bool = True,
    automatic_cancellation: bool = True,
) -> ReservationPaymentPolicy:
    return ReservationPaymentPolicy.objects.create(
        clinic=clinic,
        room=room,
        hours_before_start=hours,
        advance_rule_enabled=advance_rule_enabled,
        automatic_cancellation=automatic_cancellation,
        start_date=date(2026, 1, 1),
    )


def local_datetime(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=MEXICO_CITY)


@pytest.mark.django_db
def test_deadline_uses_hours_rule_for_same_or_previous_day() -> None:
    clinic, room, _ = create_catalog("Horas")
    policy = create_policy(clinic, hours=4)

    quote = calculate_payment_deadline(
        room=room,
        first_reservation_date=RESERVATION_DATE,
        first_start_time=time(8, 0),
        requested_at=local_datetime(28, 10),
    )

    assert quote is not None
    assert quote.policy == policy
    assert quote.deadline_policy == ReservationDeadlinePolicy.HOURS_BEFORE
    assert quote.deadline_at == local_datetime(29, 4)
    assert quote.snapshot["advance_days"] == 1


@pytest.mark.django_db
def test_deadline_uses_previous_calendar_day_for_advance_booking() -> None:
    clinic, room, _ = create_catalog("Anticipada")
    create_policy(clinic, hours=6)

    quote = calculate_payment_deadline(
        room=room,
        first_reservation_date=RESERVATION_DATE,
        first_start_time=time(8, 0),
        requested_at=local_datetime(27, 12),
    )

    assert quote is not None
    assert quote.deadline_policy == ReservationDeadlinePolicy.PREVIOUS_DAY
    assert quote.deadline_at == local_datetime(28, 23, 59).replace(second=59)


@pytest.mark.django_db
def test_room_policy_overrides_clinic_default_and_can_disable_advance_rule() -> None:
    clinic, room, _ = create_catalog("Override")
    create_policy(clinic, hours=4)
    room_policy = create_policy(
        clinic,
        room=room,
        hours=10,
        advance_rule_enabled=False,
    )

    selected = resolve_payment_policy(room=room, at=local_datetime(27, 12))
    quote = calculate_payment_deadline(
        room=room,
        first_reservation_date=RESERVATION_DATE,
        first_start_time=time(8, 0),
        requested_at=local_datetime(27, 12),
    )

    assert selected == room_policy
    assert quote is not None
    assert quote.deadline_policy == ReservationDeadlinePolicy.HOURS_BEFORE
    assert quote.deadline_at == local_datetime(28, 22)


@pytest.mark.django_db
def test_batch_freezes_deadline_policy_snapshot() -> None:
    clinic, room, tenant = create_catalog("Snapshot")
    configure_reservation(room)
    policy = create_policy(clinic, hours=4)

    batch = create_reservation_batch(
        room=room,
        tenant_doctor=tenant,
        start_date=RESERVATION_DATE,
        start_time=time(8, 0),
        end_time=time(9, 0),
        requested_at=local_datetime(27, 12),
    )
    policy.is_active = False
    policy.save()
    batch.refresh_from_db()

    assert batch.deadline_policy == ReservationDeadlinePolicy.PREVIOUS_DAY
    assert batch.payment_deadline_at is not None
    assert batch.payment_deadline_at.astimezone(MEXICO_CITY) == local_datetime(
        28, 23, 59
    ).replace(second=59)
    assert batch.deadline_policy_snapshot["hours_before_start"] == 4
    assert batch.deadline_policy_snapshot["automatic_cancellation"] is True


@pytest.mark.django_db
def test_late_booking_requires_audited_admin_exception() -> None:
    clinic, room, tenant = create_catalog("Tardía")
    configure_reservation(room)
    create_policy(clinic, hours=4)
    tenant_actor = tenant.user

    with pytest.raises(ValidationError, match="plazo de pago ya venció"):
        create_reservation_batch(
            room=room,
            tenant_doctor=tenant,
            start_date=RESERVATION_DATE,
            start_time=time(8, 0),
            end_time=time(9, 0),
            requested_at=local_datetime(29, 6),
            actor=tenant_actor,
        )

    admin = create_user("admin-tardia@example.com")
    admin.assigned_clinics.add(clinic)
    batch = create_reservation_batch(
        room=room,
        tenant_doctor=tenant,
        start_date=RESERVATION_DATE,
        start_time=time(8, 0),
        end_time=time(9, 0),
        requested_at=local_datetime(29, 6),
        actor=admin,
        deadline_exception_reason="Ingreso autorizado por atención prioritaria.",
    )

    exception = PaymentDeadlineException.objects.get(batch=batch)
    assert batch.deadline_policy == ReservationDeadlinePolicy.ADMIN_OVERRIDE
    assert exception.exception_type == PaymentDeadlineExceptionType.LATE_BOOKING
    assert exception.authorized_by == admin
    assert TraceEvent.objects.filter(
        event_type="payment_deadline_exception.created",
        payload__batch_id=str(batch.pk),
    ).exists()


@pytest.mark.django_db
def test_admin_can_replace_previous_day_deadline_with_frozen_hours_rule() -> None:
    clinic, room, tenant = create_catalog("Excepción")
    configure_reservation(room)
    create_policy(clinic, hours=4)
    admin = create_user("admin-excepcion@example.com")
    admin.assigned_clinics.add(clinic)
    batch = create_reservation_batch(
        room=room,
        tenant_doctor=tenant,
        start_date=RESERVATION_DATE,
        start_time=time(8, 0),
        end_time=time(9, 0),
        requested_at=local_datetime(27, 12),
        actor=admin,
    )

    exception = apply_hours_deadline_exception(
        batch=batch,
        reason="Convenio individual aprobado.",
        actor=admin,
        applied_at=local_datetime(28, 12),
    )
    batch.refresh_from_db()

    assert exception.previous_deadline_at.astimezone(MEXICO_CITY) == local_datetime(
        28, 23, 59
    ).replace(second=59)
    assert exception.replacement_deadline_at.astimezone(MEXICO_CITY) == local_datetime(
        29, 4
    )
    assert batch.deadline_policy == ReservationDeadlinePolicy.EXCEPTION_HOURS
    assert batch.payment_deadline_at is not None
    assert batch.payment_deadline_at.astimezone(MEXICO_CITY) == local_datetime(29, 4)


@pytest.mark.django_db
def test_expiration_cancels_batch_releases_reservations_and_notifies() -> None:
    clinic, room, tenant = create_catalog("Vencimiento")
    configure_reservation(room)
    create_policy(clinic)
    admin = create_user("admin-vencimiento@example.com")
    admin.assigned_clinics.add(clinic)
    batch = create_reservation_batch(
        room=room,
        tenant_doctor=tenant,
        start_date=RESERVATION_DATE,
        start_time=time(8, 0),
        end_time=time(9, 0),
        requested_at=local_datetime(27, 12),
        actor=admin,
    )

    expired = expire_overdue_reservation_batches(
        as_of=local_datetime(29, 0),
    )
    second_run = expire_overdue_reservation_batches(
        as_of=local_datetime(29, 1),
    )
    batch.refresh_from_db()
    reservation = batch.reservations.get()

    assert expired == 1
    assert second_run == 0
    assert batch.status == ReservationBatchStatus.EXPIRED
    assert reservation.status == ReservationStatus.CANCELLED
    assert reservation.statements.get().status == StatementStatus.CANCELLED
    assert (
        TraceEvent.objects.filter(
            event_type="reservation_batch.expired",
            payload__id=str(batch.pk),
        ).count()
        == 1
    )
    assert tenant.user.email in mail.outbox[-1].to
    assert admin.email in mail.outbox[-1].to


@pytest.mark.django_db
def test_registered_payment_prevents_automatic_expiration() -> None:
    clinic, room, tenant = create_catalog("Pagada")
    configure_reservation(room)
    create_policy(clinic)
    batch = create_reservation_batch(
        room=room,
        tenant_doctor=tenant,
        start_date=RESERVATION_DATE,
        start_time=time(8, 0),
        end_time=time(9, 0),
        requested_at=local_datetime(27, 12),
    )
    reservation = batch.reservations.get()
    Payment.objects.create(
        reservation=reservation,
        statement=reservation.statements.get(),
        tenant_doctor=tenant,
        amount=reservation.tariff_final,
        currency="MXN",
        reference="COMPROBANTE-001",
        payment_date=RESERVATION_DATE,
    )

    expired = expire_overdue_reservation_batches(as_of=local_datetime(29, 0))
    batch.refresh_from_db()
    reservation.refresh_from_db()

    assert expired == 0
    assert batch.status == ReservationBatchStatus.REQUESTED
    assert reservation.status == ReservationStatus.REQUESTED


@pytest.mark.django_db
def test_policy_can_disable_automatic_cancellation() -> None:
    clinic, room, tenant = create_catalog("Sin cancelación")
    configure_reservation(room)
    create_policy(clinic, automatic_cancellation=False)
    batch = create_reservation_batch(
        room=room,
        tenant_doctor=tenant,
        start_date=RESERVATION_DATE,
        start_time=time(8, 0),
        end_time=time(9, 0),
        requested_at=local_datetime(27, 12),
    )

    expired = expire_overdue_reservation_batches(as_of=local_datetime(29, 0))
    batch.refresh_from_db()

    assert expired == 0
    assert batch.status == ReservationBatchStatus.REQUESTED
    assert batch.reservations.get().status == ReservationStatus.REQUESTED


@pytest.mark.django_db
def test_policy_screen_create_toggle_and_scope_are_audited(client: Any) -> None:
    clinic, room, _ = create_catalog("Pantalla")
    other_clinic, _, _ = create_catalog("Ajena")
    admin = create_user("admin-pantalla@example.com")
    admin.assigned_clinics.add(clinic)
    client.force_login(admin)

    response = client.post(
        reverse("reservation_payment_policy_create"),
        {
            "clinic": str(clinic.pk),
            "room": str(room.pk),
            "hours_before_start": "5",
            "advance_rule_enabled": "on",
            "automatic_cancellation": "on",
            "start_date": "2026-01-01",
            "end_date": "",
            "notes": "Política operativa",
        },
    )
    policy = ReservationPaymentPolicy.objects.get(room=room)
    list_response = client.get(reverse("payment_deadlines"))
    toggle_response = client.post(
        reverse("reservation_payment_policy_toggle", args=[policy.pk])
    )

    assert response.status_code == 302
    assert list_response.status_code == 200
    assert room.name in list_response.content.decode()
    assert other_clinic.name not in list_response.content.decode()
    assert toggle_response.status_code == 302
    policy.refresh_from_db()
    assert policy.is_active is False
    assert TraceEvent.objects.filter(
        event_type="reservation_payment_policy.created"
    ).exists()
    assert TraceEvent.objects.filter(
        event_type="reservation_payment_policy.deactivated"
    ).exists()

    operator = create_user("operator-plazos@example.com", UserRole.OPERATOR)
    client.force_login(operator)
    assert client.get(reverse("payment_deadlines")).status_code == 403


def test_deadline_expiration_task_is_scheduled() -> None:
    schedule = settings.CELERY_BEAT_SCHEDULE["expire-overdue-reservation-batches"]

    assert schedule["task"] == "scheduling.expire_overdue_reservation_batches"
    assert schedule["schedule"] == 300.0


@pytest.mark.django_db
def test_utc_requested_at_is_converted_to_clinic_timezone() -> None:
    clinic, room, _ = create_catalog("Zona")
    create_policy(clinic)
    requested_at = datetime(2026, 6, 28, 4, 30, tzinfo=UTC)
    quote = calculate_payment_deadline(
        room=room,
        first_reservation_date=RESERVATION_DATE,
        first_start_time=time(8, 0),
        requested_at=requested_at,
    )

    assert quote is not None
    assert quote.snapshot["advance_days"] == 2
    assert quote.deadline_policy == ReservationDeadlinePolicy.PREVIOUS_DAY
