from datetime import datetime, timedelta
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.astrotrace.models import TraceEvent
from apps.astrotrace.services.timeline_service import (
    get_timeline_for_consulting_room,
    get_timeline_for_reservation,
)
from apps.integration.models import AccessCredential, AccessCredentialStatus
from apps.integration.services.access_simulator import (
    expire_old_credentials,
    get_access_status_for_reservation,
    provision_access_for_reservation,
    revoke_access_credential,
    simulate_access_use,
)
from apps.scheduling.services.reservation_service import confirm_reservation
from tests.test_reservations import create_user, create_valid_reservation


def confirmed_reservation(room_name: str = "Consultorio Acceso") -> Any:
    reservation = create_valid_reservation(room_name=room_name)
    confirm_reservation(reservation=reservation)
    reservation.refresh_from_db()
    return reservation


def direct_credential(reservation: Any, code: str = "ACC-TEST-000001") -> Any:
    now = timezone.now()
    return AccessCredential.objects.create(
        reservation=reservation,
        tenant_doctor=reservation.tenant_doctor,
        room=reservation.room,
        status=AccessCredentialStatus.ENABLED,
        simulated_code=code,
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(minutes=1),
        enabled_at=now,
    )


@pytest.mark.django_db
def test_create_credential_for_confirmed_reservation() -> None:
    reservation = confirmed_reservation("Consultorio Credencial Confirmada")

    credential = direct_credential(reservation)

    assert credential.status == AccessCredentialStatus.ENABLED
    assert credential.reservation == reservation


@pytest.mark.django_db
def test_reject_credential_for_unconfirmed_reservation() -> None:
    reservation = create_valid_reservation(room_name="Consultorio Acceso Rechazado")
    now = timezone.now()

    with pytest.raises(ValidationError):
        AccessCredential.objects.create(
            reservation=reservation,
            tenant_doctor=reservation.tenant_doctor,
            room=reservation.room,
            status=AccessCredentialStatus.ENABLED,
            simulated_code="ACC-TEST-UNCONF",
            valid_from=now - timedelta(minutes=1),
            valid_until=now + timedelta(minutes=1),
            enabled_at=now,
        )


@pytest.mark.django_db
def test_reject_duplicate_active_credential_for_reservation() -> None:
    reservation = confirmed_reservation("Consultorio Acceso Duplicado")
    direct_credential(reservation, code="ACC-TEST-DUP-1")

    with pytest.raises(ValidationError):
        direct_credential(reservation, code="ACC-TEST-DUP-2")


@pytest.mark.django_db
def test_access_window_is_15_minutes_before_and_after_reservation() -> None:
    reservation = confirmed_reservation("Consultorio Acceso Ventana")
    credential = provision_access_for_reservation(reservation)
    current_timezone = timezone.get_current_timezone()
    expected_from = timezone.make_aware(
        datetime.combine(reservation.date, reservation.start_time),
        current_timezone,
    ) - timedelta(minutes=15)
    expected_until = timezone.make_aware(
        datetime.combine(reservation.date, reservation.end_time),
        current_timezone,
    ) + timedelta(minutes=15)

    assert credential.valid_from == expected_from
    assert credential.valid_until == expected_until


@pytest.mark.django_db
def test_provision_access_creates_enabled_credential() -> None:
    reservation = confirmed_reservation("Consultorio Acceso Provisionar")

    credential = provision_access_for_reservation(reservation)

    assert credential.status == AccessCredentialStatus.ENABLED
    assert credential.simulated_code.startswith("ACC-")
    assert TraceEvent.objects.filter(event_type="access.provisioned").exists()


@pytest.mark.django_db
def test_simulate_access_use_marks_used_inside_window() -> None:
    reservation = confirmed_reservation("Consultorio Acceso Usar")
    credential = provision_access_for_reservation(reservation)
    now = timezone.now()
    credential.valid_from = now - timedelta(minutes=5)
    credential.valid_until = now + timedelta(minutes=5)
    credential.save(update_fields=["valid_from", "valid_until", "updated_at"])

    simulate_access_use(credential)
    credential.refresh_from_db()

    assert credential.status == AccessCredentialStatus.USED
    assert credential.used_at is not None
    assert TraceEvent.objects.filter(event_type="access.used").exists()


@pytest.mark.django_db
def test_simulate_access_use_rejects_outside_window() -> None:
    reservation = confirmed_reservation("Consultorio Acceso Fuera Ventana")
    credential = provision_access_for_reservation(reservation)
    now = timezone.now()
    credential.valid_from = now + timedelta(hours=1)
    credential.valid_until = now + timedelta(hours=2)
    credential.save(update_fields=["valid_from", "valid_until", "updated_at"])

    with pytest.raises(ValidationError):
        simulate_access_use(credential)


@pytest.mark.django_db
def test_revoke_access_requires_reason() -> None:
    reservation = confirmed_reservation("Consultorio Acceso Motivo")
    credential = provision_access_for_reservation(reservation)

    with pytest.raises(ValidationError):
        revoke_access_credential(credential, reason="")


@pytest.mark.django_db
def test_expire_old_credentials_marks_expired() -> None:
    reservation = confirmed_reservation("Consultorio Acceso Expirar")
    credential = provision_access_for_reservation(reservation)

    count = expire_old_credentials(now=credential.valid_until + timedelta(minutes=1))
    credential.refresh_from_db()

    assert count == 1
    assert credential.status == AccessCredentialStatus.EXPIRED
    assert TraceEvent.objects.filter(event_type="access.expired").exists()


@pytest.mark.django_db
def test_get_access_status_for_reservation_returns_expected_status() -> None:
    reservation = confirmed_reservation("Consultorio Acceso Estado")
    credential = provision_access_for_reservation(reservation)

    status = get_access_status_for_reservation(reservation)

    assert status.credential == credential
    assert status.status_label == "Habilitada"
    assert status.can_provision is False


@pytest.mark.django_db
def test_reservation_detail_shows_enable_button_when_confirmed(client: Any) -> None:
    user = create_user("access-detail@example.com")
    reservation = confirmed_reservation("Consultorio Acceso Detalle")
    client.force_login(user)

    response = client.get(f"/reservaciones/{reservation.pk}/")

    assert response.status_code == 200
    assert "Habilitar acceso simulado" in response.content.decode()


@pytest.mark.django_db
def test_access_list_responds_200(client: Any) -> None:
    user = create_user("access-list@example.com")
    client.force_login(user)

    response = client.get("/integraciones/accesos/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_provision_access_from_ui(client: Any) -> None:
    user = create_user("access-provision-ui@example.com")
    reservation = confirmed_reservation("Consultorio Acceso UI Provisionar")
    client.force_login(user)

    response = client.post(f"/reservaciones/{reservation.pk}/acceso/habilitar/")

    assert response.status_code == 302
    assert AccessCredential.objects.filter(reservation=reservation).exists()


@pytest.mark.django_db
def test_simulate_access_use_from_ui(client: Any) -> None:
    user = create_user("access-use-ui@example.com")
    reservation = confirmed_reservation("Consultorio Acceso UI Usar")
    credential = provision_access_for_reservation(reservation)
    now = timezone.now()
    credential.valid_from = now - timedelta(minutes=5)
    credential.valid_until = now + timedelta(minutes=5)
    credential.save(update_fields=["valid_from", "valid_until", "updated_at"])
    client.force_login(user)

    response = client.post(f"/accesos/{credential.pk}/usar/")
    credential.refresh_from_db()

    assert response.status_code == 302
    assert credential.status == AccessCredentialStatus.USED


@pytest.mark.django_db
def test_revoke_access_from_ui(client: Any) -> None:
    user = create_user("access-revoke-ui@example.com")
    reservation = confirmed_reservation("Consultorio Acceso UI Revocar")
    credential = provision_access_for_reservation(reservation)
    client.force_login(user)

    response = client.post(
        f"/accesos/{credential.pk}/revocar/",
        {"reason": "Prueba UI"},
    )
    credential.refresh_from_db()

    assert response.status_code == 302
    assert credential.status == AccessCredentialStatus.REVOKED


@pytest.mark.django_db
def test_revoke_access_generates_trace_event() -> None:
    reservation = confirmed_reservation("Consultorio Acceso Evento Revocar")
    credential = provision_access_for_reservation(reservation)

    revoke_access_credential(credential, reason="Prueba evento")

    assert TraceEvent.objects.filter(event_type="access.revoked").exists()


@pytest.mark.django_db
def test_reservation_timeline_includes_access_events() -> None:
    reservation = confirmed_reservation("Consultorio Acceso Timeline Reserva")
    provision_access_for_reservation(reservation)

    items = get_timeline_for_reservation(reservation)

    assert "access.provisioned" in {item.event.event_type for item in items}


@pytest.mark.django_db
def test_room_timeline_includes_access_events() -> None:
    reservation = confirmed_reservation("Consultorio Acceso Timeline Room")
    provision_access_for_reservation(reservation)

    items = get_timeline_for_consulting_room(reservation.room)

    assert "access.provisioned" in {item.event.event_type for item in items}
