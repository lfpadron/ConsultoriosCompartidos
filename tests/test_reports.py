from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.astrotrace.models import TraceEvent
from apps.astrotrace.services import record_event
from apps.finance.models import PaymentMethod, SettlementStatus
from apps.finance.services.payment_service import register_payment, validate_payment
from apps.finance.services.settlement_service import generate_settlement_for_reservation
from apps.reports.services.report_service import (
    get_documents_report,
    get_income_by_room_report,
    get_occupancy_report,
    get_payments_report,
    get_settlements_report,
    get_traceability_report,
)
from apps.vault.models import DocumentType
from apps.vault.services.document_service import upload_document
from tests.test_reservations import (
    create_availability,
    create_room,
    create_user,
    create_valid_reservation,
)
from tests.test_settlements import create_paid_reservation
from tests.test_vault import uploaded_file

REPORT_HTML_URLS = (
    "/reportes/ocupacion/",
    "/reportes/ingresos/",
    "/reportes/pagos/",
    "/reportes/liquidaciones/",
    "/reportes/documentos/",
    "/reportes/trazabilidad/",
)

REPORT_CSV_URLS = (
    ("/reportes/ocupacion.csv", "Clínica"),
    ("/reportes/ingresos.csv", "Subtotal reservado"),
    ("/reportes/pagos.csv", "Total estado de cuenta"),
    ("/reportes/liquidaciones.csv", "Estado liquidación"),
    ("/reportes/documentos.csv", "Hash abreviado"),
    ("/reportes/trazabilidad.csv", "Nivel"),
)


@pytest.mark.django_db
def test_occupancy_report_returns_expected_structure() -> None:
    room = create_room("Reporte Ocupación")
    create_availability(room)

    result = get_occupancy_report(
        {"date_from": date(2026, 6, 29), "date_to": date(2026, 6, 29)}
    )

    assert result.title == "Ocupación por consultorio"
    assert result.columns[0].label == "Clínica"
    assert result.rows[0]["consultorio"] == "Reporte Ocupación"
    assert result.rows[0]["bloques_disponibles"] == 1
    assert result.rows[0]["horas_disponibles"] == Decimal("5.00")


@pytest.mark.django_db
def test_income_report_sums_payments_and_balances() -> None:
    reservation = create_valid_reservation(room_name="Reporte Ingresos")
    payment = register_payment(
        reservation=reservation,
        amount=Decimal("100.00"),
        method=PaymentMethod.TRANSFER,
        reference="SPEI-REP-100",
    )
    validate_payment(payment=payment)

    result = get_income_by_room_report(
        {"date_from": date(2026, 6, 29), "date_to": date(2026, 6, 29)}
    )

    row = result.rows[0]
    assert row["subtotal_reservado"] == Decimal("375.00")
    assert row["pagos_validados"] == Decimal("100.00")
    assert row["saldo_pendiente"] == Decimal("275.00")


@pytest.mark.django_db
def test_payments_report_calculates_balance() -> None:
    reservation = create_valid_reservation(room_name="Reporte Pagos")
    payment = register_payment(
        reservation=reservation,
        amount=Decimal("100.00"),
        method=PaymentMethod.TRANSFER,
        reference="SPEI-REP-PAGO",
    )
    validate_payment(payment=payment)

    result = get_payments_report(
        {"date_from": date(2026, 6, 29), "date_to": date(2026, 6, 29)}
    )

    assert result.rows[0]["total_estado_cuenta"] == Decimal("375.00")
    assert result.rows[0]["pagado_validado"] == Decimal("100.00")
    assert result.rows[0]["saldo"] == Decimal("275.00")


@pytest.mark.django_db
def test_settlements_report_lists_statuses() -> None:
    reservation = create_paid_reservation("Reporte Liquidaciones")
    generate_settlement_for_reservation(reservation=reservation)

    result = get_settlements_report(
        {"date_from": date(2026, 6, 29), "date_to": date(2026, 7, 1)}
    )

    assert result.rows[0]["estado_liquidacion"] == "Calculada"
    assert result.rows[0]["neto_propietario"] == Decimal("337.50")


@pytest.mark.django_db
def test_documents_report_shows_abbreviated_hash() -> None:
    room = create_room("Reporte Documento")
    document = upload_document(
        title="Documento Reporte",
        document_type=DocumentType.CONTRACT,
        file=uploaded_file(content=b"report-doc"),
        owner=room.owner,
    )

    result = get_documents_report()

    assert result.rows[0]["hash_abreviado"] == document.sha256_hash[:12]
    assert result.rows[0]["documento"] == "Documento Reporte"


@pytest.mark.django_db
def test_traceability_report_orders_by_descending_date() -> None:
    older = record_event(event_type="report.old", object_label="Viejo")
    newer = record_event(event_type="report.new", object_label="Nuevo")
    TraceEvent.objects.filter(pk=older.pk).update(
        occurred_at=timezone.now() - timedelta(days=1)
    )
    TraceEvent.objects.filter(pk=newer.pk).update(occurred_at=timezone.now())

    result = get_traceability_report()

    assert result.rows[0]["objeto_relacionado"] == "Nuevo"
    assert result.rows[1]["objeto_relacionado"] == "Viejo"


@pytest.mark.django_db
def test_reports_index_responds_200(client: Any) -> None:
    user = create_user("reports-index@example.com")
    client.force_login(user)

    response = client.get("/reportes/")

    assert response.status_code == 200
    assert "Ocupación" in response.content.decode()


@pytest.mark.django_db
def test_each_report_html_responds_200(client: Any) -> None:
    user = create_user("reports-html@example.com")
    client.force_login(user)

    for url in REPORT_HTML_URLS:
        response = client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
def test_basic_filters_do_not_break_reports(client: Any) -> None:
    user = create_user("reports-filters@example.com")
    client.force_login(user)

    response = client.get("/reportes/pagos/?status=registrado&date_from=2026-06-29")

    assert response.status_code == 200


@pytest.mark.django_db
def test_report_table_contains_expected_headers(client: Any) -> None:
    user = create_user("reports-headers@example.com")
    client.force_login(user)

    response = client.get("/reportes/ingresos/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Clínica" in content
    assert "Consultorio" in content
    assert "Subtotal reservado" in content


@pytest.mark.django_db
def test_each_csv_endpoint_returns_csv_and_registers_trace_event(client: Any) -> None:
    user = create_user("reports-csv@example.com")
    client.force_login(user)

    for url, expected_header in REPORT_CSV_URLS:
        response = client.get(url)
        content = response.content.decode()

        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment;" in response["Content-Disposition"]
        assert expected_header in content

    assert TraceEvent.objects.filter(event_type="report.exported").count() == len(
        REPORT_CSV_URLS
    )


@pytest.mark.django_db
def test_csv_export_respects_filters(client: Any) -> None:
    user = create_user("reports-csv-filters@example.com")
    reservation = create_valid_reservation(room_name="Reporte CSV Filtrado")
    register_payment(
        reservation=reservation,
        amount=Decimal("100.00"),
        method=PaymentMethod.TRANSFER,
        reference="SPEI-FILTERED",
    )
    client.force_login(user)

    response = client.get("/reportes/pagos.csv?status=registrado")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Reporte CSV Filtrado" in content


@pytest.mark.django_db
def test_settlement_csv_contains_status_header(client: Any) -> None:
    user = create_user("reports-settlement-csv@example.com")
    reservation = create_paid_reservation("Reporte CSV Liquidación")
    settlement = generate_settlement_for_reservation(reservation=reservation)
    client.force_login(user)

    response = client.get(
        f"/reportes/liquidaciones.csv?status={SettlementStatus.CALCULATED}"
    )

    assert response.status_code == 200
    assert "Estado liquidación" in response.content.decode()
    assert str(settlement.room.name) in response.content.decode()
