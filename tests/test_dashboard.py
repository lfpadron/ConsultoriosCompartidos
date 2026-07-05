from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.astrotrace.models import TraceEvent
from apps.astrotrace.services import record_event
from apps.finance.models import PaymentMethod
from apps.finance.services.payment_service import register_payment, validate_payment
from apps.finance.services.settlement_service import generate_settlement_for_reservation
from apps.presentation.services.dashboard_service import (
    get_dashboard_alerts,
    get_dashboard_metrics,
    get_recent_trace_events,
)
from apps.scheduling.models import ReservationStatus
from apps.scheduling.services.reservation_service import confirm_reservation
from apps.vault.models import DocumentType
from apps.vault.services.document_service import (
    approve_document,
    mark_document_in_review,
    reject_document,
    upload_document,
)
from tests.test_reservations import create_room, create_user, create_valid_reservation
from tests.test_vault import uploaded_file


def alert_count(description: str) -> int:
    alerts = get_dashboard_alerts()
    return next(alert.count for alert in alerts if alert.description == description)


@pytest.mark.django_db
def test_dashboard_operation_metrics_count_correctly() -> None:
    requested = create_valid_reservation(room_name="Dashboard Operación Solicitada")
    pending = create_valid_reservation(room_name="Dashboard Operación Pendiente")
    paid = create_valid_reservation(room_name="Dashboard Operación Pagada")
    confirmed = create_valid_reservation(room_name="Dashboard Operación Confirmada")

    pending.status = ReservationStatus.PENDING_PAYMENT
    pending.save(update_fields=["status", "updated_at"])
    payment = register_payment(
        reservation=paid,
        amount=Decimal("375.00"),
        method=PaymentMethod.TRANSFER,
        reference="SPEI-DASH-OP",
    )
    validate_payment(payment=payment)
    confirm_reservation(reservation=confirmed)

    metrics = get_dashboard_metrics()

    assert requested.pk is not None
    assert metrics.operation["active_clinics"] == 4
    assert metrics.operation["active_rooms"] == 4
    assert metrics.operation["active_owners"] == 4
    assert metrics.operation["authorized_tenant_doctors"] == 4
    assert metrics.operation["active_reservations"] == 4
    assert metrics.operation["requested_reservations"] == 1
    assert metrics.operation["pending_payment_reservations"] == 1
    assert metrics.operation["paid_reservations"] == 1
    assert metrics.operation["confirmed_reservations"] == 1


@pytest.mark.django_db
def test_dashboard_finance_metrics_sum_correctly() -> None:
    partial = create_valid_reservation(room_name="Dashboard Finanzas Parcial")
    paid = create_valid_reservation(room_name="Dashboard Finanzas Pagada")
    partial_payment = register_payment(
        reservation=partial,
        amount=Decimal("100.00"),
        method=PaymentMethod.TRANSFER,
        reference="SPEI-DASH-100",
    )
    full_payment = register_payment(
        reservation=paid,
        amount=Decimal("375.00"),
        method=PaymentMethod.TRANSFER,
        reference="SPEI-DASH-375",
    )
    validate_payment(payment=partial_payment)
    validate_payment(payment=full_payment)
    paid.refresh_from_db()
    generate_settlement_for_reservation(reservation=paid)

    metrics = get_dashboard_metrics()

    assert metrics.finance["expected_reservation_total"] == Decimal("750.00")
    assert metrics.finance["validated_payment_total"] == Decimal("475.00")
    assert metrics.finance["pending_payment_balance"] == Decimal("275.00")
    assert metrics.finance["calculated_commissions"] == Decimal("75.00")
    assert metrics.finance["pending_owner_net"] == Decimal("337.50")
    assert metrics.finance["pending_settlements"] == 1
    assert metrics.finance["paid_settlements"] == 0


@pytest.mark.django_db
def test_dashboard_document_metrics_count_by_status() -> None:
    room = create_room("Dashboard Documento Métricas")
    received = upload_document(
        title="INE dashboard",
        document_type=DocumentType.INE,
        file=uploaded_file(content=b"dash-ine"),
        owner=room.owner,
    )
    in_review = upload_document(
        title="RFC dashboard",
        document_type=DocumentType.RFC,
        file=uploaded_file(content=b"dash-rfc"),
        owner=room.owner,
    )
    approved = upload_document(
        title="Contrato dashboard",
        document_type=DocumentType.CONTRACT,
        file=uploaded_file(content=b"dash-contrato"),
        owner=room.owner,
    )
    rejected = upload_document(
        title="Cedula dashboard",
        document_type=DocumentType.PROFESSIONAL_LICENSE,
        file=uploaded_file(content=b"dash-cedula"),
        owner=room.owner,
    )

    mark_document_in_review(document=in_review)
    approve_document(document=approved)
    reject_document(document=rejected, reason="No legible")

    metrics = get_dashboard_metrics()

    assert received.pk is not None
    assert metrics.documents["received_documents"] == 1
    assert metrics.documents["in_review_documents"] == 1
    assert metrics.documents["approved_documents"] == 1
    assert metrics.documents["rejected_documents"] == 1
    assert metrics.documents["legal_documents_pending_review"] == 2


@pytest.mark.django_db
def test_dashboard_trace_metrics_count_last_7_days() -> None:
    old_event = record_event(
        event_type="payment.registered",
        object_label="Pago viejo",
        payload={"level": "financiero"},
    )
    record_event(
        event_type="payment.registered",
        object_label="Pago reciente",
        payload={"level": "financiero"},
    )
    record_event(
        event_type="document.received",
        object_label="Documento reciente",
        payload={"level": "legal"},
    )
    TraceEvent.objects.filter(pk=old_event.pk).update(
        occurred_at=timezone.now() - timedelta(days=8)
    )

    metrics = get_dashboard_metrics()

    assert metrics.traceability["trace_events_last_7_days"] == 2
    assert metrics.traceability["financial_trace_events_last_7_days"] == 1
    assert metrics.traceability["legal_trace_events_last_7_days"] == 1


@pytest.mark.django_db
def test_dashboard_alerts_detect_pending_payments() -> None:
    reservation = create_valid_reservation(room_name="Dashboard Alerta Pago")
    register_payment(
        reservation=reservation,
        amount=Decimal("100.00"),
        method=PaymentMethod.TRANSFER,
        reference="SPEI-DASH-ALERT",
    )

    assert alert_count("Pagos registrados pendientes de validar") == 1


@pytest.mark.django_db
def test_dashboard_alerts_detect_documents_in_review() -> None:
    room = create_room("Dashboard Alerta Documento")
    document = upload_document(
        title="Documento en revisión",
        document_type=DocumentType.CONTRACT,
        file=uploaded_file(content=b"dash-review"),
        owner=room.owner,
    )
    mark_document_in_review(document=document)

    assert alert_count("Documentos en revisión") == 1


@pytest.mark.django_db
def test_dashboard_alerts_detect_rooms_without_availability() -> None:
    create_room("Dashboard Sin Disponibilidad")

    assert alert_count("Consultorios activos sin reglas de disponibilidad") == 1


@pytest.mark.django_db
def test_dashboard_alerts_detect_rooms_without_rates() -> None:
    create_room("Dashboard Sin Tarifa")

    assert alert_count("Consultorios activos sin reglas tarifarias") == 1


@pytest.mark.django_db
def test_recent_trace_events_respects_limit() -> None:
    for index in range(12):
        record_event(
            event_type="dashboard.event",
            object_label=f"Evento {index}",
            payload={"level": "operativo"},
        )

    items = get_recent_trace_events(limit=5)

    assert len(items) == 5


@pytest.mark.django_db
def test_dashboard_root_responds_200_for_authenticated_user(client: Any) -> None:
    user = create_user("dashboard-root@example.com")
    client.force_login(user)

    response = client.get("/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_dashboard_page_responds_200_for_authenticated_user(client: Any) -> None:
    user = create_user("dashboard-page@example.com")
    client.force_login(user)

    response = client.get("/dashboard/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_dashboard_shows_main_metric_cards(client: Any) -> None:
    user = create_user("dashboard-cards@example.com")
    client.force_login(user)

    response = client.get("/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Operación" in content
    assert "Finanzas" in content
    assert "Documentos" in content
    assert "Trazabilidad" in content
    assert "Clínicas activas" in content


@pytest.mark.django_db
def test_dashboard_shows_alerts(client: Any) -> None:
    user = create_user("dashboard-alerts@example.com")
    client.force_login(user)

    response = client.get("/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Alertas" in content
    assert "Pagos registrados pendientes de validar" in content


@pytest.mark.django_db
def test_dashboard_shows_recent_activity(client: Any) -> None:
    user = create_user("dashboard-activity@example.com")
    record_event(
        event_type="payment.registered",
        object_label="Pago dashboard",
        payload={"level": "financiero"},
    )
    client.force_login(user)

    response = client.get("/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Actividad reciente" in content
    assert "Financiero" in content or "financiero" in content


@pytest.mark.django_db
def test_dashboard_quick_links_exist(client: Any) -> None:
    user = create_user("dashboard-links@example.com")
    client.force_login(user)

    response = client.get("/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Enlaces rápidos" in content
    assert "Nueva clínica" in content
    assert "Nuevo consultorio" in content
    assert "Timeline" in content
