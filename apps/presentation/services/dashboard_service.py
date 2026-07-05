"""Dashboard metrics and alerts selectors."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from django.db.models import Count, Q, QuerySet, Sum
from django.urls import reverse
from django.utils import timezone

from apps.astrotrace.models import TraceEvent
from apps.astrotrace.services.timeline_service import (
    LEVEL_FINANCIAL,
    LEVEL_LEGAL,
    TimelineItem,
    build_timeline_item,
)
from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    OwnerProfile,
    TenantDoctorProfile,
    TenantDoctorStatus,
)
from apps.finance.models import (
    Payment,
    PaymentStatus,
    Settlement,
    SettlementStatus,
    Statement,
    StatementStatus,
)
from apps.scheduling.models import (
    ACTIVE_RESERVATION_STATUSES,
    Reservation,
    ReservationStatus,
)
from apps.vault.models import DocumentAsset, DocumentStatus, DocumentType

LEGAL_DOCUMENT_TYPES = (
    DocumentType.CONTRACT,
    DocumentType.INE,
    DocumentType.RFC,
    DocumentType.PROFESSIONAL_LICENSE,
    DocumentType.ADDRESS_PROOF,
)


@dataclass(frozen=True)
class MetricCard:
    key: str
    label: str
    value: int | Decimal
    display_value: str
    icon: str
    variant: str
    url: str = ""


@dataclass(frozen=True)
class MetricSection:
    title: str
    icon: str
    metrics: tuple[MetricCard, ...]


@dataclass(frozen=True)
class DashboardMetrics:
    operation: dict[str, int]
    finance: dict[str, int | Decimal]
    documents: dict[str, int]
    traceability: dict[str, int]
    sections: tuple[MetricSection, ...]


@dataclass(frozen=True)
class DashboardAlert:
    alert_type: str
    description: str
    count: int
    url: str
    icon: str
    variant: str


def get_dashboard_metrics() -> DashboardMetrics:
    operation = _operation_metrics()
    finance = _finance_metrics()
    documents = _document_metrics()
    traceability = _traceability_metrics()

    sections = (
        MetricSection(
            title="Operación",
            icon="bi-activity",
            metrics=(
                _count_metric(
                    "active_clinics",
                    "Clínicas activas",
                    operation["active_clinics"],
                    "bi-hospital",
                    "primary",
                    "clinics",
                ),
                _count_metric(
                    "active_rooms",
                    "Consultorios activos",
                    operation["active_rooms"],
                    "bi-door-open",
                    "primary",
                    "rooms",
                ),
                _count_metric(
                    "active_owners",
                    "Médicos propietarios activos",
                    operation["active_owners"],
                    "bi-person-badge",
                    "primary",
                    "owners",
                ),
                _count_metric(
                    "authorized_tenant_doctors",
                    "Médicos arrendatarios autorizados",
                    operation["authorized_tenant_doctors"],
                    "bi-person-vcard",
                    "primary",
                    "tenant_doctors",
                ),
                _count_metric(
                    "active_reservations",
                    "Reservaciones activas",
                    operation["active_reservations"],
                    "bi-calendar-check",
                    "primary",
                    "reservations",
                ),
                _count_metric(
                    "requested_reservations",
                    "Reservaciones solicitadas",
                    operation["requested_reservations"],
                    "bi-hourglass-split",
                    "primary",
                    "reservations",
                ),
                _count_metric(
                    "pending_payment_reservations",
                    "Reservaciones pendientes de pago",
                    operation["pending_payment_reservations"],
                    "bi-wallet2",
                    "primary",
                    "reservations",
                ),
                _count_metric(
                    "paid_reservations",
                    "Reservaciones pagadas",
                    operation["paid_reservations"],
                    "bi-check2-circle",
                    "primary",
                    "reservations",
                ),
                _count_metric(
                    "confirmed_reservations",
                    "Reservaciones confirmadas",
                    operation["confirmed_reservations"],
                    "bi-calendar2-check",
                    "primary",
                    "reservations",
                ),
            ),
        ),
        MetricSection(
            title="Finanzas",
            icon="bi-cash-coin",
            metrics=(
                _money_metric(
                    "expected_reservation_total",
                    "Total esperado por reservaciones vigentes",
                    finance["expected_reservation_total"],
                    "bi-receipt",
                    "warning",
                    "reservations",
                ),
                _money_metric(
                    "validated_payment_total",
                    "Total validado en pagos",
                    finance["validated_payment_total"],
                    "bi-credit-card-2-front",
                    "warning",
                    "payments",
                ),
                _money_metric(
                    "pending_payment_balance",
                    "Saldo pendiente de pago",
                    finance["pending_payment_balance"],
                    "bi-exclamation-circle",
                    "warning",
                    "payments",
                ),
                _money_metric(
                    "calculated_commissions",
                    "Comisiones calculadas",
                    finance["calculated_commissions"],
                    "bi-percent",
                    "warning",
                    "rates",
                ),
                _money_metric(
                    "pending_owner_net",
                    "Neto propietario pendiente de liquidar",
                    finance["pending_owner_net"],
                    "bi-bank",
                    "warning",
                    "settlements",
                ),
                _count_metric(
                    "pending_settlements",
                    "Liquidaciones pendientes",
                    int(finance["pending_settlements"]),
                    "bi-clock-history",
                    "warning",
                    "settlements",
                ),
                _count_metric(
                    "paid_settlements",
                    "Liquidaciones pagadas",
                    int(finance["paid_settlements"]),
                    "bi-patch-check",
                    "warning",
                    "settlements",
                ),
            ),
        ),
        MetricSection(
            title="Documentos",
            icon="bi-file-earmark-text",
            metrics=(
                _count_metric(
                    "received_documents",
                    "Documentos recibidos",
                    documents["received_documents"],
                    "bi-inbox",
                    "success",
                    "documents",
                ),
                _count_metric(
                    "in_review_documents",
                    "Documentos en revisión",
                    documents["in_review_documents"],
                    "bi-search",
                    "success",
                    "documents",
                ),
                _count_metric(
                    "approved_documents",
                    "Documentos aprobados",
                    documents["approved_documents"],
                    "bi-check2-square",
                    "success",
                    "documents",
                ),
                _count_metric(
                    "rejected_documents",
                    "Documentos rechazados",
                    documents["rejected_documents"],
                    "bi-x-octagon",
                    "success",
                    "documents",
                ),
                _count_metric(
                    "legal_documents_pending_review",
                    "Documentos legales pendientes de revisión",
                    documents["legal_documents_pending_review"],
                    "bi-shield-exclamation",
                    "success",
                    "documents",
                ),
            ),
        ),
        MetricSection(
            title="Trazabilidad",
            icon="bi-diagram-3",
            metrics=(
                _count_metric(
                    "trace_events_last_7_days",
                    "Eventos AstroTrace últimos 7 días",
                    traceability["trace_events_last_7_days"],
                    "bi-diagram-2",
                    "danger",
                    "timeline",
                ),
                _count_metric(
                    "legal_trace_events_last_7_days",
                    "Eventos legales últimos 7 días",
                    traceability["legal_trace_events_last_7_days"],
                    "bi-shield-check",
                    "danger",
                    "timeline",
                ),
                _count_metric(
                    "financial_trace_events_last_7_days",
                    "Eventos financieros últimos 7 días",
                    traceability["financial_trace_events_last_7_days"],
                    "bi-cash-stack",
                    "danger",
                    "timeline",
                ),
            ),
        ),
    )

    return DashboardMetrics(
        operation=operation,
        finance=finance,
        documents=documents,
        traceability=traceability,
        sections=sections,
    )


def get_dashboard_alerts() -> list[DashboardAlert]:
    return [
        DashboardAlert(
            alert_type="Reservaciones",
            description="Reservaciones solicitadas sin pago validado",
            count=_requested_reservations_without_payment(),
            url=reverse("reservations"),
            icon="bi-hourglass-split",
            variant="primary",
        ),
        DashboardAlert(
            alert_type="Pagos",
            description="Pagos registrados pendientes de validar",
            count=Payment.objects.filter(
                status=PaymentStatus.REGISTERED,
                is_deleted=False,
            ).count(),
            url=_url("payments", {"status": PaymentStatus.REGISTERED}),
            icon="bi-credit-card",
            variant="warning",
        ),
        DashboardAlert(
            alert_type="Documentos",
            description="Documentos en revisión",
            count=DocumentAsset.objects.filter(
                status=DocumentStatus.IN_REVIEW,
                is_deleted=False,
            ).count(),
            url=_url("documents", {"status": DocumentStatus.IN_REVIEW}),
            icon="bi-search",
            variant="success",
        ),
        DashboardAlert(
            alert_type="Legal",
            description="Documentos legales recibidos sin aprobar",
            count=_legal_documents_without_approval(),
            url=_url("documents", {"document_type": DocumentType.CONTRACT}),
            icon="bi-shield-exclamation",
            variant="danger",
        ),
        DashboardAlert(
            alert_type="Liquidaciones",
            description="Liquidaciones calculadas pendientes de pago",
            count=Settlement.objects.filter(
                status=SettlementStatus.CALCULATED,
                is_deleted=False,
            ).count(),
            url=_url("settlements", {"status": SettlementStatus.CALCULATED}),
            icon="bi-bank",
            variant="warning",
        ),
        DashboardAlert(
            alert_type="Disponibilidad",
            description="Consultorios activos sin reglas de disponibilidad",
            count=_active_rooms_without_availability(),
            url=reverse("rooms"),
            icon="bi-calendar-week",
            variant="primary",
        ),
        DashboardAlert(
            alert_type="Tarifas",
            description="Consultorios activos sin reglas tarifarias",
            count=_active_rooms_without_rates(),
            url=reverse("rates"),
            icon="bi-cash-coin",
            variant="warning",
        ),
        DashboardAlert(
            alert_type="Reservaciones",
            description="Reservaciones pagadas pero no confirmadas",
            count=Reservation.objects.filter(
                status=ReservationStatus.PAID,
                is_deleted=False,
            ).count(),
            url=reverse("reservations"),
            icon="bi-calendar2-check",
            variant="primary",
        ),
    ]


def get_recent_trace_events(limit: int = 10) -> list[TimelineItem]:
    events = TraceEvent.objects.filter(is_deleted=False).select_related("actor")[:limit]
    return [build_timeline_item(event) for event in events]


def _operation_metrics() -> dict[str, int]:
    return {
        "active_clinics": Clinic.objects.filter(
            is_active=True,
            is_deleted=False,
        ).count(),
        "active_rooms": ConsultingRoom.objects.filter(
            is_active=True,
            is_deleted=False,
        ).count(),
        "active_owners": OwnerProfile.objects.filter(
            is_active=True,
            is_deleted=False,
        ).count(),
        "authorized_tenant_doctors": TenantDoctorProfile.objects.filter(
            status=TenantDoctorStatus.AUTHORIZED,
            is_active=True,
            is_deleted=False,
        ).count(),
        "active_reservations": Reservation.objects.filter(
            status__in=ACTIVE_RESERVATION_STATUSES,
            is_deleted=False,
        ).count(),
        "requested_reservations": Reservation.objects.filter(
            status=ReservationStatus.REQUESTED,
            is_deleted=False,
        ).count(),
        "pending_payment_reservations": Reservation.objects.filter(
            status=ReservationStatus.PENDING_PAYMENT,
            is_deleted=False,
        ).count(),
        "paid_reservations": Reservation.objects.filter(
            status=ReservationStatus.PAID,
            is_deleted=False,
        ).count(),
        "confirmed_reservations": Reservation.objects.filter(
            status=ReservationStatus.CONFIRMED,
            is_deleted=False,
        ).count(),
    }


def _finance_metrics() -> dict[str, int | Decimal]:
    active_statements = _current_statements().filter(
        reservation__status__in=ACTIVE_RESERVATION_STATUSES,
    )
    payable_statements = active_statements.filter(
        reservation__status__in=(
            ReservationStatus.PAID,
            ReservationStatus.CONFIRMED,
        ),
    )
    active_validated_payments = Payment.objects.filter(
        status=PaymentStatus.VALIDATED,
        reservation__status__in=ACTIVE_RESERVATION_STATUSES,
        is_deleted=False,
        reservation__is_deleted=False,
    )

    expected_reservation_total = _decimal_sum(active_statements, "total_doctor")
    validated_payment_total = _decimal_sum(active_validated_payments, "amount")
    calculated_commissions = _decimal_sum(
        active_statements,
        "platform_commission",
    )
    payable_owner_net = _decimal_sum(payable_statements, "owner_net")
    paid_owner_net = _decimal_sum(
        Settlement.objects.filter(
            status=SettlementStatus.PAID,
            is_deleted=False,
        ),
        "owner_net",
    )

    return {
        "expected_reservation_total": expected_reservation_total,
        "validated_payment_total": validated_payment_total,
        "pending_payment_balance": max(
            expected_reservation_total - validated_payment_total,
            Decimal("0.00"),
        ),
        "calculated_commissions": calculated_commissions,
        "pending_owner_net": max(payable_owner_net - paid_owner_net, Decimal("0.00")),
        "pending_settlements": Settlement.objects.filter(
            status__in=(SettlementStatus.PENDING, SettlementStatus.CALCULATED),
            is_deleted=False,
        ).count(),
        "paid_settlements": Settlement.objects.filter(
            status=SettlementStatus.PAID,
            is_deleted=False,
        ).count(),
    }


def _document_metrics() -> dict[str, int]:
    documents = DocumentAsset.objects.filter(is_deleted=False)
    return {
        "received_documents": documents.filter(status=DocumentStatus.RECEIVED).count(),
        "in_review_documents": documents.filter(
            status=DocumentStatus.IN_REVIEW,
        ).count(),
        "approved_documents": documents.filter(status=DocumentStatus.APPROVED).count(),
        "rejected_documents": documents.filter(status=DocumentStatus.REJECTED).count(),
        "legal_documents_pending_review": documents.filter(
            document_type__in=LEGAL_DOCUMENT_TYPES,
            status__in=(DocumentStatus.RECEIVED, DocumentStatus.IN_REVIEW),
        ).count(),
    }


def _traceability_metrics() -> dict[str, int]:
    since = timezone.now() - timedelta(days=7)
    events = list(
        TraceEvent.objects.filter(
            occurred_at__gte=since,
            is_deleted=False,
        ).select_related("actor")
    )
    timeline_items = [build_timeline_item(event) for event in events]
    return {
        "trace_events_last_7_days": len(timeline_items),
        "legal_trace_events_last_7_days": sum(
            1 for item in timeline_items if item.level == LEVEL_LEGAL
        ),
        "financial_trace_events_last_7_days": sum(
            1 for item in timeline_items if item.level == LEVEL_FINANCIAL
        ),
    }


def _current_statements() -> QuerySet[Statement]:
    return Statement.objects.filter(
        status=StatementStatus.CURRENT,
        is_deleted=False,
        reservation__is_deleted=False,
    )


def _requested_reservations_without_payment() -> int:
    return (
        Reservation.objects.filter(
            status=ReservationStatus.REQUESTED,
            is_deleted=False,
        )
        .exclude(
            payments__status=PaymentStatus.VALIDATED,
            payments__is_deleted=False,
        )
        .distinct()
        .count()
    )


def _legal_documents_without_approval() -> int:
    return DocumentAsset.objects.filter(
        document_type__in=LEGAL_DOCUMENT_TYPES,
        status__in=(DocumentStatus.RECEIVED, DocumentStatus.IN_REVIEW),
        is_deleted=False,
    ).count()


def _active_rooms_without_availability() -> int:
    return (
        _active_rooms()
        .annotate(
            active_availability_rules=Count(
                "availability_rules",
                filter=Q(
                    availability_rules__is_active=True,
                    availability_rules__is_deleted=False,
                ),
            )
        )
        .filter(active_availability_rules=0)
        .count()
    )


def _active_rooms_without_rates() -> int:
    return (
        _active_rooms()
        .annotate(
            active_rate_rules=Count(
                "rate_rules",
                filter=Q(
                    rate_rules__is_active=True,
                    rate_rules__is_deleted=False,
                ),
            )
        )
        .filter(active_rate_rules=0)
        .count()
    )


def _active_rooms() -> QuerySet[ConsultingRoom]:
    return ConsultingRoom.objects.filter(is_active=True, is_deleted=False)


def _decimal_sum(queryset: QuerySet[Any], field_name: str) -> Decimal:
    return queryset.aggregate(total=Sum(field_name))["total"] or Decimal("0.00")


def _count_metric(
    key: str,
    label: str,
    value: int,
    icon: str,
    variant: str,
    url_name: str,
) -> MetricCard:
    return MetricCard(
        key=key,
        label=label,
        value=value,
        display_value=f"{value:,}",
        icon=icon,
        variant=variant,
        url=reverse(url_name),
    )


def _money_metric(
    key: str,
    label: str,
    value: int | Decimal,
    icon: str,
    variant: str,
    url_name: str,
) -> MetricCard:
    decimal_value = Decimal(value)
    return MetricCard(
        key=key,
        label=label,
        value=decimal_value,
        display_value=f"${decimal_value:,.2f} MXN",
        icon=icon,
        variant=variant,
        url=reverse(url_name),
    )


def _url(url_name: str, params: dict[str, object]) -> str:
    return f"{reverse(url_name)}?{urlencode(params)}"
