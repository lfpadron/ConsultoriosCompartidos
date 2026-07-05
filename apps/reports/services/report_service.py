"""Selectors for MVP operational, financial, document and trace reports."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Model, Q, QuerySet, Sum
from django.utils import timezone

from apps.astrotrace.models import TraceEvent
from apps.astrotrace.services.timeline_service import (
    LEVEL_CHOICES,
    build_timeline_item,
)
from apps.catalog.models import ConsultingRoom
from apps.finance.models import (
    Payment,
    PaymentStatus,
    Settlement,
    Statement,
    StatementStatus,
)
from apps.scheduling.models import ACTIVE_RESERVATION_STATUSES, Reservation
from apps.scheduling.services import (
    BLOCK_STATUS_FREE,
    BLOCK_STATUS_RESERVED_FUTURE,
    generate_availability_blocks,
)
from apps.vault.models import DocumentAsset
from apps.vault.services.document_service import entity_from_document


@dataclass(frozen=True)
class ReportColumn:
    key: str
    label: str


@dataclass(frozen=True)
class ReportResult:
    title: str
    columns: tuple[ReportColumn, ...]
    rows: list[dict[str, Any]]
    totals: dict[str, Any]


OCCUPANCY_COLUMNS = (
    ReportColumn("clinica", "Clínica"),
    ReportColumn("consultorio", "Consultorio"),
    ReportColumn("propietario", "Propietario"),
    ReportColumn("bloques_disponibles", "Bloques disponibles"),
    ReportColumn("reservaciones_activas", "Reservaciones activas"),
    ReportColumn("horas_disponibles", "Horas disponibles"),
    ReportColumn("horas_reservadas", "Horas reservadas"),
    ReportColumn("porcentaje_ocupacion", "Porcentaje ocupación"),
)

INCOME_COLUMNS = (
    ReportColumn("clinica", "Clínica"),
    ReportColumn("consultorio", "Consultorio"),
    ReportColumn("propietario", "Propietario"),
    ReportColumn("subtotal_reservado", "Subtotal reservado"),
    ReportColumn("pagos_validados", "Pagos validados"),
    ReportColumn("saldo_pendiente", "Saldo pendiente"),
    ReportColumn("comisiones_calculadas", "Comisiones calculadas"),
    ReportColumn("neto_propietario", "Neto propietario"),
)

PAYMENTS_COLUMNS = (
    ReportColumn("reservacion", "Reservación"),
    ReportColumn("medico_arrendatario", "Médico arrendatario"),
    ReportColumn("consultorio", "Consultorio"),
    ReportColumn("total_estado_cuenta", "Total estado de cuenta"),
    ReportColumn("pagado_validado", "Pagado validado"),
    ReportColumn("saldo", "Saldo"),
    ReportColumn("estado_reservacion", "Estado reservación"),
    ReportColumn("estado_pagos", "Estado pagos"),
)

SETTLEMENTS_COLUMNS = (
    ReportColumn("reservacion", "Reservación"),
    ReportColumn("propietario", "Propietario"),
    ReportColumn("consultorio", "Consultorio"),
    ReportColumn("subtotal", "Subtotal"),
    ReportColumn("comision", "Comisión"),
    ReportColumn("neto_propietario", "Neto propietario"),
    ReportColumn("estado_liquidacion", "Estado liquidación"),
    ReportColumn("referencia_pago", "Referencia pago"),
)

DOCUMENTS_COLUMNS = (
    ReportColumn("documento", "Documento"),
    ReportColumn("tipo", "Tipo"),
    ReportColumn("estado", "Estado"),
    ReportColumn("entidad_relacionada", "Entidad relacionada"),
    ReportColumn("version", "Versión"),
    ReportColumn("hash_abreviado", "Hash abreviado"),
    ReportColumn("fecha_recepcion", "Fecha recepción"),
    ReportColumn("revisado_por", "Revisado por"),
)

TRACEABILITY_COLUMNS = (
    ReportColumn("fecha", "Fecha"),
    ReportColumn("nivel", "Nivel"),
    ReportColumn("modulo", "Módulo"),
    ReportColumn("accion", "Acción"),
    ReportColumn("usuario", "Usuario"),
    ReportColumn("objeto_relacionado", "Objeto relacionado"),
    ReportColumn("descripcion", "Descripción corta"),
)


def get_occupancy_report(filters: dict[str, Any] | None = None) -> ReportResult:
    filters = filters or {}
    start_date, end_date = _availability_date_range(filters)
    rows: list[dict[str, Any]] = []
    total_available_blocks = 0
    total_active_reservations = 0
    total_available_hours = Decimal("0.00")
    total_reserved_hours = Decimal("0.00")

    for room in _filtered_rooms(filters):
        blocks = generate_availability_blocks(room, start_date, end_date)
        free_blocks = [block for block in blocks if block.status == BLOCK_STATUS_FREE]
        reserved_blocks = [
            block for block in blocks if block.status == BLOCK_STATUS_RESERVED_FUTURE
        ]
        available_hours = sum(
            (
                _duration_hours(block.start_time, block.end_time)
                for block in free_blocks
            ),
            Decimal("0.00"),
        )
        reserved_hours = sum(
            (
                _duration_hours(block.start_time, block.end_time)
                for block in reserved_blocks
            ),
            Decimal("0.00"),
        )
        active_reservations = _filtered_reservations(filters).filter(room=room).count()
        occupancy_percentage = _percentage(
            reserved_hours,
            available_hours + reserved_hours,
        )

        total_available_blocks += len(free_blocks)
        total_active_reservations += active_reservations
        total_available_hours += available_hours
        total_reserved_hours += reserved_hours
        rows.append(
            {
                "clinica": room.clinic.name,
                "consultorio": room.name,
                "propietario": str(room.owner) if room.owner else "Sin propietario",
                "bloques_disponibles": len(free_blocks),
                "reservaciones_activas": active_reservations,
                "horas_disponibles": available_hours,
                "horas_reservadas": reserved_hours,
                "porcentaje_ocupacion": occupancy_percentage,
            }
        )

    return ReportResult(
        title="Ocupación por consultorio",
        columns=OCCUPANCY_COLUMNS,
        rows=rows,
        totals={
            "bloques_disponibles": total_available_blocks,
            "reservaciones_activas": total_active_reservations,
            "horas_disponibles": total_available_hours,
            "horas_reservadas": total_reserved_hours,
            "porcentaje_ocupacion": _percentage(
                total_reserved_hours,
                total_available_hours + total_reserved_hours,
            ),
        },
    )


def get_income_by_room_report(filters: dict[str, Any] | None = None) -> ReportResult:
    filters = filters or {}
    rows: list[dict[str, Any]] = []
    totals = {
        "subtotal_reservado": Decimal("0.00"),
        "pagos_validados": Decimal("0.00"),
        "saldo_pendiente": Decimal("0.00"),
        "comisiones_calculadas": Decimal("0.00"),
        "neto_propietario": Decimal("0.00"),
    }

    reservations = _filtered_reservations(filters)
    for room in _filtered_rooms(filters):
        room_reservations = reservations.filter(room=room)
        statements = _current_statements().filter(reservation__in=room_reservations)
        subtotal = _decimal_sum(statements, "subtotal")
        validated_payments = _decimal_sum(
            Payment.objects.filter(
                reservation__in=room_reservations,
                status=PaymentStatus.VALIDATED,
                is_deleted=False,
            ),
            "amount",
        )
        balance = max(subtotal - validated_payments, Decimal("0.00"))
        commissions = _decimal_sum(statements, "platform_commission")
        owner_net = _decimal_sum(statements, "owner_net")

        totals["subtotal_reservado"] += subtotal
        totals["pagos_validados"] += validated_payments
        totals["saldo_pendiente"] += balance
        totals["comisiones_calculadas"] += commissions
        totals["neto_propietario"] += owner_net
        rows.append(
            {
                "clinica": room.clinic.name,
                "consultorio": room.name,
                "propietario": str(room.owner) if room.owner else "Sin propietario",
                "subtotal_reservado": subtotal,
                "pagos_validados": validated_payments,
                "saldo_pendiente": balance,
                "comisiones_calculadas": commissions,
                "neto_propietario": owner_net,
            }
        )

    return ReportResult(
        title="Ingresos por consultorio",
        columns=INCOME_COLUMNS,
        rows=rows,
        totals=totals,
    )


def get_payments_report(filters: dict[str, Any] | None = None) -> ReportResult:
    filters = filters or {}
    reservations = _filtered_reservations(filters).select_related(
        "room",
        "room__clinic",
        "tenant_doctor",
        "tenant_doctor__user",
    )
    tenant_doctor = filters.get("tenant_doctor")
    status = filters.get("status")
    if tenant_doctor:
        reservations = reservations.filter(tenant_doctor=tenant_doctor)
    if status:
        reservations = reservations.filter(
            payments__status=status,
            payments__is_deleted=False,
        ).distinct()

    rows: list[dict[str, Any]] = []
    totals = {
        "total_estado_cuenta": Decimal("0.00"),
        "pagado_validado": Decimal("0.00"),
        "saldo": Decimal("0.00"),
    }
    for reservation in reservations:
        statement = _current_statement_for_reservation(reservation)
        total = statement.total_doctor if statement is not None else Decimal("0.00")
        paid = _decimal_sum(
            Payment.objects.filter(
                reservation=reservation,
                status=PaymentStatus.VALIDATED,
                is_deleted=False,
            ),
            "amount",
        )
        balance = max(total - paid, Decimal("0.00"))
        totals["total_estado_cuenta"] += total
        totals["pagado_validado"] += paid
        totals["saldo"] += balance
        rows.append(
            {
                "reservacion": str(reservation),
                "medico_arrendatario": str(reservation.tenant_doctor),
                "consultorio": reservation.room.name,
                "total_estado_cuenta": total,
                "pagado_validado": paid,
                "saldo": balance,
                "estado_reservacion": reservation.get_status_display(),
                "estado_pagos": _payment_status_summary(reservation),
            }
        )

    return ReportResult(
        title="Pagos",
        columns=PAYMENTS_COLUMNS,
        rows=rows,
        totals=totals,
    )


def get_settlements_report(filters: dict[str, Any] | None = None) -> ReportResult:
    filters = filters or {}
    queryset = Settlement.objects.filter(is_deleted=False).select_related(
        "reservation",
        "owner",
        "owner__user",
        "room",
        "room__clinic",
    )
    queryset = _filter_by_room_scope(queryset, filters)
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    status = filters.get("status")
    if date_from:
        queryset = queryset.filter(reservation__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(reservation__date__lte=date_to)
    if status:
        queryset = queryset.filter(status=status)

    rows = [
        {
            "reservacion": str(settlement.reservation),
            "propietario": str(settlement.owner),
            "consultorio": settlement.room.name,
            "subtotal": settlement.reservation_subtotal,
            "comision": settlement.platform_commission,
            "neto_propietario": settlement.owner_net,
            "estado_liquidacion": settlement.get_status_display(),
            "referencia_pago": settlement.payment_reference,
        }
        for settlement in queryset.order_by("-generated_at")
    ]
    return ReportResult(
        title="Liquidaciones",
        columns=SETTLEMENTS_COLUMNS,
        rows=rows,
        totals={
            "subtotal": _decimal_sum(queryset, "reservation_subtotal"),
            "comision": _decimal_sum(queryset, "platform_commission"),
            "neto_propietario": _decimal_sum(queryset, "owner_net"),
        },
    )


def get_documents_report(filters: dict[str, Any] | None = None) -> ReportResult:
    filters = filters or {}
    queryset = DocumentAsset.objects.filter(is_deleted=False).select_related(
        "owner",
        "owner__user",
        "tenant_doctor",
        "tenant_doctor__user",
        "room",
        "room__clinic",
        "reservation",
        "payment",
        "settlement",
        "reviewed_by",
    )
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    clinic = filters.get("clinic")
    room = filters.get("room")
    owner = filters.get("owner")
    tenant_doctor = filters.get("tenant_doctor")
    status = filters.get("status")
    document_type = filters.get("document_type")

    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)
    if clinic:
        queryset = queryset.filter(
            Q(room__clinic=clinic)
            | Q(reservation__room__clinic=clinic)
            | Q(payment__reservation__room__clinic=clinic)
            | Q(settlement__room__clinic=clinic)
        )
    if room:
        queryset = queryset.filter(
            Q(room=room)
            | Q(reservation__room=room)
            | Q(payment__reservation__room=room)
            | Q(settlement__room=room)
        )
    if owner:
        queryset = queryset.filter(
            Q(owner=owner)
            | Q(room__owner=owner)
            | Q(reservation__room__owner=owner)
            | Q(payment__reservation__room__owner=owner)
            | Q(settlement__owner=owner)
        )
    if tenant_doctor:
        queryset = queryset.filter(
            Q(tenant_doctor=tenant_doctor)
            | Q(reservation__tenant_doctor=tenant_doctor)
            | Q(payment__tenant_doctor=tenant_doctor)
        )
    if status:
        queryset = queryset.filter(status=status)
    if document_type:
        queryset = queryset.filter(document_type=document_type)

    rows = []
    for document in queryset.order_by("-created_at", "title", "-version"):
        entity = entity_from_document(document)
        rows.append(
            {
                "documento": document.title,
                "tipo": document.get_document_type_display(),
                "estado": document.get_status_display(),
                "entidad_relacionada": (
                    f"{entity.label}: {entity.value}" if entity is not None else ""
                ),
                "version": document.version,
                "hash_abreviado": document.sha256_hash[:12],
                "fecha_recepcion": document.created_at,
                "revisado_por": (
                    document.reviewed_by.email if document.reviewed_by else ""
                ),
            }
        )

    return ReportResult(
        title="Documentos",
        columns=DOCUMENTS_COLUMNS,
        rows=rows,
        totals={"documentos": len(rows)},
    )


def get_traceability_report(filters: dict[str, Any] | None = None) -> ReportResult:
    filters = filters or {}
    queryset = TraceEvent.objects.filter(is_deleted=False).select_related("actor")
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    level = filters.get("level")
    if date_from:
        queryset = queryset.filter(occurred_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(occurred_at__date__lte=date_to)

    rows = []
    for event in queryset.order_by("-occurred_at"):
        item = build_timeline_item(event)
        if level and item.level != level:
            continue
        rows.append(
            {
                "fecha": event.occurred_at,
                "nivel": item.level,
                "modulo": item.module,
                "accion": item.action,
                "usuario": item.actor_label,
                "objeto_relacionado": item.related_object,
                "descripcion": item.description,
            }
        )

    return ReportResult(
        title="Trazabilidad",
        columns=TRACEABILITY_COLUMNS,
        rows=rows,
        totals={"eventos": len(rows)},
    )


def report_rows_for_display(result: ReportResult) -> list[list[str]]:
    return [
        [format_report_value(row.get(column.key, "")) for column in result.columns]
        for row in result.rows
    ]


def report_totals_for_display(result: ReportResult) -> list[dict[str, str]]:
    return [
        {"label": column.label, "value": format_report_value(result.totals[column.key])}
        for column in result.columns
        if column.key in result.totals
    ]


def format_report_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return timezone.localtime(value).strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def serialize_report_filters(filters: dict[str, Any]) -> dict[str, str]:
    serialized: dict[str, str] = {}
    for key, value in filters.items():
        if value in ("", None):
            continue
        if isinstance(value, Model):
            serialized[key] = str(value.pk)
        elif isinstance(value, date):
            serialized[key] = value.isoformat()
        else:
            serialized[key] = str(value)
    return serialized


def _filtered_rooms(filters: dict[str, Any]) -> QuerySet[ConsultingRoom]:
    queryset = ConsultingRoom.objects.filter(is_deleted=False).select_related(
        "clinic",
        "owner",
        "owner__user",
    )
    clinic = filters.get("clinic")
    room = filters.get("room")
    owner = filters.get("owner")
    if clinic:
        queryset = queryset.filter(clinic=clinic)
    if room:
        queryset = queryset.filter(pk=room.pk)
    if owner:
        queryset = queryset.filter(owner=owner)
    return queryset.order_by("clinic__name", "name")


def _filter_by_room_scope(
    queryset: QuerySet[Any],
    filters: dict[str, Any],
) -> QuerySet[Any]:
    clinic = filters.get("clinic")
    room = filters.get("room")
    owner = filters.get("owner")
    if clinic:
        queryset = queryset.filter(room__clinic=clinic)
    if room:
        queryset = queryset.filter(room=room)
    if owner:
        queryset = queryset.filter(room__owner=owner)
    return queryset


def _filtered_reservations(filters: dict[str, Any]) -> QuerySet[Reservation]:
    queryset = Reservation.objects.filter(
        status__in=ACTIVE_RESERVATION_STATUSES,
        is_deleted=False,
    ).select_related("room", "room__clinic", "room__owner", "tenant_doctor")
    clinic = filters.get("clinic")
    room = filters.get("room")
    owner = filters.get("owner")
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    if clinic:
        queryset = queryset.filter(room__clinic=clinic)
    if room:
        queryset = queryset.filter(room=room)
    if owner:
        queryset = queryset.filter(room__owner=owner)
    if date_from:
        queryset = queryset.filter(date__gte=date_from)
    if date_to:
        queryset = queryset.filter(date__lte=date_to)
    return queryset.order_by("-date", "-start_time")


def _current_statements() -> QuerySet[Statement]:
    return Statement.objects.filter(status=StatementStatus.CURRENT, is_deleted=False)


def _current_statement_for_reservation(reservation: Reservation) -> Statement | None:
    return (
        _current_statements()
        .filter(reservation=reservation)
        .order_by("-version")
        .first()
    )


def _payment_status_summary(reservation: Reservation) -> str:
    statuses = list(
        Payment.objects.filter(reservation=reservation, is_deleted=False)
        .order_by("status")
        .values_list("status", flat=True)
        .distinct()
    )
    if not statuses:
        return "Sin pagos"
    labels = dict(PaymentStatus.choices)
    return ", ".join(str(labels.get(status, status)) for status in statuses)


def _availability_date_range(filters: dict[str, Any]) -> tuple[date, date]:
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    if date_from and date_to:
        start_date = date_from
        end_date = date_to
    elif date_from:
        start_date = date_from
        end_date = start_date + timedelta(days=6)
    elif date_to:
        end_date = date_to
        start_date = end_date - timedelta(days=6)
    else:
        start_date = timezone.localdate()
        end_date = start_date + timedelta(days=6)
    if end_date < start_date:
        end_date = start_date
    return start_date, end_date


def _duration_hours(start_time: time, end_time: time) -> Decimal:
    start = datetime.combine(date.min, start_time)
    end = datetime.combine(date.min, end_time)
    seconds = Decimal(str((end - start).total_seconds()))
    return (seconds / Decimal("3600")).quantize(Decimal("0.01"))


def _percentage(numerator: Decimal, denominator: Decimal) -> str:
    if denominator <= Decimal("0.00"):
        return "0.00%"
    value = (numerator / denominator * Decimal("100")).quantize(Decimal("0.01"))
    return f"{value}%"


def _decimal_sum(queryset: QuerySet[Any], field_name: str) -> Decimal:
    return queryset.aggregate(total=Sum(field_name))["total"] or Decimal("0.00")


def level_filter_choices() -> tuple[tuple[str, str], ...]:
    return LEVEL_CHOICES
