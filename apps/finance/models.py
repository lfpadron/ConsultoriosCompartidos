"""Finance models prepared for monetary workflows."""

from datetime import date, time
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.constants import DEFAULT_CURRENCY
from apps.core.models import BaseModel
from apps.scheduling.models import ReservationStatus


class PriceType(models.TextChoices):
    HOURLY = "por_hora", _("Por hora")
    BLOCK = "por_bloque", _("Por bloque")


class RateRule(BaseModel):
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        on_delete=models.PROTECT,
        related_name="rate_rules",
    )
    name = models.CharField(_("nombre"), max_length=160)
    weekdays = models.JSONField(_("días de semana"), default=list)
    start_time = models.TimeField(_("hora inicio"), default=time(8, 0))
    end_time = models.TimeField(_("hora fin"), default=time(9, 0))
    start_date = models.DateField(_("fecha inicio"), default=timezone.localdate)
    end_date = models.DateField(_("fecha fin"), blank=True, null=True)
    price_type = models.CharField(
        _("tipo de precio"),
        max_length=16,
        choices=PriceType.choices,
        default=PriceType.HOURLY,
    )
    amount = models.DecimalField(_("importe"), max_digits=12, decimal_places=2)
    currency = models.CharField(_("moneda"), max_length=3, default=DEFAULT_CURRENCY)
    priority = models.PositiveIntegerField(_("prioridad"), default=1)
    notes = models.TextField(_("notas"), blank=True)

    class Meta:
        verbose_name = _("regla tarifaria")
        verbose_name_plural = _("reglas tarifarias")
        ordering = ("room__name", "-priority", "start_time")

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        self.weekdays = _normalize_weekdays(self.weekdays)

        if not self.name.strip():
            errors["name"] = _("El nombre es obligatorio.")
        if not self.weekdays:
            errors["weekdays"] = _("Selecciona al menos un día de semana.")
        if self.start_time >= self.end_time:
            errors["end_time"] = _("La hora fin debe ser mayor que la hora inicio.")
        if self.end_date and self.end_date < self.start_date:
            errors["end_date"] = _(
                "La fecha fin no puede ser menor que la fecha inicio."
            )
        if self.amount < Decimal("0"):
            errors["amount"] = _("El importe debe ser mayor o igual a cero.")
        if self.priority < 1:
            errors["priority"] = _("La prioridad debe ser un entero positivo.")
        if not self.currency.strip():
            errors["currency"] = _("La moneda es obligatoria.")

        if self.room_id and self.is_active and not self.is_deleted:
            for rule in RateRule.objects.filter(
                room_id=self.room_id,
                is_active=True,
                is_deleted=False,
            ).exclude(pk=self.pk):
                if _is_exact_duplicate(self, rule):
                    errors["name"] = _(
                        "Ya existe una regla tarifaria activa exactamente igual."
                    )
                    break
                if _rules_overlap(self, rule) and self.priority == rule.priority:
                    errors["priority"] = _(
                        "Las reglas traslapadas deben tener prioridades diferentes."
                    )
                    break

        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


def _normalize_weekdays(value: Any) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError({"weekdays": _("Los días deben enviarse como lista.")})

    normalized = sorted({int(item) for item in value})
    invalid_days = [day for day in normalized if day < 0 or day > 6]
    if invalid_days:
        raise ValidationError({"weekdays": _("Los días deben estar entre 0 y 6.")})
    return normalized


def _is_exact_duplicate(left: RateRule, right: RateRule) -> bool:
    return (
        left.weekdays == right.weekdays
        and left.start_time == right.start_time
        and left.end_time == right.end_time
        and left.start_date == right.start_date
        and left.end_date == right.end_date
        and left.price_type == right.price_type
        and left.amount == right.amount
        and left.currency == right.currency
        and left.priority == right.priority
    )


def _rules_overlap(left: RateRule, right: RateRule) -> bool:
    if not set(left.weekdays).intersection(right.weekdays):
        return False
    if left.start_time >= right.end_time or left.end_time <= right.start_time:
        return False
    return _date_ranges_overlap(left, right)


def _date_ranges_overlap(left: RateRule, right: RateRule) -> bool:
    left_end = left.end_date or date.max
    right_end = right.end_date or date.max
    return left.start_date <= right_end and right.start_date <= left_end


class StatementStatus(models.TextChoices):
    CURRENT = "vigente", _("Vigente")
    REPLACED = "reemplazado", _("Reemplazado")
    CANCELLED = "cancelado", _("Cancelado")


class Statement(BaseModel):
    reservation = models.ForeignKey(
        "scheduling.Reservation",
        on_delete=models.PROTECT,
        related_name="statements",
    )
    version = models.PositiveIntegerField(_("versión"), default=1)
    status = models.CharField(
        _("estado"),
        max_length=24,
        choices=StatementStatus.choices,
        default=StatementStatus.CURRENT,
    )
    currency = models.CharField(_("moneda"), max_length=3, default=DEFAULT_CURRENCY)
    duration_hours = models.DecimalField(
        _("duración horas"), max_digits=8, decimal_places=2, default=0
    )
    subtotal = models.DecimalField(
        _("subtotal"), max_digits=12, decimal_places=2, default=0
    )
    discounts = models.DecimalField(
        _("descuentos"), max_digits=12, decimal_places=2, default=0
    )
    taxes = models.DecimalField(
        _("impuestos"), max_digits=12, decimal_places=2, default=0
    )
    total_doctor = models.DecimalField(
        _("total médico"), max_digits=12, decimal_places=2, default=0
    )
    platform_commission = models.DecimalField(
        _("comisión plataforma"), max_digits=12, decimal_places=2, default=0
    )
    commission_taxes = models.DecimalField(
        _("impuestos comisión"), max_digits=12, decimal_places=2, default=0
    )
    owner_net = models.DecimalField(
        _("neto propietario"), max_digits=12, decimal_places=2, default=0
    )
    applied_rate_rule = models.ForeignKey(
        RateRule,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="statements",
    )
    calculation_explanation = models.TextField(_("explicación de cálculo"), blank=True)
    calculation_hash = models.CharField(_("hash de cálculo"), max_length=64, blank=True)
    generated_at = models.DateTimeField(_("generado en"), default=timezone.now)

    class Meta:
        verbose_name = _("estado de cuenta")
        verbose_name_plural = _("estados de cuenta")
        ordering = ("reservation__date", "reservation__start_time", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("reservation", "version"),
                name="finance_statement_unique_reservation_version",
            )
        ]

    def __str__(self) -> str:
        return f"{self.reservation} v{self.version}"


class PaymentMethod(models.TextChoices):
    TRANSFER = "transferencia", _("Transferencia")
    CASH = "efectivo", _("Efectivo")
    CARD = "tarjeta", _("Tarjeta")
    DEPOSIT = "depósito", _("Depósito")
    OTHER = "otro", _("Otro")


class PaymentStatus(models.TextChoices):
    REGISTERED = "registrado", _("Registrado")
    VALIDATED = "validado", _("Validado")
    REJECTED = "rechazado", _("Rechazado")
    CANCELLED = "cancelado", _("Cancelado")


class Payment(BaseModel):
    reservation = models.ForeignKey(
        "scheduling.Reservation",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    statement = models.ForeignKey(
        Statement,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    tenant_doctor = models.ForeignKey(
        "catalog.TenantDoctorProfile",
        on_delete=models.PROTECT,
        related_name="payments",
    )
    amount = models.DecimalField(_("importe"), max_digits=12, decimal_places=2)
    currency = models.CharField(_("moneda"), max_length=3, default=DEFAULT_CURRENCY)
    method = models.CharField(
        _("método"),
        max_length=24,
        choices=PaymentMethod.choices,
        default=PaymentMethod.TRANSFER,
    )
    reference = models.CharField(_("referencia"), max_length=160, blank=True)
    payment_date = models.DateField(_("fecha de pago"), default=timezone.localdate)
    receipt = models.FileField(
        _("comprobante"),
        upload_to="payment-receipts/",
        blank=True,
        null=True,
    )
    status = models.CharField(
        _("estado"),
        max_length=24,
        choices=PaymentStatus.choices,
        default=PaymentStatus.REGISTERED,
    )
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="validated_payments",
    )
    validated_at = models.DateTimeField(_("validado en"), blank=True, null=True)
    rejected_reason = models.TextField(_("motivo de rechazo"), blank=True)
    notes = models.TextField(_("notas"), blank=True)

    class Meta:
        verbose_name = _("pago")
        verbose_name_plural = _("pagos")
        ordering = ("-payment_date", "-created_at")

    def __str__(self) -> str:
        return f"{self.reservation} - {self.amount} {self.currency}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}

        if self.amount <= Decimal("0"):
            errors["amount"] = _("El importe debe ser mayor que cero.")
        if not self.currency.strip():
            errors["currency"] = _("La moneda es obligatoria.")
        if self.method != PaymentMethod.CASH and not self.reference.strip():
            errors["reference"] = _(
                "La referencia es obligatoria salvo pagos en efectivo."
            )

        if self.reservation_id:
            if self.reservation.status == ReservationStatus.CANCELLED:
                errors["reservation"] = _(
                    "No se permiten pagos para reservaciones canceladas."
                )
            if self.tenant_doctor_id and self.tenant_doctor_id != (
                self.reservation.tenant_doctor_id
            ):
                errors["tenant_doctor"] = _(
                    "El médico arrendatario no corresponde a la reservación."
                )

        if self.statement_id:
            if (
                self.reservation_id
                and self.statement.reservation_id != self.reservation_id
            ):
                errors["statement"] = _(
                    "El estado de cuenta no corresponde a la reservación."
                )
            if self.currency and self.statement.currency != self.currency:
                errors["currency"] = _(
                    "La moneda debe coincidir con el estado de cuenta."
                )

        previous_status = self._previous_status()
        if self.status == PaymentStatus.VALIDATED and previous_status in {
            PaymentStatus.REJECTED,
            PaymentStatus.CANCELLED,
        }:
            errors["status"] = _("No se puede validar un pago rechazado o cancelado.")
        if (
            self.status == PaymentStatus.CANCELLED
            and previous_status == PaymentStatus.VALIDATED
        ):
            errors["status"] = _("No se puede cancelar un pago validado.")
        if self.status == PaymentStatus.REJECTED and not self.rejected_reason.strip():
            errors["rejected_reason"] = _("El motivo de rechazo es obligatorio.")

        if self.status == PaymentStatus.VALIDATED and self.statement_id:
            validated_total = Payment.objects.filter(
                statement_id=self.statement_id,
                status=PaymentStatus.VALIDATED,
                is_deleted=False,
            ).exclude(pk=self.pk).aggregate(total=Sum("amount"))["total"] or Decimal(
                "0.00"
            )
            if validated_total + self.amount > self.statement.total_doctor:
                errors["amount"] = _(
                    "La suma de pagos validados no puede exceder el total médico."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def _previous_status(self) -> str | None:
        if not self.pk:
            return None
        return (
            Payment.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        )


class SettlementStatus(models.TextChoices):
    PENDING = "pendiente", _("Pendiente")
    CALCULATED = "calculada", _("Calculada")
    PAID = "pagada", _("Pagada")
    CANCELLED = "cancelada", _("Cancelada")


class Settlement(BaseModel):
    reservation = models.ForeignKey(
        "scheduling.Reservation",
        on_delete=models.PROTECT,
        related_name="settlements",
    )
    statement = models.ForeignKey(
        Statement,
        on_delete=models.PROTECT,
        related_name="settlements",
    )
    owner = models.ForeignKey(
        "catalog.OwnerProfile",
        on_delete=models.PROTECT,
        related_name="settlements",
    )
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        on_delete=models.PROTECT,
        related_name="settlements",
    )
    currency = models.CharField(_("moneda"), max_length=3, default=DEFAULT_CURRENCY)
    reservation_subtotal = models.DecimalField(
        _("subtotal reservación"), max_digits=12, decimal_places=2
    )
    platform_commission = models.DecimalField(
        _("comisión plataforma"), max_digits=12, decimal_places=2
    )
    commission_taxes = models.DecimalField(
        _("impuestos comisión"), max_digits=12, decimal_places=2, default=0
    )
    owner_net = models.DecimalField(
        _("neto propietario"), max_digits=12, decimal_places=2
    )
    status = models.CharField(
        _("estado"),
        max_length=24,
        choices=SettlementStatus.choices,
        default=SettlementStatus.CALCULATED,
    )
    payment_reference = models.CharField(
        _("referencia de pago"),
        max_length=160,
        blank=True,
    )
    payment_date = models.DateField(_("fecha de pago"), blank=True, null=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="paid_settlements",
    )
    notes = models.TextField(_("notas"), blank=True)
    generated_at = models.DateTimeField(_("generada en"), default=timezone.now)
    paid_at = models.DateTimeField(_("pagada en"), blank=True, null=True)

    class Meta:
        verbose_name = _("liquidación")
        verbose_name_plural = _("liquidaciones")
        ordering = ("-generated_at",)

    def __str__(self) -> str:
        return f"{self.reservation} - {self.owner_net} {self.currency}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}

        if self.reservation_id:
            if self.reservation.status not in {
                ReservationStatus.PAID,
                ReservationStatus.CONFIRMED,
            }:
                errors["reservation"] = _(
                    "Sólo se puede liquidar una reservación pagada o confirmada."
                )

            active_duplicate = (
                Settlement.objects.filter(
                    reservation_id=self.reservation_id,
                    is_deleted=False,
                )
                .exclude(status=SettlementStatus.CANCELLED)
                .exclude(pk=self.pk)
                .exists()
            )
            if active_duplicate:
                errors["reservation"] = _(
                    "Ya existe una liquidación activa para esta reservación."
                )

            if self.room_id and self.room_id != self.reservation.room_id:
                errors["room"] = _("El consultorio no corresponde a la reservación.")

        if self.statement_id:
            if self.statement.status != StatementStatus.CURRENT:
                errors["statement"] = _("El estado de cuenta debe estar vigente.")
            if self.reservation_id and self.statement.reservation_id != (
                self.reservation_id
            ):
                errors["statement"] = _(
                    "El estado de cuenta no corresponde a la reservación."
                )
            if self.currency and self.currency != self.statement.currency:
                errors["currency"] = _(
                    "La moneda debe coincidir con el estado de cuenta."
                )
            if self.reservation_subtotal != self.statement.subtotal:
                errors["reservation_subtotal"] = _(
                    "El subtotal debe coincidir con el estado de cuenta vigente."
                )
            if self.platform_commission != self.statement.platform_commission:
                errors["platform_commission"] = _(
                    "La comisión debe coincidir con el estado de cuenta vigente."
                )
            if self.commission_taxes != self.statement.commission_taxes:
                errors["commission_taxes"] = _(
                    "Los impuestos de comisión deben coincidir con el estado de cuenta."
                )
            if self.owner_net != self.statement.owner_net:
                errors["owner_net"] = _(
                    "El neto propietario debe coincidir con el estado de cuenta "
                    "vigente."
                )

        if self.owner_id and self.room_id and self.owner_id != self.room.owner_id:
            errors["owner"] = _("El propietario no corresponde al consultorio.")

        previous_status = self._previous_status()
        if (
            self.status == SettlementStatus.PAID
            and previous_status == SettlementStatus.CANCELLED
        ):
            errors["status"] = _("No se puede pagar una liquidación cancelada.")
        if (
            self.status == SettlementStatus.CANCELLED
            and previous_status == SettlementStatus.PAID
        ):
            errors["status"] = _("No se puede cancelar una liquidación pagada.")
        if self.status == SettlementStatus.PAID and not self.payment_reference.strip():
            errors["payment_reference"] = _(
                "La referencia de pago es obligatoria al marcar como pagada."
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def _previous_status(self) -> str | None:
        if not self.pk:
            return None
        return (
            Settlement.objects.filter(pk=self.pk)
            .values_list("status", flat=True)
            .first()
        )
