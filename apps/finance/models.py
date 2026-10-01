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


class RoomRateDiscount(BaseModel):
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        on_delete=models.PROTECT,
        related_name="rate_discounts",
        verbose_name=_("consultorio"),
    )
    rate_rule = models.ForeignKey(
        RateRule,
        on_delete=models.PROTECT,
        related_name="room_discounts",
        verbose_name=_("regla tarifaria"),
    )
    percentage = models.DecimalField(
        _("porcentaje de descuento"),
        max_digits=4,
        decimal_places=1,
    )
    start_date = models.DateField(_("fecha inicio de vigencia"))
    end_date = models.DateField(_("fecha fin de vigencia"), blank=True, null=True)

    class Meta:
        verbose_name = _("descuento por consultorio")
        verbose_name_plural = _("descuentos por consultorio")
        ordering = ("room__clinic__name", "room__name", "-start_date", "-created_at")

    def __str__(self) -> str:
        return f"{self.room} - {self.rate_rule} - {self.percentage}%"

    def clean(self) -> None:
        super().clean()
        errors = _discount_validation_errors(
            self.percentage,
            self.start_date,
            self.end_date,
        )
        if (
            self.room_id
            and self.rate_rule_id
            and self.rate_rule.room_id != self.room_id
        ):
            errors["rate_rule"] = _(
                "La regla tarifaria debe pertenecer al consultorio seleccionado."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class TenantDoctorDiscount(BaseModel):
    tenant_doctor = models.ForeignKey(
        "catalog.TenantDoctorProfile",
        on_delete=models.PROTECT,
        related_name="discounts",
        verbose_name=_("médico arrendatario"),
    )
    percentage = models.DecimalField(
        _("porcentaje de descuento"),
        max_digits=4,
        decimal_places=1,
    )
    start_date = models.DateField(_("fecha inicio de vigencia"))
    end_date = models.DateField(_("fecha fin de vigencia"), blank=True, null=True)

    class Meta:
        verbose_name = _("descuento por médico arrendatario")
        verbose_name_plural = _("descuentos por médico arrendatario")
        ordering = ("tenant_doctor__display_name", "-start_date", "-created_at")

    def __str__(self) -> str:
        return f"{self.tenant_doctor} - {self.percentage}%"

    def clean(self) -> None:
        super().clean()
        errors = _discount_validation_errors(
            self.percentage,
            self.start_date,
            self.end_date,
        )
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


def _discount_validation_errors(
    percentage: Decimal,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, Any]:
    errors: dict[str, Any] = {}
    if percentage is not None:
        if percentage < Decimal("0.0"):
            errors["percentage"] = _("El porcentaje no puede ser negativo.")
        if percentage > Decimal("99.0"):
            errors["percentage"] = _("El porcentaje no puede exceder 99%.")
    if end_date and start_date and end_date < start_date:
        errors["end_date"] = _(
            "La fecha fin de vigencia no puede ser menor que la fecha inicio."
        )
    return errors


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
    room_discount_percentage = models.DecimalField(
        _("descuento consultorio %"), max_digits=4, decimal_places=1, default=0
    )
    tenant_discount_percentage = models.DecimalField(
        _("descuento médico arrendatario %"),
        max_digits=4,
        decimal_places=1,
        default=0,
    )
    tariff_total = models.DecimalField(
        _("tarifa total"), max_digits=12, decimal_places=2, default=0
    )
    tariff_final = models.DecimalField(
        _("tarifa final"), max_digits=12, decimal_places=2, default=0
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
    applied_room_discount = models.ForeignKey(
        RoomRateDiscount,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="statements",
    )
    applied_tenant_discount = models.ForeignKey(
        TenantDoctorDiscount,
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
    CREDIT = "saldo_favor", _("Saldo a favor")
    OTHER = "otro", _("Otro")


class PaymentStatus(models.TextChoices):
    REGISTERED = "registrado", _("Registrado")
    VALIDATED = "validado", _("Validado")
    REJECTED = "rechazado", _("Rechazado")
    CANCELLED = "cancelado", _("Cancelado")


class Payment(BaseModel):
    batch = models.ForeignKey(
        "scheduling.ReservationBatch",
        on_delete=models.PROTECT,
        related_name="payment_submissions",
        verbose_name=_("grupo de reservaciones"),
        blank=True,
        null=True,
    )
    reservation = models.ForeignKey(
        "scheduling.Reservation",
        on_delete=models.PROTECT,
        related_name="payments",
        blank=True,
        null=True,
    )
    statement = models.ForeignKey(
        Statement,
        on_delete=models.PROTECT,
        related_name="payments",
        blank=True,
        null=True,
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
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(batch__isnull=False)
                    | models.Q(
                        reservation__isnull=False,
                        statement__isnull=False,
                    )
                ),
                name="finance_payment_requires_subject",
            )
        ]

    def __str__(self) -> str:
        subject = self.batch or self.reservation
        return f"{subject} - {self.amount} {self.currency}"

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

        if self.batch_id:
            batch = self.batch
            assert batch is not None
            if batch.status in {"cancelled", "expired"}:
                errors["batch"] = _(
                    "No se permiten pagos para grupos cancelados o vencidos."
                )
            if (
                self.tenant_doctor_id
                and self.tenant_doctor_id != batch.tenant_doctor_id
            ):
                errors["tenant_doctor"] = _(
                    "El médico arrendatario no corresponde al grupo."
                )
            if self.currency and batch.currency != self.currency:
                errors["currency"] = _("La moneda debe coincidir con el grupo.")

        if self.reservation_id:
            reservation = self.reservation
            assert reservation is not None
            if reservation.status == ReservationStatus.CANCELLED:
                errors["reservation"] = _(
                    "No se permiten pagos para reservaciones canceladas."
                )
            if self.tenant_doctor_id and self.tenant_doctor_id != (
                reservation.tenant_doctor_id
            ):
                errors["tenant_doctor"] = _(
                    "El médico arrendatario no corresponde a la reservación."
                )

        if self.statement_id:
            statement = self.statement
            assert statement is not None
            if self.reservation_id and statement.reservation_id != self.reservation_id:
                errors["statement"] = _(
                    "El estado de cuenta no corresponde a la reservación."
                )
            if self.currency and statement.currency != self.currency:
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
            statement = self.statement
            assert statement is not None
            validated_total = Payment.objects.filter(
                statement_id=self.statement_id,
                status=PaymentStatus.VALIDATED,
                is_deleted=False,
            ).exclude(pk=self.pk).aggregate(total=Sum("amount"))["total"] or Decimal(
                "0.00"
            )
            if validated_total + self.amount > statement.total_doctor:
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


class PaymentAllocation(BaseModel):
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name=_("pago"),
    )
    reservation = models.ForeignKey(
        "scheduling.Reservation",
        on_delete=models.PROTECT,
        related_name="payment_allocations",
        verbose_name=_("reservación"),
    )
    statement = models.ForeignKey(
        Statement,
        on_delete=models.PROTECT,
        related_name="payment_allocations",
        verbose_name=_("estado de cuenta"),
    )
    amount = models.DecimalField(_("importe asignado"), max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = _("asignación de pago")
        verbose_name_plural = _("asignaciones de pago")
        ordering = ("reservation__date", "reservation__start_time", "created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("payment", "reservation"),
                name="finance_payment_allocation_unique_reservation",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="finance_payment_allocation_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.payment} -> {self.reservation}: {self.amount}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if self.amount <= Decimal("0.00"):
            errors["amount"] = _("El importe asignado debe ser mayor que cero.")
        if self.statement_id and self.reservation_id:
            if self.statement.reservation_id != self.reservation_id:
                errors["statement"] = _(
                    "El estado de cuenta no corresponde a la reservación."
                )
        if self.payment_id and self.payment.batch_id and self.reservation_id:
            if self.reservation.batch_id != self.payment.batch_id:
                errors["reservation"] = _(
                    "La reservación no pertenece al grupo del pago."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class CancellationCaseStatus(models.TextChoices):
    PENDING = "pendiente", _("Pendiente de resolución")
    RESOLVED = "resuelta", _("Resuelta")
    NO_REFUND = "sin_devolucion", _("Sin devolución")


class CancellationResolutionMethod(models.TextChoices):
    NOT_APPLICABLE = "no_aplica", _("No aplica")
    MANUAL_REFUND = "devolucion_manual", _("Devolución manual")
    FUTURE_CREDIT = "saldo_favor", _("Saldo a favor")


class CancellationCase(BaseModel):
    batch = models.ForeignKey(
        "scheduling.ReservationBatch",
        on_delete=models.PROTECT,
        related_name="cancellation_cases",
        verbose_name=_("grupo de reservaciones"),
        blank=True,
        null=True,
    )
    tenant_doctor = models.ForeignKey(
        "catalog.TenantDoctorProfile",
        on_delete=models.PROTECT,
        related_name="cancellation_cases",
        verbose_name=_("médico arrendatario"),
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="requested_cancellation_cases",
        verbose_name=_("solicitada por"),
        blank=True,
        null=True,
    )
    managed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="managed_cancellation_cases",
        verbose_name=_("gestionada por"),
        blank=True,
        null=True,
    )
    status = models.CharField(
        _("estado"),
        max_length=24,
        choices=CancellationCaseStatus.choices,
        default=CancellationCaseStatus.PENDING,
    )
    resolution_method = models.CharField(
        _("forma de resolución"),
        max_length=24,
        choices=CancellationResolutionMethod.choices,
        default=CancellationResolutionMethod.NOT_APPLICABLE,
    )
    reason = models.TextField(_("motivo"))
    currency = models.CharField(_("moneda"), max_length=3, default=DEFAULT_CURRENCY)
    total_paid = models.DecimalField(
        _("total pagado"), max_digits=12, decimal_places=2, default=0
    )
    penalty_amount = models.DecimalField(
        _("penalización"), max_digits=12, decimal_places=2, default=0
    )
    refundable_amount = models.DecimalField(
        _("importe a devolver"), max_digits=12, decimal_places=2, default=0
    )
    refund_reference = models.CharField(
        _("referencia de devolución"), max_length=160, blank=True
    )
    refund_date = models.DateField(_("fecha de devolución"), blank=True, null=True)
    refund_receipt = models.FileField(
        _("comprobante de devolución"),
        upload_to="refund-receipts/",
        blank=True,
        null=True,
    )
    requested_at = models.DateTimeField(_("solicitada en"), default=timezone.now)
    resolved_at = models.DateTimeField(_("resuelta en"), blank=True, null=True)
    notes = models.TextField(_("notas"), blank=True)

    class Meta:
        verbose_name = _("expediente de cancelación")
        verbose_name_plural = _("expedientes de cancelación")
        ordering = ("-requested_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(total_paid__gte=0),
                name="finance_cancellation_total_paid_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(penalty_amount__gte=0),
                name="finance_cancellation_penalty_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(refundable_amount__gte=0),
                name="finance_cancellation_refundable_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        reference = self.batch.reference if self.batch else str(self.pk)
        return f"Cancelación {reference} - {self.get_status_display()}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if not self.reason.strip():
            errors["reason"] = _("El motivo es obligatorio.")
        if self.total_paid < Decimal("0.00"):
            errors["total_paid"] = _("El total pagado no puede ser negativo.")
        if self.penalty_amount < Decimal("0.00"):
            errors["penalty_amount"] = _("La penalización no puede ser negativa.")
        if self.refundable_amount < Decimal("0.00"):
            errors["refundable_amount"] = _(
                "El importe a devolver no puede ser negativo."
            )
        if self.penalty_amount + self.refundable_amount != self.total_paid:
            errors["refundable_amount"] = _(
                "La penalización y la devolución deben sumar el total pagado."
            )
        if self.status == CancellationCaseStatus.RESOLVED:
            if self.resolution_method == CancellationResolutionMethod.NOT_APPLICABLE:
                errors["resolution_method"] = _(
                    "Selecciona la forma en que se resolvió la cancelación."
                )
            if self.resolved_at is None:
                errors["resolved_at"] = _("Registra la fecha de resolución.")
        if (
            self.resolution_method == CancellationResolutionMethod.MANUAL_REFUND
            and self.status == CancellationCaseStatus.RESOLVED
        ):
            if not self.refund_reference.strip():
                errors["refund_reference"] = _(
                    "La referencia de devolución es obligatoria."
                )
            if not self.refund_receipt:
                errors["refund_receipt"] = _(
                    "El comprobante de devolución es obligatorio."
                )
            if self.refund_date is None:
                errors["refund_date"] = _(
                    "La fecha de devolución es obligatoria."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class CancellationItem(BaseModel):
    cancellation_case = models.ForeignKey(
        CancellationCase,
        on_delete=models.PROTECT,
        related_name="items",
        verbose_name=_("expediente"),
    )
    reservation = models.OneToOneField(
        "scheduling.Reservation",
        on_delete=models.PROTECT,
        related_name="cancellation_item",
        verbose_name=_("reservación"),
    )
    cancellation_policy = models.ForeignKey(
        "billing.CancellationPolicy",
        on_delete=models.PROTECT,
        related_name="cancellation_items",
        verbose_name=_("política aplicada"),
        blank=True,
        null=True,
    )
    policy_snapshot = models.JSONField(
        _("snapshot de política"),
        default=dict,
        blank=True,
    )
    days_before = models.PositiveSmallIntegerField(_("días naturales antes"))
    penalty_percentage = models.DecimalField(
        _("porcentaje de penalización"), max_digits=4, decimal_places=1, default=0
    )
    paid_amount = models.DecimalField(
        _("importe pagado"), max_digits=12, decimal_places=2, default=0
    )
    penalty_amount = models.DecimalField(
        _("penalización"), max_digits=12, decimal_places=2, default=0
    )
    refundable_amount = models.DecimalField(
        _("importe a devolver"), max_digits=12, decimal_places=2, default=0
    )
    cancelled_at = models.DateTimeField(_("cancelada en"), default=timezone.now)

    class Meta:
        verbose_name = _("partida de cancelación")
        verbose_name_plural = _("partidas de cancelación")
        ordering = ("reservation__date", "reservation__start_time")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(penalty_percentage__gte=0)
                & models.Q(penalty_percentage__lte=100),
                name="finance_cancellation_item_percentage_range",
            ),
            models.CheckConstraint(
                condition=models.Q(paid_amount__gte=0)
                & models.Q(penalty_amount__gte=0)
                & models.Q(refundable_amount__gte=0),
                name="finance_cancellation_item_amounts_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reservation} - devolución {self.refundable_amount}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        percentage_out_of_range = self.penalty_percentage < Decimal(
            "0.0"
        ) or self.penalty_percentage > Decimal("100.0")
        if percentage_out_of_range:
            errors["penalty_percentage"] = _(
                "La penalización debe estar entre 0% y 100%."
            )
        if self.penalty_amount + self.refundable_amount != self.paid_amount:
            errors["refundable_amount"] = _(
                "La penalización y la devolución deben sumar el importe pagado."
            )
        if self.reservation_id and self.cancellation_case_id:
            case = self.cancellation_case
            if self.reservation.tenant_doctor_id != case.tenant_doctor_id:
                errors["reservation"] = _(
                    "La reservación no corresponde al médico del expediente."
                )
            if case.batch_id and self.reservation.batch_id != case.batch_id:
                errors["reservation"] = _(
                    "La reservación no pertenece al grupo del expediente."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class TenantCreditStatus(models.TextChoices):
    ACTIVE = "activo", _("Activo")
    EXHAUSTED = "agotado", _("Agotado")
    CANCELLED = "cancelado", _("Cancelado")


class TenantCredit(BaseModel):
    cancellation_case = models.OneToOneField(
        CancellationCase,
        on_delete=models.PROTECT,
        related_name="tenant_credit",
        verbose_name=_("expediente de cancelación"),
    )
    tenant_doctor = models.ForeignKey(
        "catalog.TenantDoctorProfile",
        on_delete=models.PROTECT,
        related_name="credits",
        verbose_name=_("médico arrendatario"),
    )
    original_amount = models.DecimalField(
        _("importe original"), max_digits=12, decimal_places=2
    )
    remaining_amount = models.DecimalField(
        _("saldo disponible"), max_digits=12, decimal_places=2
    )
    currency = models.CharField(_("moneda"), max_length=3, default=DEFAULT_CURRENCY)
    status = models.CharField(
        _("estado"),
        max_length=16,
        choices=TenantCreditStatus.choices,
        default=TenantCreditStatus.ACTIVE,
    )

    class Meta:
        verbose_name = _("saldo a favor")
        verbose_name_plural = _("saldos a favor")
        ordering = ("created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(original_amount__gt=0),
                name="finance_tenant_credit_original_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(remaining_amount__gte=0),
                name="finance_tenant_credit_remaining_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tenant_doctor}: {self.remaining_amount} {self.currency}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if self.original_amount <= Decimal("0.00"):
            errors["original_amount"] = _("El importe original debe ser positivo.")
        if self.remaining_amount < Decimal("0.00"):
            errors["remaining_amount"] = _("El saldo no puede ser negativo.")
        if self.remaining_amount > self.original_amount:
            errors["remaining_amount"] = _(
                "El saldo no puede exceder el importe original."
            )
        if self.cancellation_case_id and self.tenant_doctor_id:
            if self.cancellation_case.tenant_doctor_id != self.tenant_doctor_id:
                errors["tenant_doctor"] = _(
                    "El médico no corresponde al expediente de cancelación."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class TenantCreditApplicationStatus(models.TextChoices):
    RESERVED = "reservado", _("Reservado")
    APPLIED = "aplicado", _("Aplicado")
    RELEASED = "liberado", _("Liberado")


class TenantCreditApplication(BaseModel):
    credit = models.ForeignKey(
        TenantCredit,
        on_delete=models.PROTECT,
        related_name="applications",
        verbose_name=_("saldo a favor"),
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="credit_applications",
        verbose_name=_("pago"),
    )
    amount = models.DecimalField(_("importe aplicado"), max_digits=12, decimal_places=2)
    status = models.CharField(
        _("estado"),
        max_length=16,
        choices=TenantCreditApplicationStatus.choices,
        default=TenantCreditApplicationStatus.RESERVED,
    )
    applied_at = models.DateTimeField(_("aplicado en"), blank=True, null=True)
    released_at = models.DateTimeField(_("liberado en"), blank=True, null=True)

    class Meta:
        verbose_name = _("aplicación de saldo a favor")
        verbose_name_plural = _("aplicaciones de saldo a favor")
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("credit", "payment"),
                name="finance_credit_application_unique_payment",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="finance_credit_application_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.credit} -> {self.payment}: {self.amount}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if self.amount <= Decimal("0.00"):
            errors["amount"] = _("El importe aplicado debe ser positivo.")
        if self.credit_id and self.payment_id:
            if self.credit.tenant_doctor_id != self.payment.tenant_doctor_id:
                errors["payment"] = _(
                    "El pago y el saldo deben pertenecer al mismo médico."
                )
            if self.credit.currency != self.payment.currency:
                errors["payment"] = _("La moneda del saldo debe coincidir con el pago.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


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
