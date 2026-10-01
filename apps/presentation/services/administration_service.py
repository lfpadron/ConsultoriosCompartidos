"""Consolidated selectors for the business administration center."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db.models import Exists, OuterRef, Q, QuerySet, Sum
from django.urls import reverse

from apps.core.permissions import scope_queryset_for_user
from apps.finance.models import (
    AccountPartyType,
    AccountPayment,
    AccountStatement,
    AccountStatementStatus,
    CancellationCase,
    CancellationCaseStatus,
    Payment,
    PaymentAllocation,
    PaymentStatus,
    Statement,
    StatementStatus,
)
from apps.scheduling.models import (
    Reservation,
    ReservationBatch,
    ReservationBatchStatus,
    ReservationDeadlinePolicy,
    ReservationStatus,
)
from apps.vault.models import DocumentAsset, DocumentStatus

ZERO = Decimal("0.00")
PENDING_BATCH_STATUSES = (
    ReservationBatchStatus.REQUESTED,
    ReservationBatchStatus.PARTIALLY_CANCELLED,
)
ACTIVE_PAYMENT_STATUSES = (
    PaymentStatus.REGISTERED,
    PaymentStatus.VALIDATED,
)
HOURS_DEADLINE_POLICIES = (
    ReservationDeadlinePolicy.HOURS_BEFORE,
    ReservationDeadlinePolicy.EXCEPTION_HOURS,
    ReservationDeadlinePolicy.ADMIN_OVERRIDE,
)


@dataclass(frozen=True)
class ReservationQueueItem:
    reservation: Reservation
    marker: str
    priority_label: str
    row_class: str
    deadline_at: datetime | None
    payment_label: str
    detail_url: str


@dataclass(frozen=True)
class PendingAction:
    action_type: str
    title: str
    description: str
    url: str
    icon: str
    variant: str
    marker: str
    priority: int
    occurred_at: datetime
    due_at: datetime | None = None
    amount: Decimal | None = None
    currency: str = "MXN"


@dataclass(frozen=True)
class PendingCounts:
    payment_proofs: int
    reservation_payments: int
    account_payments: int
    cancellations: int
    owner_payouts: int
    documents: int

    @property
    def total(self) -> int:
        return (
            self.payment_proofs
            + self.reservation_payments
            + self.account_payments
            + self.cancellations
            + self.owner_payouts
            + self.documents
        )


@dataclass(frozen=True)
class FinancialSummary:
    expected_reservation_total: Decimal
    validated_reservation_payments: Decimal
    pending_reservation_balance: Decimal
    calculated_commissions: Decimal
    owner_net: Decimal
    account_balance_due: Decimal
    owner_payout_due: Decimal
    cancellation_refunds_due: Decimal
    currency: str = "MXN"


@dataclass(frozen=True)
class AdministrationOverview:
    reservations: list[ReservationQueueItem]
    total_reservations: int
    actions: list[PendingAction]
    pending: PendingCounts
    finance: FinancialSummary


def get_administration_overview(
    *,
    filters: dict[str, Any],
    user: Any,
    reservation_limit: int = 100,
    action_limit: int = 40,
) -> AdministrationOverview:
    reservations = _filtered_reservations(filters=filters, user=user)
    account_statements = _filtered_account_statements(filters=filters, user=user)
    batches = _pending_batches(reservations=reservations, user=user)
    reservation_payments = _registered_reservation_payments(
        reservations=reservations,
        user=user,
    )
    account_payments = _registered_account_payments(
        account_statements=account_statements,
        user=user,
    )
    cancellations = _pending_cancellations(
        reservations=reservations,
        user=user,
    )
    owner_statements = account_statements.filter(
        party_type=AccountPartyType.OWNER,
        payout_due__gt=0,
    )
    documents = _pending_documents(filters=filters, user=user)

    actions = [
        *_payment_proof_actions(batches),
        *_reservation_payment_actions(reservation_payments),
        *_account_payment_actions(account_payments),
        *_cancellation_actions(cancellations),
        *_owner_payout_actions(owner_statements),
        *_document_actions(documents),
    ]
    actions.sort(key=lambda item: (item.priority, item.occurred_at))

    reservation_items = [
        _reservation_queue_item(reservation)
        for reservation in reservations.order_by("date", "start_time")[
            :reservation_limit
        ]
    ]
    return AdministrationOverview(
        reservations=reservation_items,
        total_reservations=reservations.count(),
        actions=actions[:action_limit],
        pending=PendingCounts(
            payment_proofs=batches.count(),
            reservation_payments=reservation_payments.count(),
            account_payments=account_payments.count(),
            cancellations=cancellations.count(),
            owner_payouts=owner_statements.count(),
            documents=documents.count(),
        ),
        finance=_financial_summary(
            reservations=reservations,
            account_statements=account_statements,
            cancellations=cancellations,
            user=user,
        ),
    )


def _filtered_reservations(
    *,
    filters: dict[str, Any],
    user: Any,
) -> QuerySet[Reservation]:
    payment_submission = Payment.objects.filter(
        Q(reservation_id=OuterRef("pk")) | Q(batch_id=OuterRef("batch_id")),
        status__in=ACTIVE_PAYMENT_STATUSES,
        is_deleted=False,
    )
    queryset = scope_queryset_for_user(
        Reservation.objects.filter(is_deleted=False).select_related(
            "batch",
            "room",
            "room__clinic",
            "room__owner",
            "room__owner__user",
            "tenant_doctor",
            "tenant_doctor__user",
        ),
        user,
    ).annotate(has_payment_submission=Exists(payment_submission))
    clinic = filters.get("clinic")
    room = filters.get("room")
    owner = filters.get("owner")
    tenant_doctor = filters.get("tenant_doctor")
    status = filters.get("status")
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    if clinic:
        queryset = queryset.filter(room__clinic=clinic)
    if room:
        queryset = queryset.filter(room=room)
    if owner:
        queryset = queryset.filter(room__owner=owner)
    if tenant_doctor:
        queryset = queryset.filter(tenant_doctor=tenant_doctor)
    if status:
        queryset = queryset.filter(status=status)
    if date_from:
        queryset = queryset.filter(date__gte=date_from)
    if date_to:
        queryset = queryset.filter(date__lte=date_to)
    return queryset


def _filtered_account_statements(
    *,
    filters: dict[str, Any],
    user: Any,
) -> QuerySet[AccountStatement]:
    queryset = scope_queryset_for_user(
        AccountStatement.objects.filter(
            status=AccountStatementStatus.ISSUED,
            is_deleted=False,
        ).select_related("owner", "tenant_doctor"),
        user,
    )
    clinic = filters.get("clinic")
    room = filters.get("room")
    owner = filters.get("owner")
    tenant_doctor = filters.get("tenant_doctor")
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    if clinic:
        queryset = queryset.filter(
            Q(owner__consulting_rooms__clinic=clinic) | Q(lines__room__clinic=clinic)
        )
    if room:
        queryset = queryset.filter(Q(owner=room.owner) | Q(lines__room=room))
    if owner:
        queryset = queryset.filter(owner=owner)
    if tenant_doctor:
        queryset = queryset.filter(tenant_doctor=tenant_doctor)
    if date_from:
        queryset = queryset.filter(period_end__gte=date_from)
    if date_to:
        queryset = queryset.filter(period_start__lte=date_to)
    return queryset.distinct()


def _pending_batches(
    *,
    reservations: QuerySet[Reservation],
    user: Any,
) -> QuerySet[ReservationBatch]:
    return (
        scope_queryset_for_user(
            ReservationBatch.objects.filter(
                status__in=PENDING_BATCH_STATUSES,
                payment_proof_submitted_at__isnull=True,
                expired_at__isnull=True,
                is_deleted=False,
                reservations__in=reservations.order_by(),
            ).select_related("room", "room__clinic", "tenant_doctor"),
            user,
        )
        .exclude(
            Q(
                payment_submissions__status__in=ACTIVE_PAYMENT_STATUSES,
                payment_submissions__is_deleted=False,
            )
            | Q(
                reservations__payments__status__in=ACTIVE_PAYMENT_STATUSES,
                reservations__payments__is_deleted=False,
            )
        )
        .distinct()
    )


def _registered_reservation_payments(
    *,
    reservations: QuerySet[Reservation],
    user: Any,
) -> QuerySet[Payment]:
    return (
        scope_queryset_for_user(
            Payment.objects.filter(
                status=PaymentStatus.REGISTERED,
                is_deleted=False,
            ).select_related(
                "reservation",
                "batch",
                "tenant_doctor",
                "tenant_doctor__user",
            ),
            user,
        )
        .filter(
            Q(reservation__in=reservations.order_by())
            | Q(batch__reservations__in=reservations.order_by())
        )
        .distinct()
    )


def _registered_account_payments(
    *,
    account_statements: QuerySet[AccountStatement],
    user: Any,
) -> QuerySet[AccountPayment]:
    return scope_queryset_for_user(
        AccountPayment.objects.filter(
            account_statement__in=account_statements.order_by(),
            status=PaymentStatus.REGISTERED,
            is_deleted=False,
        ).select_related(
            "account_statement",
            "account_statement__owner",
            "account_statement__tenant_doctor",
        ),
        user,
    )


def _pending_cancellations(
    *,
    reservations: QuerySet[Reservation],
    user: Any,
) -> QuerySet[CancellationCase]:
    return (
        scope_queryset_for_user(
            CancellationCase.objects.filter(
                status=CancellationCaseStatus.PENDING,
                is_deleted=False,
            ).select_related("batch", "tenant_doctor"),
            user,
        )
        .filter(batch__reservations__in=reservations.order_by())
        .distinct()
    )


def _pending_documents(
    *,
    filters: dict[str, Any],
    user: Any,
) -> QuerySet[DocumentAsset]:
    queryset = scope_queryset_for_user(
        DocumentAsset.objects.filter(
            status__in=(DocumentStatus.RECEIVED, DocumentStatus.IN_REVIEW),
            is_deleted=False,
        ).select_related("room", "reservation", "owner", "tenant_doctor"),
        user,
    )
    clinic = filters.get("clinic")
    room = filters.get("room")
    owner = filters.get("owner")
    tenant_doctor = filters.get("tenant_doctor")
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    if clinic:
        queryset = queryset.filter(
            Q(room__clinic=clinic)
            | Q(reservation__room__clinic=clinic)
            | Q(owner__consulting_rooms__clinic=clinic)
        )
    if room:
        queryset = queryset.filter(Q(room=room) | Q(reservation__room=room))
    if owner:
        queryset = queryset.filter(Q(owner=owner) | Q(room__owner=owner))
    if tenant_doctor:
        queryset = queryset.filter(
            Q(tenant_doctor=tenant_doctor) | Q(reservation__tenant_doctor=tenant_doctor)
        )
    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)
    return queryset.distinct()


def _reservation_queue_item(reservation: Reservation) -> ReservationQueueItem:
    marker, priority_label, row_class = _reservation_priority(reservation)
    batch = reservation.batch
    deadline_at = batch.payment_deadline_at if batch else None
    has_payment = bool(getattr(reservation, "has_payment_submission", False))
    if reservation.status == ReservationStatus.CANCELLED:
        payment_label = "Cancelada"
    elif reservation.status in {
        ReservationStatus.PAID,
        ReservationStatus.CONFIRMED,
        ReservationStatus.FINISHED,
    }:
        payment_label = reservation.get_status_display()
    elif has_payment or (batch and batch.payment_proof_submitted_at):
        payment_label = "Comprobante enviado"
    else:
        payment_label = "Sin comprobante"
    detail_url = (
        reverse("reservation_batch_detail", args=[batch.pk])
        if batch
        else reverse("reservation_detail", args=[reservation.pk])
    )
    return ReservationQueueItem(
        reservation=reservation,
        marker=marker,
        priority_label=priority_label,
        row_class=row_class,
        deadline_at=deadline_at,
        payment_label=str(payment_label),
        detail_url=detail_url,
    )


def _reservation_priority(reservation: Reservation) -> tuple[str, str, str]:
    if reservation.status == ReservationStatus.CANCELLED:
        return "X", "Cancelada", "administration-row-cancelled"
    batch = reservation.batch
    if batch and batch.deadline_policy in HOURS_DEADLINE_POLICIES:
        return "!!", "Regla de horas", "administration-row-hours"
    if batch and batch.deadline_policy == ReservationDeadlinePolicy.PREVIOUS_DAY:
        return "!", "Día anterior", "administration-row-previous-day"
    return "", "Sin alerta", ""


def _payment_proof_actions(
    batches: QuerySet[ReservationBatch],
) -> list[PendingAction]:
    actions: list[PendingAction] = []
    for batch in batches[:50]:
        marker, variant, priority = _batch_priority(batch)
        actions.append(
            PendingAction(
                action_type="Reservación",
                title="Comprobante pendiente",
                description=(
                    f"{batch.reference} | {batch.tenant_doctor} | {batch.room}"
                ),
                url=reverse("reservation_batch_detail", args=[batch.pk]),
                icon="bi-hourglass-split",
                variant=variant,
                marker=marker,
                priority=priority,
                occurred_at=batch.payment_deadline_at or batch.requested_at,
                due_at=batch.payment_deadline_at,
                amount=batch.tariff_final,
                currency=batch.currency,
            )
        )
    return actions


def _batch_priority(batch: ReservationBatch) -> tuple[str, str, int]:
    if batch.deadline_policy in HOURS_DEADLINE_POLICIES:
        return "!!", "orange", 0
    if batch.deadline_policy == ReservationDeadlinePolicy.PREVIOUS_DAY:
        return "!", "warning", 5
    return "", "secondary", 8


def _reservation_payment_actions(payments: QuerySet[Payment]) -> list[PendingAction]:
    return [
        PendingAction(
            action_type="Pago",
            title="Validar comprobante de reservación",
            description=str(payment.batch or payment.reservation),
            url=reverse("payment_detail", args=[payment.pk]),
            icon="bi-credit-card",
            variant="primary",
            marker="",
            priority=10,
            occurred_at=payment.created_at,
            amount=payment.amount,
            currency=payment.currency,
        )
        for payment in payments[:50]
    ]


def _account_payment_actions(
    payments: QuerySet[AccountPayment],
) -> list[PendingAction]:
    return [
        PendingAction(
            action_type="Estado de cuenta",
            title="Validar pago de suscripción o cuota",
            description=str(payment.account_statement),
            url=reverse("account_payment_validate", args=[payment.pk]),
            icon="bi-receipt",
            variant="primary",
            marker="",
            priority=15,
            occurred_at=payment.created_at,
            amount=payment.amount,
            currency=payment.currency,
        )
        for payment in payments[:50]
    ]


def _cancellation_actions(
    cases: QuerySet[CancellationCase],
) -> list[PendingAction]:
    return [
        PendingAction(
            action_type="Cancelación",
            title="Resolver cancelación",
            description=f"{case.batch or 'Sin grupo'} | {case.tenant_doctor}",
            url=reverse("reservation_cancellation_detail", args=[case.pk]),
            icon="bi-calendar-x",
            variant="danger",
            marker="X",
            priority=20,
            occurred_at=case.requested_at,
            amount=case.refundable_amount,
            currency=case.currency,
        )
        for case in cases[:50]
    ]


def _owner_payout_actions(
    statements: QuerySet[AccountStatement],
) -> list[PendingAction]:
    return [
        PendingAction(
            action_type="Propietario",
            title="Pagar corte al propietario",
            description=str(statement),
            url=reverse("account_statement_detail", args=[statement.pk]),
            icon="bi-bank",
            variant="success",
            marker="",
            priority=30,
            occurred_at=statement.generated_at,
            amount=statement.payout_due,
            currency=statement.currency,
        )
        for statement in statements[:50]
    ]


def _document_actions(documents: QuerySet[DocumentAsset]) -> list[PendingAction]:
    return [
        PendingAction(
            action_type="Documento",
            title="Revisar documento",
            description=str(document),
            url=reverse("document_detail", args=[document.pk]),
            icon="bi-file-earmark-check",
            variant="secondary",
            marker="",
            priority=40,
            occurred_at=document.created_at,
        )
        for document in documents[:50]
    ]


def _financial_summary(
    *,
    reservations: QuerySet[Reservation],
    account_statements: QuerySet[AccountStatement],
    cancellations: QuerySet[CancellationCase],
    user: Any,
) -> FinancialSummary:
    financial_reservations = reservations.exclude(status=ReservationStatus.CANCELLED)
    statements = scope_queryset_for_user(
        Statement.objects.filter(
            reservation__in=financial_reservations.order_by(),
            status=StatementStatus.CURRENT,
            is_deleted=False,
        ),
        user,
    )
    allocations = scope_queryset_for_user(
        PaymentAllocation.objects.filter(
            reservation__in=financial_reservations.order_by(),
            payment__status=PaymentStatus.VALIDATED,
            payment__is_deleted=False,
            is_deleted=False,
        ),
        user,
    )
    legacy_payments = scope_queryset_for_user(
        Payment.objects.filter(
            reservation__in=financial_reservations.order_by(),
            status=PaymentStatus.VALIDATED,
            allocations__isnull=True,
            is_deleted=False,
        ),
        user,
    )
    expected = _decimal_sum(statements, "total_doctor")
    validated = _decimal_sum(allocations, "amount") + _decimal_sum(
        legacy_payments,
        "amount",
    )
    return FinancialSummary(
        expected_reservation_total=expected,
        validated_reservation_payments=validated,
        pending_reservation_balance=max(expected - validated, ZERO),
        calculated_commissions=_decimal_sum(statements, "platform_commission"),
        owner_net=_decimal_sum(statements, "owner_net"),
        account_balance_due=_decimal_sum(account_statements, "balance_due"),
        owner_payout_due=_decimal_sum(account_statements, "payout_due"),
        cancellation_refunds_due=_decimal_sum(cancellations, "refundable_amount"),
    )


def _decimal_sum(queryset: QuerySet[Any], field_name: str) -> Decimal:
    return queryset.aggregate(total=Sum(field_name))["total"] or ZERO
