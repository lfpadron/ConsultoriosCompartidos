"""Periodic account statements for owners and tenant doctors."""

from calendar import monthrange
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Model, Q, Sum
from django.utils import timezone

from apps.astrotrace.services import record_event
from apps.billing.models import (
    FeePaymentMode,
    OwnerPayoutSchedule,
    OwnerSubscription,
    PayoutFrequency,
    RoomFixedFeeRule,
    RoomMonthlyFee,
    SubscriptionCycle,
    TenantSubscription,
)
from apps.catalog.models import OwnerProfile, TenantDoctorProfile
from apps.core.constants import DEFAULT_CURRENCY
from apps.finance.models import (
    AccountLineType,
    AccountPartyType,
    AccountPayment,
    AccountStatement,
    AccountStatementLine,
    OwnerPayout,
    Payment,
    PaymentAllocation,
    PaymentMethod,
    PaymentStatus,
    Settlement,
    SettlementStatus,
    Statement,
    StatementStatus,
)
from apps.scheduling.models import ReservationStatus

ZERO = Decimal("0.00")


@transaction.atomic
def generate_owner_account_statement(
    *,
    owner: OwnerProfile,
    period_start: date,
    period_end: date,
    currency: str = DEFAULT_CURRENCY,
    actor: Model | None = None,
    as_of: date | None = None,
) -> AccountStatement:
    schedule = _owner_schedule_for_date(owner, period_end)
    _validate_owner_period(period_start, period_end, schedule.frequency)
    available_on = period_end + timedelta(days=1)
    if available_on > (as_of or timezone.localdate()):
        raise ValidationError(
            {"period_end": "El periodo todavía no está disponible para emisión."}
        )
    existing = AccountStatement.objects.filter(
        owner=owner,
        period_start=period_start,
        period_end=period_end,
        currency=currency,
        is_deleted=False,
    ).first()
    if existing is not None:
        return refresh_account_statement(account_statement=existing, actor=actor)

    account_statement = AccountStatement(
        party_type=AccountPartyType.OWNER,
        owner=owner,
        period_start=period_start,
        period_end=period_end,
        available_on=available_on,
        currency=currency,
        payout_frequency=schedule.frequency,
    )
    _set_audit_users(account_statement, actor, created=True)
    account_statement.save()
    _create_owner_earning_lines(account_statement, actor=actor)
    _create_owner_subscription_lines(account_statement, actor=actor)
    _create_owner_fee_lines(account_statement, actor=actor)
    refresh_account_statement(account_statement=account_statement, actor=actor)
    record_event(
        event_type="account_statement.owner_issued",
        object_label=str(account_statement),
        actor=actor,
        payload=_statement_payload(account_statement, actor=actor),
    )
    return account_statement


@transaction.atomic
def generate_tenant_account_statement(
    *,
    tenant_doctor: TenantDoctorProfile,
    period_start: date,
    period_end: date,
    currency: str = DEFAULT_CURRENCY,
    actor: Model | None = None,
    as_of: date | None = None,
) -> AccountStatement:
    _validate_monthly_period(period_start, period_end)
    available_on = period_end + timedelta(days=1)
    if available_on > (as_of or timezone.localdate()):
        raise ValidationError(
            {"period_end": "El periodo todavía no está disponible para emisión."}
        )
    existing = AccountStatement.objects.filter(
        tenant_doctor=tenant_doctor,
        period_start=period_start,
        period_end=period_end,
        currency=currency,
        is_deleted=False,
    ).first()
    if existing is not None:
        return refresh_account_statement(account_statement=existing, actor=actor)

    account_statement = AccountStatement(
        party_type=AccountPartyType.TENANT,
        tenant_doctor=tenant_doctor,
        period_start=period_start,
        period_end=period_end,
        available_on=available_on,
        currency=currency,
    )
    _set_audit_users(account_statement, actor, created=True)
    account_statement.save()
    _create_tenant_reservation_lines(account_statement, actor=actor)
    _create_tenant_subscription_lines(account_statement, actor=actor)
    refresh_account_statement(account_statement=account_statement, actor=actor)
    record_event(
        event_type="account_statement.tenant_issued",
        object_label=str(account_statement),
        actor=actor,
        payload=_statement_payload(account_statement, actor=actor),
    )
    return account_statement


@transaction.atomic
def refresh_account_statement(
    *,
    account_statement: AccountStatement,
    actor: Model | None = None,
) -> AccountStatement:
    statement = AccountStatement.objects.select_for_update().get(
        pk=account_statement.pk,
        is_deleted=False,
    )
    if statement.party_type == AccountPartyType.OWNER:
        _reconcile_owner_settlement_lines(statement, actor=actor)
        _reconcile_owner_payout_lines(statement, actor=actor)
        _create_owner_earning_lines(statement, actor=actor)
        _create_owner_payout_lines(statement, actor=actor)
    else:
        _reconcile_tenant_reservation_lines(statement, actor=actor)
        _create_tenant_reservation_lines(statement, actor=actor)
        _create_tenant_reservation_payment_lines(statement, actor=actor)
    _create_validated_account_payment_lines(statement, actor=actor)

    totals = _line_totals(statement)
    statement.rental_income = totals[AccountLineType.RENTAL_INCOME]
    statement.reservation_charges = totals[AccountLineType.RESERVATION_CHARGE]
    statement.commissions = totals[AccountLineType.PLATFORM_COMMISSION]
    statement.owner_net = totals[AccountLineType.OWNER_NET]
    statement.subscription_charges = (
        totals[AccountLineType.OWNER_SUBSCRIPTION]
        + totals[AccountLineType.TENANT_SUBSCRIPTION]
    )
    statement.direct_fees = totals[AccountLineType.ROOM_FEE_DIRECT]
    statement.deducted_fees = totals[AccountLineType.ROOM_FEE_DEDUCTED]
    statement.reservation_payments = totals[AccountLineType.RESERVATION_PAYMENT]
    statement.account_payments_total = (
        totals[AccountLineType.OWNER_PAYMENT]
        + totals[AccountLineType.TENANT_SUBSCRIPTION_PAYMENT]
    )
    statement.payouts_made = totals[AccountLineType.OWNER_PAYOUT]
    if statement.party_type == AccountPartyType.OWNER:
        uncovered_deductions = max(
            statement.deducted_fees - statement.owner_net,
            ZERO,
        )
        statement.balance_due = max(
            statement.subscription_charges
            + statement.direct_fees
            + uncovered_deductions
            - statement.account_payments_total,
            ZERO,
        )
        statement.payout_due = max(
            statement.owner_net
            - statement.deducted_fees
            - statement.payouts_made,
            ZERO,
        )
    else:
        statement.balance_due = max(
            statement.reservation_charges
            + statement.subscription_charges
            - statement.reservation_payments
            - statement.account_payments_total,
            ZERO,
        )
        statement.payout_due = ZERO
    statement.refreshed_at = timezone.now()
    _set_audit_users(statement, actor)
    statement.save()
    _copy_statement_state(account_statement, statement)
    return statement


@transaction.atomic
def register_account_payment(
    *,
    account_statement: AccountStatement,
    category: str,
    amount: Decimal,
    method: str,
    reference: str,
    payment_date: date,
    receipt: Any = None,
    notes: str = "",
    actor: Model | None = None,
) -> AccountPayment:
    statement = AccountStatement.objects.select_for_update().get(
        pk=account_statement.pk,
        is_deleted=False,
    )
    refresh_account_statement(account_statement=statement, actor=actor)
    if amount > statement.balance_due:
        raise ValidationError(
            {"amount": "El importe no puede exceder el saldo por pagar."}
        )
    if method != PaymentMethod.CASH and not receipt:
        raise ValidationError({"receipt": "El comprobante es obligatorio."})
    payment = AccountPayment(
        account_statement=statement,
        category=category,
        amount=amount,
        currency=statement.currency,
        method=method,
        reference=reference,
        payment_date=payment_date,
        notes=notes,
    )
    if receipt:
        payment.receipt = receipt
    _set_audit_users(payment, actor, created=True)
    payment.save()
    record_event(
        event_type="account_payment.registered",
        object_label=str(payment),
        actor=actor,
        payload=_account_payment_payload(payment, actor=actor),
    )
    return payment


@transaction.atomic
def validate_account_payment(
    *,
    payment: AccountPayment,
    actor: Model | None = None,
) -> AccountPayment:
    locked_payment = AccountPayment.objects.select_for_update().select_related(
        "account_statement"
    ).get(pk=payment.pk, is_deleted=False)
    if locked_payment.status != PaymentStatus.REGISTERED:
        raise ValidationError({"status": "Sólo se puede validar un pago registrado."})
    locked_payment.status = PaymentStatus.VALIDATED
    locked_payment.validated_at = timezone.now()
    locked_payment.validated_by = cast(Any, actor) if actor is not None else None
    _set_audit_users(locked_payment, actor)
    locked_payment.save()
    refresh_account_statement(
        account_statement=locked_payment.account_statement,
        actor=actor,
    )
    record_event(
        event_type="account_payment.validated",
        object_label=str(locked_payment),
        actor=actor,
        payload=_account_payment_payload(locked_payment, actor=actor),
    )
    _copy_account_payment_state(payment, locked_payment)
    return locked_payment


@transaction.atomic
def reject_account_payment(
    *,
    payment: AccountPayment,
    reason: str,
    actor: Model | None = None,
) -> AccountPayment:
    if not reason.strip():
        raise ValidationError({"reason": "El motivo de rechazo es obligatorio."})
    locked_payment = AccountPayment.objects.select_for_update().get(
        pk=payment.pk,
        is_deleted=False,
    )
    if locked_payment.status != PaymentStatus.REGISTERED:
        raise ValidationError({"status": "Sólo se puede rechazar un pago registrado."})
    locked_payment.status = PaymentStatus.REJECTED
    locked_payment.rejected_reason = reason
    _set_audit_users(locked_payment, actor)
    locked_payment.save()
    record_event(
        event_type="account_payment.rejected",
        object_label=str(locked_payment),
        actor=actor,
        payload={
            **_account_payment_payload(locked_payment, actor=actor),
            "reason": reason,
        },
    )
    _copy_account_payment_state(payment, locked_payment)
    return locked_payment


@transaction.atomic
def register_owner_payout(
    *,
    account_statement: AccountStatement,
    amount: Decimal,
    reference: str,
    payment_date: date,
    receipt: Any,
    notes: str = "",
    actor: Model | None = None,
) -> OwnerPayout:
    statement = AccountStatement.objects.select_for_update().get(
        pk=account_statement.pk,
        is_deleted=False,
    )
    if statement.party_type != AccountPartyType.OWNER:
        raise ValidationError(
            {"account_statement": "El estado debe pertenecer a un propietario."}
        )
    refresh_account_statement(account_statement=statement, actor=actor)
    if amount > statement.payout_due:
        raise ValidationError(
            {"amount": "El importe no puede exceder el saldo por entregar."}
        )
    previous_payouts = statement.owner_payouts.filter(is_deleted=False).exists()
    payout = OwnerPayout(
        account_statement=statement,
        amount=amount,
        deducted_fees=ZERO if previous_payouts else statement.deducted_fees,
        currency=statement.currency,
        reference=reference,
        payment_date=payment_date,
        receipt=receipt,
        paid_by=cast(Any, actor) if actor is not None else None,
        notes=notes,
    )
    _set_audit_users(payout, actor, created=True)
    payout.save()
    refresh_account_statement(account_statement=statement, actor=actor)
    record_event(
        event_type="account_statement.owner_payout_registered",
        object_label=str(payout),
        actor=actor,
        payload=_owner_payout_payload(payout, actor=actor),
    )
    return payout


def generate_due_account_statements(
    *,
    as_of: date | None = None,
    actor: Model | None = None,
) -> list[AccountStatement]:
    run_date = as_of or timezone.localdate()
    generated: list[AccountStatement] = []
    schedules = OwnerPayoutSchedule.objects.filter(
        is_active=True,
        is_deleted=False,
        start_date__lt=run_date,
    )
    for schedule in schedules.select_related("owner"):
        period = due_owner_period(frequency=schedule.frequency, as_of=run_date)
        if period is None:
            continue
        if schedule.start_date > period[1] or (
            schedule.end_date is not None and schedule.end_date < period[1]
        ):
            continue
        generated.append(
            generate_owner_account_statement(
                owner=schedule.owner,
                period_start=period[0],
                period_end=period[1],
                actor=actor,
                as_of=run_date,
            )
        )
    if run_date.day == 1:
        period_start, period_end = _previous_month(run_date)
        tenant_ids = set(
            TenantDoctorProfile.objects.filter(
                Q(
                    reservations__date__range=(period_start, period_end),
                    reservations__is_deleted=False,
                )
                | Q(
                    subscription_rules__start_date__lte=period_end,
                    subscription_rules__is_deleted=False,
                )
            ).values_list("pk", flat=True)
        )
        for tenant in TenantDoctorProfile.objects.filter(pk__in=tenant_ids):
            generated.append(
                generate_tenant_account_statement(
                    tenant_doctor=tenant,
                    period_start=period_start,
                    period_end=period_end,
                    actor=actor,
                    as_of=run_date,
                )
            )
    return generated


def due_owner_period(*, frequency: str, as_of: date) -> tuple[date, date] | None:
    if frequency == PayoutFrequency.WEEKLY and as_of.weekday() == 0:
        return as_of - timedelta(days=7), as_of - timedelta(days=1)
    if frequency == PayoutFrequency.BIWEEKLY:
        if as_of.day == 16:
            return as_of.replace(day=1), as_of.replace(day=15)
        if as_of.day == 1:
            previous_start, previous_end = _previous_month(as_of)
            return previous_start.replace(day=16), previous_end
    if frequency == PayoutFrequency.MONTHLY and as_of.day == 1:
        return _previous_month(as_of)
    return None


def _create_owner_earning_lines(
    account_statement: AccountStatement,
    *,
    actor: Model | None,
) -> None:
    settlements = Settlement.objects.filter(
        owner=account_statement.owner,
        reservation__date__range=(
            account_statement.period_start,
            account_statement.period_end,
        ),
        currency=account_statement.currency,
        status__in=(SettlementStatus.CALCULATED, SettlementStatus.PAID),
        is_deleted=False,
    ).select_related("reservation", "room")
    for settlement in settlements:
        snapshot = {
            "statement_id": str(settlement.statement_id),
            "reservation_subtotal": str(settlement.reservation_subtotal),
            "platform_commission": str(settlement.platform_commission),
            "commission_taxes": str(settlement.commission_taxes),
            "owner_net": str(settlement.owner_net),
        }
        common: dict[str, Any] = {
            "account_statement": account_statement,
            "effective_date": settlement.reservation.date,
            "room": settlement.room,
            "reservation": settlement.reservation,
            "settlement": settlement,
            "source_model": settlement._meta.label,
            "source_snapshot": snapshot,
        }
        _create_line(
            **common,
            line_type=AccountLineType.RENTAL_INCOME,
            description=f"Ingreso {settlement.reservation}",
            amount=settlement.reservation_subtotal,
            source_id=f"{settlement.pk}:gross",
            actor=actor,
        )
        _create_line(
            **common,
            line_type=AccountLineType.PLATFORM_COMMISSION,
            description=f"Comisión {settlement.reservation}",
            amount=settlement.platform_commission + settlement.commission_taxes,
            source_id=f"{settlement.pk}:commission",
            actor=actor,
        )
        _create_line(
            **common,
            line_type=AccountLineType.OWNER_NET,
            description=f"Neto {settlement.reservation}",
            amount=settlement.owner_net,
            source_id=f"{settlement.pk}:net",
            actor=actor,
        )


def _reconcile_owner_settlement_lines(
    account_statement: AccountStatement,
    *,
    actor: Model | None,
) -> None:
    valid_ids = Settlement.objects.filter(
        owner=account_statement.owner,
        reservation__date__range=(
            account_statement.period_start,
            account_statement.period_end,
        ),
        currency=account_statement.currency,
        status__in=(SettlementStatus.CALCULATED, SettlementStatus.PAID),
        is_deleted=False,
    ).values_list("pk", flat=True)
    stale_lines = account_statement.lines.filter(
        line_type__in=(
            AccountLineType.RENTAL_INCOME,
            AccountLineType.PLATFORM_COMMISSION,
            AccountLineType.OWNER_NET,
        ),
        is_deleted=False,
    ).exclude(settlement_id__in=valid_ids)
    _soft_delete_lines(stale_lines, actor=actor)


def _reconcile_owner_payout_lines(
    account_statement: AccountStatement,
    *,
    actor: Model | None,
) -> None:
    payouts = OwnerPayout.objects.filter(
        account_statement=account_statement,
        is_deleted=False,
    )
    payout_ids = {str(payout_id) for payout_id in payouts.values_list("pk", flat=True)}
    actual_lines = account_statement.lines.filter(
        line_type=AccountLineType.OWNER_PAYOUT,
        source_model=OwnerPayout._meta.label,
        is_deleted=False,
    ).exclude(source_id__in=payout_ids)
    _soft_delete_lines(actual_lines, actor=actor)

    legacy_lines = account_statement.lines.filter(
        line_type=AccountLineType.OWNER_PAYOUT,
        source_model=Settlement._meta.label,
        is_deleted=False,
    )
    if payout_ids:
        _soft_delete_lines(legacy_lines, actor=actor)
        return
    valid_legacy_ids = {
        f"{settlement_id}:payout"
        for settlement_id in Settlement.objects.filter(
            owner=account_statement.owner,
            reservation__date__range=(
                account_statement.period_start,
                account_statement.period_end,
            ),
            status=SettlementStatus.PAID,
            currency=account_statement.currency,
            is_deleted=False,
        ).values_list("pk", flat=True)
    }
    _soft_delete_lines(
        legacy_lines.exclude(source_id__in=valid_legacy_ids),
        actor=actor,
    )


def _reconcile_tenant_reservation_lines(
    account_statement: AccountStatement,
    *,
    actor: Model | None,
) -> None:
    valid_ids = Statement.objects.filter(
        reservation__tenant_doctor=account_statement.tenant_doctor,
        reservation__date__range=(
            account_statement.period_start,
            account_statement.period_end,
        ),
        reservation__status__in=(
            ReservationStatus.PAID,
            ReservationStatus.CONFIRMED,
            ReservationStatus.FINISHED,
        ),
        status=StatementStatus.CURRENT,
        currency=account_statement.currency,
        is_deleted=False,
    ).values_list("reservation_id", flat=True)
    stale_lines = account_statement.lines.filter(
        line_type__in=(
            AccountLineType.RESERVATION_CHARGE,
            AccountLineType.RESERVATION_PAYMENT,
        ),
        is_deleted=False,
    ).exclude(reservation_id__in=valid_ids)
    _soft_delete_lines(stale_lines, actor=actor)


def _soft_delete_lines(lines: Any, *, actor: Model | None) -> None:
    for line in lines:
        line.is_deleted = True
        _set_audit_users(line, actor)
        line.save(update_fields=["is_deleted", "updated_by", "updated_at"])
        record_event(
            event_type="account_statement.line_reversed",
            object_label=str(line),
            actor=actor,
            payload={
                "model": line._meta.label,
                "id": str(line.pk),
                "account_statement_id": str(line.account_statement_id),
                "line_type": line.line_type,
                "amount": str(line.amount),
                "level": "financiero",
                "actor_id": str(actor.pk) if actor is not None else "",
            },
        )


def _create_owner_subscription_lines(
    account_statement: AccountStatement,
    *,
    actor: Model | None,
) -> None:
    rules = OwnerSubscription.objects.filter(
        owner=account_statement.owner,
        currency=account_statement.currency,
        is_active=True,
        is_deleted=False,
        start_date__lte=account_statement.period_end,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=account_statement.period_start))
    for rule in rules:
        for due_date in _subscription_due_dates(
            rule,
            account_statement.period_start,
            account_statement.period_end,
        ):
            coverage_end = _subscription_coverage_end(rule, due_date)
            _create_line(
                account_statement=account_statement,
                line_type=AccountLineType.OWNER_SUBSCRIPTION,
                description=(
                    f"Suscripción {rule.get_billing_cycle_display()} del propietario"
                ),
                effective_date=due_date,
                coverage_start=due_date,
                coverage_end=coverage_end,
                amount=rule.amount,
                source_model=rule._meta.label,
                source_id=f"{rule.pk}:{due_date.isoformat()}",
                source_snapshot={
                    "version": rule.version,
                    "billing_cycle": rule.billing_cycle,
                    "amount": str(rule.amount),
                },
                actor=actor,
            )


def _create_owner_fee_lines(
    account_statement: AccountStatement,
    *,
    actor: Model | None,
) -> None:
    owner = account_statement.owner
    months = list(
        _month_starts(
            account_statement.period_start,
            account_statement.period_end,
        )
    )
    variable_fees = RoomMonthlyFee.objects.filter(
        room__owner=owner,
        billing_month__in=months,
        currency=account_statement.currency,
        is_active=True,
        is_deleted=False,
    ).select_related("room")
    variable_keys = {
        (fee.room_id, fee.fee_type, fee.billing_month): fee for fee in variable_fees
    }
    for fee in variable_fees:
        charge_date = _month_end(fee.billing_month)
        if (
            account_statement.period_start
            <= charge_date
            <= account_statement.period_end
        ):
            _create_fee_line(account_statement, fee, charge_date, actor=actor)

    fixed_rules = RoomFixedFeeRule.objects.filter(
        room__owner=owner,
        currency=account_statement.currency,
        is_active=True,
        is_deleted=False,
        start_date__lte=account_statement.period_end,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=account_statement.period_start))
    for rule in fixed_rules.select_related("room"):
        for month_start in months:
            if (rule.room_id, rule.fee_type, month_start) in variable_keys:
                continue
            charge_date = _month_end(month_start)
            month_overlaps_rule = rule.start_date <= charge_date and (
                rule.end_date is None or rule.end_date >= month_start
            )
            if (
                month_overlaps_rule
                and account_statement.period_start
                <= charge_date
                <= account_statement.period_end
            ):
                _create_fee_line(account_statement, rule, charge_date, actor=actor)


def _create_fee_line(
    account_statement: AccountStatement,
    fee: RoomFixedFeeRule | RoomMonthlyFee,
    charge_date: date,
    *,
    actor: Model | None,
) -> None:
    line_type = (
        AccountLineType.ROOM_FEE_DEDUCTED
        if fee.payment_mode == FeePaymentMode.AUTO_DEDUCT
        else AccountLineType.ROOM_FEE_DIRECT
    )
    _create_line(
        account_statement=account_statement,
        line_type=line_type,
        description=f"{fee.get_fee_type_display()} - {fee.room}",
        effective_date=charge_date,
        coverage_start=charge_date.replace(day=1),
        coverage_end=_month_end(charge_date),
        room=fee.room,
        amount=fee.amount,
        source_model=fee._meta.label,
        source_id=f"{fee.pk}:{charge_date:%Y-%m}",
        source_snapshot={
            "version": fee.version,
            "fee_type": fee.fee_type,
            "payment_mode": fee.payment_mode,
            "amount": str(fee.amount),
        },
        actor=actor,
    )


def _create_tenant_reservation_lines(
    account_statement: AccountStatement,
    *,
    actor: Model | None,
) -> None:
    statements = Statement.objects.filter(
        reservation__tenant_doctor=account_statement.tenant_doctor,
        reservation__date__range=(
            account_statement.period_start,
            account_statement.period_end,
        ),
        reservation__status__in=(
            ReservationStatus.PAID,
            ReservationStatus.CONFIRMED,
            ReservationStatus.FINISHED,
        ),
        status=StatementStatus.CURRENT,
        currency=account_statement.currency,
        is_deleted=False,
    ).select_related("reservation", "reservation__room")
    for statement in statements:
        _create_line(
            account_statement=account_statement,
            line_type=AccountLineType.RESERVATION_CHARGE,
            description=f"Reservación {statement.reservation}",
            effective_date=statement.reservation.date,
            room=statement.reservation.room,
            reservation=statement.reservation,
            amount=statement.total_doctor,
            source_model=statement._meta.label,
            source_id=str(statement.pk),
            source_snapshot={
                "version": statement.version,
                "tariff_final": str(statement.tariff_final),
                "total_doctor": str(statement.total_doctor),
                "calculation_hash": statement.calculation_hash,
            },
            actor=actor,
        )


def _create_tenant_subscription_lines(
    account_statement: AccountStatement,
    *,
    actor: Model | None,
) -> None:
    rules = TenantSubscription.objects.filter(
        tenant_doctor=account_statement.tenant_doctor,
        currency=account_statement.currency,
        is_active=True,
        is_deleted=False,
        start_date__lte=account_statement.period_end,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=account_statement.period_start))
    for rule in rules:
        for due_date in _subscription_due_dates(
            rule,
            account_statement.period_start,
            account_statement.period_end,
        ):
            _create_line(
                account_statement=account_statement,
                line_type=AccountLineType.TENANT_SUBSCRIPTION,
                description=(
                    f"Suscripción {rule.get_billing_cycle_display()} del arrendatario"
                ),
                effective_date=due_date,
                coverage_start=due_date,
                coverage_end=_subscription_coverage_end(rule, due_date),
                amount=rule.amount,
                source_model=rule._meta.label,
                source_id=f"{rule.pk}:{due_date.isoformat()}",
                source_snapshot={
                    "version": rule.version,
                    "billing_cycle": rule.billing_cycle,
                    "amount": str(rule.amount),
                },
                actor=actor,
            )


def _create_owner_payout_lines(
    account_statement: AccountStatement,
    *,
    actor: Model | None,
) -> None:
    payouts = OwnerPayout.objects.filter(
        account_statement=account_statement,
        is_deleted=False,
    ).select_related("paid_by")
    if payouts.exists():
        for payout in payouts:
            _create_line(
                account_statement=account_statement,
                line_type=AccountLineType.OWNER_PAYOUT,
                description="Pago de corte al propietario",
                effective_date=payout.payment_date,
                amount=payout.amount,
                source_model=payout._meta.label,
                source_id=str(payout.pk),
                source_snapshot={
                    "reference": payout.reference,
                    "deducted_fees": str(payout.deducted_fees),
                    "paid_by_id": str(payout.paid_by_id or ""),
                },
                actor=actor,
            )
        return
    settlements = Settlement.objects.filter(
        owner=account_statement.owner,
        reservation__date__range=(
            account_statement.period_start,
            account_statement.period_end,
        ),
        status=SettlementStatus.PAID,
        currency=account_statement.currency,
        is_deleted=False,
    ).select_related("reservation", "room")
    for settlement in settlements:
        _create_line(
            account_statement=account_statement,
            line_type=AccountLineType.OWNER_PAYOUT,
            description=f"Pago al propietario - {settlement.reservation}",
            effective_date=settlement.payment_date or settlement.reservation.date,
            room=settlement.room,
            reservation=settlement.reservation,
            settlement=settlement,
            amount=settlement.owner_net,
            source_model=settlement._meta.label,
            source_id=f"{settlement.pk}:payout",
            source_snapshot={
                "payment_reference": settlement.payment_reference,
                "payment_date": (
                    settlement.payment_date.isoformat()
                    if settlement.payment_date
                    else None
                ),
                "owner_net": str(settlement.owner_net),
            },
            actor=actor,
        )


def _create_tenant_reservation_payment_lines(
    account_statement: AccountStatement,
    *,
    actor: Model | None,
) -> None:
    reservation_filter = {
        "reservation__tenant_doctor": account_statement.tenant_doctor,
        "reservation__date__range": (
            account_statement.period_start,
            account_statement.period_end,
        ),
    }
    allocations = PaymentAllocation.objects.filter(
        **reservation_filter,
        payment__status=PaymentStatus.VALIDATED,
        payment__currency=account_statement.currency,
        payment__is_deleted=False,
        is_deleted=False,
    ).select_related("payment", "reservation", "reservation__room")
    for allocation in allocations:
        _create_line(
            account_statement=account_statement,
            line_type=AccountLineType.RESERVATION_PAYMENT,
            description=f"Pago de reservación {allocation.reservation}",
            effective_date=allocation.payment.payment_date,
            room=allocation.reservation.room,
            reservation=allocation.reservation,
            amount=allocation.amount,
            source_model=allocation._meta.label,
            source_id=str(allocation.pk),
            source_snapshot={
                "payment_id": str(allocation.payment_id),
                "reference": allocation.payment.reference,
                "amount": str(allocation.amount),
            },
            actor=actor,
        )
    legacy_payments = Payment.objects.filter(
        reservation__tenant_doctor=account_statement.tenant_doctor,
        reservation__date__range=(
            account_statement.period_start,
            account_statement.period_end,
        ),
        status=PaymentStatus.VALIDATED,
        currency=account_statement.currency,
        allocations__isnull=True,
        is_deleted=False,
    ).select_related("reservation", "reservation__room")
    for payment in legacy_payments:
        if payment.reservation is None:
            continue
        _create_line(
            account_statement=account_statement,
            line_type=AccountLineType.RESERVATION_PAYMENT,
            description=f"Pago de reservación {payment.reservation}",
            effective_date=payment.payment_date,
            room=payment.reservation.room,
            reservation=payment.reservation,
            amount=payment.amount,
            source_model=payment._meta.label,
            source_id=str(payment.pk),
            source_snapshot={
                "reference": payment.reference,
                "amount": str(payment.amount),
            },
            actor=actor,
        )


def _create_validated_account_payment_lines(
    account_statement: AccountStatement,
    *,
    actor: Model | None,
) -> None:
    payments = AccountPayment.objects.filter(
        account_statement=account_statement,
        status=PaymentStatus.VALIDATED,
        is_deleted=False,
    )
    for payment in payments:
        line_type = (
            AccountLineType.OWNER_PAYMENT
            if account_statement.party_type == AccountPartyType.OWNER
            else AccountLineType.TENANT_SUBSCRIPTION_PAYMENT
        )
        _create_line(
            account_statement=account_statement,
            line_type=line_type,
            description=payment.get_category_display(),
            effective_date=payment.payment_date,
            amount=payment.amount,
            source_model=payment._meta.label,
            source_id=str(payment.pk),
            source_snapshot={
                "category": payment.category,
                "method": payment.method,
                "reference": payment.reference,
                "amount": str(payment.amount),
            },
            actor=actor,
        )


def _create_line(
    *,
    account_statement: AccountStatement,
    line_type: str,
    description: str,
    effective_date: date,
    amount: Decimal,
    source_model: str,
    source_id: str,
    actor: Model | None,
    coverage_start: date | None = None,
    coverage_end: date | None = None,
    room: Any = None,
    reservation: Any = None,
    settlement: Any = None,
    source_snapshot: dict[str, Any] | None = None,
) -> AccountStatementLine | None:
    if amount == ZERO:
        return None
    line, created = AccountStatementLine.objects.get_or_create(
        account_statement=account_statement,
        line_type=line_type,
        source_model=source_model,
        source_id=source_id,
        defaults={
            "description": description,
            "effective_date": effective_date,
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
            "room": room,
            "reservation": reservation,
            "settlement": settlement,
            "amount": amount,
            "source_snapshot": source_snapshot or {},
            "created_by": cast(Any, actor) if actor is not None else None,
            "updated_by": cast(Any, actor) if actor is not None else None,
        },
    )
    if created:
        record_event(
            event_type="account_statement.line_created",
            object_label=str(line),
            actor=actor,
            payload={
                "model": line._meta.label,
                "id": str(line.pk),
                "account_statement_id": str(account_statement.pk),
                "line_type": line.line_type,
                "amount": str(line.amount),
                "source_model": source_model,
                "source_id": source_id,
                "level": "financiero",
                "actor_id": str(actor.pk) if actor is not None else "",
            },
        )
    return line


def _line_totals(account_statement: AccountStatement) -> dict[str, Decimal]:
    totals = {choice: ZERO for choice in AccountLineType.values}
    rows = (
        account_statement.lines.filter(is_deleted=False)
        .values("line_type")
        .annotate(total=Sum("amount"))
    )
    for row in rows:
        totals[row["line_type"]] = row["total"] or ZERO
    return totals


def _owner_schedule_for_date(
    owner: OwnerProfile,
    target_date: date,
) -> OwnerPayoutSchedule:
    schedule = (
        OwnerPayoutSchedule.objects.filter(
            owner=owner,
            is_active=True,
            is_deleted=False,
            start_date__lte=target_date,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=target_date))
        .order_by("-start_date", "-version")
        .first()
    )
    if schedule is None:
        raise ValidationError(
            {"owner": "El propietario no tiene una frecuencia de pago vigente."}
        )
    return schedule


def _validate_owner_period(
    period_start: date,
    period_end: date,
    frequency: str,
) -> None:
    if period_end < period_start:
        raise ValidationError({"period_end": "El periodo es inválido."})
    if frequency == PayoutFrequency.WEEKLY:
        valid = period_start.weekday() == 0 and period_end == period_start + timedelta(
            days=6
        )
    elif frequency == PayoutFrequency.BIWEEKLY:
        valid = (
            period_start.day == 1
            and period_end == period_start.replace(day=15)
        ) or (
            period_start.day == 16
            and period_end == _month_end(period_start)
        )
    else:
        valid = period_start.day == 1 and period_end == _month_end(period_start)
    if not valid:
        raise ValidationError(
            {"period_end": "El periodo no coincide con la frecuencia del propietario."}
        )


def _validate_monthly_period(period_start: date, period_end: date) -> None:
    if period_start.day != 1 or period_end != _month_end(period_start):
        raise ValidationError(
            {"period_end": "El estado del arrendatario debe cubrir un mes completo."}
        )


def _subscription_due_dates(
    rule: OwnerSubscription | TenantSubscription,
    period_start: date,
    period_end: date,
) -> Iterable[date]:
    cycle_months = 12 if rule.billing_cycle == SubscriptionCycle.ANNUAL else 1
    cycle_index = 0
    due_date = rule.start_date
    while due_date < period_start:
        cycle_index += 1
        due_date = _add_months(rule.start_date, cycle_months * cycle_index)
    while due_date <= period_end and (
        rule.end_date is None or due_date <= rule.end_date
    ):
        yield due_date
        cycle_index += 1
        due_date = _add_months(rule.start_date, cycle_months * cycle_index)


def _subscription_coverage_end(
    rule: OwnerSubscription | TenantSubscription,
    due_date: date,
) -> date:
    end_date = _next_subscription_date(due_date, rule.billing_cycle) - timedelta(days=1)
    return min(end_date, rule.end_date) if rule.end_date else end_date


def _next_subscription_date(due_date: date, billing_cycle: str) -> date:
    months = 12 if billing_cycle == SubscriptionCycle.ANNUAL else 1
    return _add_months(due_date, months)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _month_starts(period_start: date, period_end: date) -> Iterable[date]:
    current = period_start.replace(day=1)
    while current <= period_end:
        yield current
        current = _add_months(current, 1)


def _month_end(value: date) -> date:
    return value.replace(day=monthrange(value.year, value.month)[1])


def _previous_month(value: date) -> tuple[date, date]:
    previous_end = value.replace(day=1) - timedelta(days=1)
    return previous_end.replace(day=1), previous_end


def _set_audit_users(
    instance: Any,
    actor: Model | None,
    *,
    created: bool = False,
) -> None:
    if actor is None:
        return
    if created:
        instance.created_by = cast(Any, actor)
    instance.updated_by = cast(Any, actor)


def _statement_payload(
    account_statement: AccountStatement,
    *,
    actor: Model | None,
) -> dict[str, str]:
    return {
        "model": account_statement._meta.label,
        "id": str(account_statement.pk),
        "party_type": account_statement.party_type,
        "owner_id": str(account_statement.owner_id or ""),
        "tenant_doctor_id": str(account_statement.tenant_doctor_id or ""),
        "period_start": account_statement.period_start.isoformat(),
        "period_end": account_statement.period_end.isoformat(),
        "balance_due": str(account_statement.balance_due),
        "payout_due": str(account_statement.payout_due),
        "currency": account_statement.currency,
        "level": "financiero",
        "actor_id": str(actor.pk) if actor is not None else "",
    }


def _account_payment_payload(
    payment: AccountPayment,
    *,
    actor: Model | None,
) -> dict[str, str]:
    return {
        "model": payment._meta.label,
        "id": str(payment.pk),
        "account_statement_id": str(payment.account_statement_id),
        "category": payment.category,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "method": payment.method,
        "reference": payment.reference,
        "status": payment.status,
        "level": "financiero",
        "actor_id": str(actor.pk) if actor is not None else "",
    }


def _owner_payout_payload(
    payout: OwnerPayout,
    *,
    actor: Model | None,
) -> dict[str, str]:
    return {
        "model": payout._meta.label,
        "id": str(payout.pk),
        "account_statement_id": str(payout.account_statement_id),
        "amount": str(payout.amount),
        "deducted_fees": str(payout.deducted_fees),
        "currency": payout.currency,
        "reference": payout.reference,
        "payment_date": payout.payment_date.isoformat(),
        "level": "financiero",
        "actor_id": str(actor.pk) if actor is not None else "",
    }


def _copy_statement_state(target: AccountStatement, source: AccountStatement) -> None:
    for field_name in (
        "rental_income",
        "reservation_charges",
        "commissions",
        "owner_net",
        "subscription_charges",
        "direct_fees",
        "deducted_fees",
        "reservation_payments",
        "account_payments_total",
        "payouts_made",
        "balance_due",
        "payout_due",
        "refreshed_at",
    ):
        setattr(target, field_name, getattr(source, field_name))


def _copy_account_payment_state(target: AccountPayment, source: AccountPayment) -> None:
    target.status = source.status
    target.validated_at = source.validated_at
    target.validated_by = source.validated_by
    target.rejected_reason = source.rejected_reason
