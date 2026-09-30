"""Versioned commercial configuration models."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Max, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.constants import DEFAULT_CURRENCY
from apps.core.models import BaseModel


class SubscriptionCycle(models.TextChoices):
    MONTHLY = "monthly", _("Mensual")
    ANNUAL = "annual", _("Anual")


class PayoutFrequency(models.TextChoices):
    WEEKLY = "weekly", _("Semanal")
    BIWEEKLY = "biweekly", _("Quincenal")
    MONTHLY = "monthly", _("Mensual")


class RoomFeeType(models.TextChoices):
    MAINTENANCE = "maintenance", _("Mantenimiento")
    INTERNET = "internet", _("Internet")
    ELECTRICITY = "electricity", _("Electricidad")
    CLEANING = "cleaning", _("Limpieza")
    RECEPTIONIST = "receptionist", _("Recepcionista")
    MISCELLANEOUS = "miscellaneous", _("Diversos")


class FeePaymentMode(models.TextChoices):
    DIRECT = "direct", _("Pago directo del propietario")
    AUTO_DEDUCT = "auto_deduct", _("Descuento automático al cierre")


def _date_ranges_overlap(left: Any, right: Any) -> bool:
    left_end = left.end_date or date.max
    right_end = right.end_date or date.max
    return left.start_date <= right_end and right.start_date <= left_end


def _validity_errors(start_date: date | None, end_date: date | None) -> dict[str, Any]:
    if start_date and end_date and end_date < start_date:
        return {
            "end_date": _(
                "La fecha fin de vigencia no puede ser menor que la fecha inicio."
            )
        }
    return {}


def _percentage_errors(percentage: Decimal | None) -> dict[str, Any]:
    if percentage is None:
        return {}
    if percentage < Decimal("0.0") or percentage > Decimal("100.0"):
        return {"percentage": _("El porcentaje debe estar entre 0% y 100%.")}
    return {}


class VersionedConfiguration(BaseModel):
    """Immutable business configuration with an activation lifecycle."""

    objects: models.Manager[Any] = models.Manager()
    version = models.PositiveIntegerField(_("versión"), default=1, editable=False)
    start_date = models.DateField(
        _("fecha inicio de vigencia"), default=timezone.localdate
    )
    end_date = models.DateField(
        _("fecha fin de vigencia"),
        blank=True,
        null=True,
    )
    notes = models.TextField(_("notas"), blank=True)

    immutable_fields: ClassVar[tuple[str, ...]] = (
        "version",
        "start_date",
        "end_date",
        "notes",
    )

    class Meta:
        abstract = True

    def version_scope(self) -> dict[str, Any]:
        raise NotImplementedError

    def clean(self) -> None:
        super().clean()
        errors = _validity_errors(self.start_date, self.end_date)
        errors.update(self._immutable_errors())
        if errors:
            raise ValidationError(errors)

    def _immutable_errors(self) -> dict[str, Any]:
        if self._state.adding or not self.pk:
            return {}
        previous = (
            type(self).objects.filter(pk=self.pk).values(*self.immutable_fields).first()
        )
        if previous is None:
            return {}
        changed = [
            field_name
            for field_name in self.immutable_fields
            if previous[field_name] != getattr(self, field_name)
        ]
        if not changed:
            return {}
        return {
            field_name: _(
                "Este registro es histórico. Desactívalo y crea una nueva versión."
            )
            for field_name in changed
        }

    def clean_active_overlap(self, **scope: Any) -> None:
        if not self.is_active or self.is_deleted:
            return
        queryset = (
            type(self)
            .objects.filter(
                **scope,
                is_active=True,
                is_deleted=False,
                start_date__lte=self.end_date or date.max,
            )
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=self.start_date))
        )
        if self.pk:
            queryset = queryset.exclude(pk=self.pk)
        if queryset.exists():
            raise ValidationError(
                {
                    "start_date": _(
                        "Ya existe una configuración activa que se traslapa con "
                        "esta vigencia. Desactívala antes de crear otra versión."
                    )
                }
            )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._state.adding:
            current_version = (
                type(self)
                .objects.filter(**self.version_scope())
                .aggregate(maximum=Max("version"))["maximum"]
                or 0
            )
            self.version = current_version + 1
        self.full_clean()
        super().save(*args, **kwargs)


class OwnerSubscription(VersionedConfiguration):
    owner = models.ForeignKey(
        "catalog.OwnerProfile",
        on_delete=models.PROTECT,
        related_name="subscription_rules",
        verbose_name=_("propietario"),
    )
    billing_cycle = models.CharField(
        _("periodicidad"),
        max_length=12,
        choices=SubscriptionCycle.choices,
        default=SubscriptionCycle.MONTHLY,
    )
    amount = models.DecimalField(_("importe"), max_digits=12, decimal_places=2)
    currency = models.CharField(_("moneda"), max_length=3, default=DEFAULT_CURRENCY)

    immutable_fields = VersionedConfiguration.immutable_fields + (
        "owner_id",
        "billing_cycle",
        "amount",
        "currency",
    )

    class Meta:
        verbose_name = _("suscripción de propietario")
        verbose_name_plural = _("suscripciones de propietarios")
        ordering = ("owner__display_name", "-start_date", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "version"),
                name="billing_owner_subscription_unique_version",
            ),
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name="billing_owner_subscription_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.owner} - {self.get_billing_cycle_display()} v{self.version}"

    def version_scope(self) -> dict[str, Any]:
        return {"owner_id": self.owner_id}

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if self.amount is not None and self.amount < Decimal("0"):
            errors["amount"] = _("El importe debe ser mayor o igual a cero.")
        if not self.currency.strip():
            errors["currency"] = _("La moneda es obligatoria.")
        if errors:
            raise ValidationError(errors)
        if self.owner_id:
            self.clean_active_overlap(owner_id=self.owner_id)


class TenantSubscription(VersionedConfiguration):
    tenant_doctor = models.ForeignKey(
        "catalog.TenantDoctorProfile",
        on_delete=models.PROTECT,
        related_name="subscription_rules",
        verbose_name=_("médico arrendatario"),
    )
    billing_cycle = models.CharField(
        _("periodicidad"),
        max_length=12,
        choices=SubscriptionCycle.choices,
        default=SubscriptionCycle.MONTHLY,
    )
    amount = models.DecimalField(_("importe"), max_digits=12, decimal_places=2)
    currency = models.CharField(_("moneda"), max_length=3, default=DEFAULT_CURRENCY)

    immutable_fields = VersionedConfiguration.immutable_fields + (
        "tenant_doctor_id",
        "billing_cycle",
        "amount",
        "currency",
    )

    class Meta:
        verbose_name = _("suscripción de médico arrendatario")
        verbose_name_plural = _("suscripciones de médicos arrendatarios")
        ordering = ("tenant_doctor__display_name", "-start_date", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("tenant_doctor", "version"),
                name="billing_tenant_subscription_unique_version",
            ),
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name="billing_tenant_subscription_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.tenant_doctor} - {self.get_billing_cycle_display()} "
            f"v{self.version}"
        )

    def version_scope(self) -> dict[str, Any]:
        return {"tenant_doctor_id": self.tenant_doctor_id}

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if self.amount is not None and self.amount < Decimal("0"):
            errors["amount"] = _("El importe debe ser mayor o igual a cero.")
        if not self.currency.strip():
            errors["currency"] = _("La moneda es obligatoria.")
        if errors:
            raise ValidationError(errors)
        if self.tenant_doctor_id:
            self.clean_active_overlap(tenant_doctor_id=self.tenant_doctor_id)


class OwnerCommissionRule(VersionedConfiguration):
    owner = models.ForeignKey(
        "catalog.OwnerProfile",
        on_delete=models.PROTECT,
        related_name="commission_rules",
        verbose_name=_("propietario"),
    )
    percentage = models.DecimalField(
        _("porcentaje de comisión"),
        max_digits=4,
        decimal_places=1,
    )

    immutable_fields = VersionedConfiguration.immutable_fields + (
        "owner_id",
        "percentage",
    )

    class Meta:
        verbose_name = _("regla de comisión del propietario")
        verbose_name_plural = _("reglas de comisión de propietarios")
        ordering = ("owner__display_name", "-start_date", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "version"),
                name="billing_owner_commission_unique_version",
            ),
            models.CheckConstraint(
                condition=Q(percentage__gte=0) & Q(percentage__lte=100),
                name="billing_owner_commission_percentage_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.owner} - {self.percentage}% v{self.version}"

    def version_scope(self) -> dict[str, Any]:
        return {"owner_id": self.owner_id}

    def clean(self) -> None:
        super().clean()
        errors = _percentage_errors(self.percentage)
        if errors:
            raise ValidationError(errors)
        if self.owner_id:
            self.clean_active_overlap(owner_id=self.owner_id)


class OwnerPayoutSchedule(VersionedConfiguration):
    owner = models.ForeignKey(
        "catalog.OwnerProfile",
        on_delete=models.PROTECT,
        related_name="payout_schedules",
        verbose_name=_("propietario"),
    )
    frequency = models.CharField(
        _("frecuencia de pago"),
        max_length=12,
        choices=PayoutFrequency.choices,
    )

    immutable_fields = VersionedConfiguration.immutable_fields + (
        "owner_id",
        "frequency",
    )

    class Meta:
        verbose_name = _("frecuencia de pago al propietario")
        verbose_name_plural = _("frecuencias de pago a propietarios")
        ordering = ("owner__display_name", "-start_date", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "version"),
                name="billing_owner_payout_unique_version",
            )
        ]

    def __str__(self) -> str:
        return f"{self.owner} - {self.get_frequency_display()} v{self.version}"

    def version_scope(self) -> dict[str, Any]:
        return {"owner_id": self.owner_id}

    def clean(self) -> None:
        super().clean()
        if self.owner_id:
            self.clean_active_overlap(owner_id=self.owner_id)


class RoomFixedFeeRule(VersionedConfiguration):
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        on_delete=models.PROTECT,
        related_name="fixed_fee_rules",
        verbose_name=_("consultorio"),
    )
    fee_type = models.CharField(
        _("tipo de cuota"),
        max_length=24,
        choices=RoomFeeType.choices,
    )
    amount = models.DecimalField(_("importe"), max_digits=12, decimal_places=2)
    currency = models.CharField(_("moneda"), max_length=3, default=DEFAULT_CURRENCY)
    payment_mode = models.CharField(
        _("forma de pago"),
        max_length=16,
        choices=FeePaymentMode.choices,
        default=FeePaymentMode.DIRECT,
    )

    immutable_fields = VersionedConfiguration.immutable_fields + (
        "room_id",
        "fee_type",
        "amount",
        "currency",
        "payment_mode",
    )

    class Meta:
        verbose_name = _("regla de cuota fija")
        verbose_name_plural = _("reglas de cuotas fijas")
        ordering = ("room__clinic__name", "room__name", "fee_type", "-start_date")
        constraints = [
            models.UniqueConstraint(
                fields=("room", "fee_type", "version"),
                name="billing_room_fixed_fee_unique_version",
            ),
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name="billing_room_fixed_fee_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.room} - {self.get_fee_type_display()} v{self.version}"

    def version_scope(self) -> dict[str, Any]:
        return {"room_id": self.room_id, "fee_type": self.fee_type}

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if self.amount is not None and self.amount < Decimal("0"):
            errors["amount"] = _("El importe debe ser mayor o igual a cero.")
        if not self.currency.strip():
            errors["currency"] = _("La moneda es obligatoria.")
        if errors:
            raise ValidationError(errors)
        if self.room_id and self.fee_type:
            self.clean_active_overlap(room_id=self.room_id, fee_type=self.fee_type)


class RoomMonthlyFee(VersionedConfiguration):
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        on_delete=models.PROTECT,
        related_name="monthly_fees",
        verbose_name=_("consultorio"),
    )
    fee_type = models.CharField(
        _("tipo de cuota"),
        max_length=24,
        choices=RoomFeeType.choices,
    )
    billing_month = models.DateField(_("mes de cobro"))
    amount = models.DecimalField(_("importe"), max_digits=12, decimal_places=2)
    currency = models.CharField(_("moneda"), max_length=3, default=DEFAULT_CURRENCY)
    payment_mode = models.CharField(
        _("forma de pago"),
        max_length=16,
        choices=FeePaymentMode.choices,
        default=FeePaymentMode.DIRECT,
    )

    immutable_fields = VersionedConfiguration.immutable_fields + (
        "room_id",
        "fee_type",
        "billing_month",
        "amount",
        "currency",
        "payment_mode",
    )

    class Meta:
        verbose_name = _("cuota mensual variable")
        verbose_name_plural = _("cuotas mensuales variables")
        ordering = ("-billing_month", "room__clinic__name", "room__name", "fee_type")
        constraints = [
            models.UniqueConstraint(
                fields=("room", "fee_type", "billing_month", "version"),
                name="billing_room_monthly_fee_unique_version",
            ),
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name="billing_room_monthly_fee_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.room} - {self.get_fee_type_display()} "
            f"{self.billing_month:%Y-%m} v{self.version}"
        )

    def version_scope(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "fee_type": self.fee_type,
            "billing_month": self.billing_month,
        }

    def clean(self) -> None:
        if self.billing_month:
            self.start_date = self.billing_month.replace(day=1)
            if self.billing_month.month == 12:
                next_month = self.start_date.replace(
                    year=self.billing_month.year + 1,
                    month=1,
                )
            else:
                next_month = self.start_date.replace(
                    month=self.billing_month.month + 1,
                )
            self.end_date = next_month - timedelta(days=1)
        super().clean()
        errors: dict[str, Any] = {}
        if self.billing_month and self.billing_month.day != 1:
            errors["billing_month"] = _("Selecciona el mes, sin un día específico.")
        if self.amount is not None and self.amount < Decimal("0"):
            errors["amount"] = _("El importe debe ser mayor o igual a cero.")
        if not self.currency.strip():
            errors["currency"] = _("La moneda es obligatoria.")
        if errors:
            raise ValidationError(errors)
        if self.room_id and self.fee_type and self.billing_month:
            self.clean_active_overlap(
                room_id=self.room_id,
                fee_type=self.fee_type,
                billing_month=self.billing_month,
            )


class CancellationPolicy(VersionedConfiguration):
    clinic = models.ForeignKey(
        "catalog.Clinic",
        on_delete=models.PROTECT,
        related_name="cancellation_policies",
        verbose_name=_("clínica"),
    )
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="cancellation_policies",
        verbose_name=_("consultorio específico"),
    )
    name = models.CharField(_("nombre"), max_length=160)

    immutable_fields = VersionedConfiguration.immutable_fields + (
        "clinic_id",
        "room_id",
        "name",
    )

    class Meta:
        verbose_name = _("política de cancelación")
        verbose_name_plural = _("políticas de cancelación")
        ordering = ("clinic__name", "room__name", "-start_date", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("clinic", "room", "version"),
                name="billing_cancellation_policy_unique_version",
            )
        ]

    def __str__(self) -> str:
        scope = self.room or self.clinic
        return f"{scope} - {self.name} v{self.version}"

    def version_scope(self) -> dict[str, Any]:
        return {"clinic_id": self.clinic_id, "room_id": self.room_id}

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if not self.name.strip():
            errors["name"] = _("El nombre es obligatorio.")
        room = self.room
        if room is not None and self.clinic_id and room.clinic_id != self.clinic_id:
            errors["room"] = _("El consultorio debe pertenecer a la clínica elegida.")
        if errors:
            raise ValidationError(errors)
        if self.clinic_id:
            self.clean_active_overlap(clinic_id=self.clinic_id, room_id=self.room_id)


class CancellationPenaltyRule(BaseModel):
    policy = models.ForeignKey(
        CancellationPolicy,
        on_delete=models.PROTECT,
        related_name="penalty_rules",
        verbose_name=_("política"),
    )
    days_before = models.PositiveSmallIntegerField(_("días naturales antes"))
    percentage = models.DecimalField(
        _("porcentaje de penalización"),
        max_digits=4,
        decimal_places=1,
    )

    class Meta:
        verbose_name = _("regla de penalización por cancelación")
        verbose_name_plural = _("reglas de penalización por cancelación")
        ordering = ("policy", "days_before")
        constraints = [
            models.UniqueConstraint(
                fields=("policy", "days_before"),
                name="billing_cancellation_penalty_unique_day",
            ),
            models.CheckConstraint(
                condition=Q(percentage__gte=0) & Q(percentage__lte=100),
                name="billing_cancellation_penalty_percentage_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.days_before} días: {self.percentage}%"

    def clean(self) -> None:
        super().clean()
        errors = _percentage_errors(self.percentage)
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding and self.pk:
            previous = type(self).objects.get(pk=self.pk)
            if (
                previous.policy_id != self.policy_id
                or previous.days_before != self.days_before
                or previous.percentage != self.percentage
            ):
                raise ValidationError(
                    _(
                        "Las reglas históricas no se editan. Desactiva la política "
                        "y crea una nueva versión."
                    )
                )
        self.full_clean()
        super().save(*args, **kwargs)
