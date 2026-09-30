"""Scheduling models prepared for availability and calendar rules."""

import uuid
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.billing.models import VersionedConfiguration
from apps.core.constants import DEFAULT_CURRENCY
from apps.core.models import BaseModel


class Weekday(models.IntegerChoices):
    MONDAY = 0, _("Lunes")
    TUESDAY = 1, _("Martes")
    WEDNESDAY = 2, _("Miércoles")
    THURSDAY = 3, _("Jueves")
    FRIDAY = 4, _("Viernes")
    SATURDAY = 5, _("Sábado")
    SUNDAY = 6, _("Domingo")


class AvailabilityRule(BaseModel):
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        on_delete=models.PROTECT,
        related_name="availability_rules",
    )
    name = models.CharField(_("nombre"), max_length=160)
    weekday = models.PositiveSmallIntegerField(
        _("día de semana"),
        choices=Weekday.choices,
        default=Weekday.MONDAY,
    )
    weekdays = models.JSONField(_("días de semana"), default=list, blank=True)
    start_time = models.TimeField(_("hora inicio"), default=time(8, 0))
    end_time = models.TimeField(_("hora fin"), default=time(9, 0))
    start_date = models.DateField(_("fecha inicio"), default=timezone.localdate)
    end_date = models.DateField(_("fecha fin"), blank=True, null=True)
    notes = models.TextField(_("notas"), blank=True)

    class Meta:
        verbose_name = _("regla de disponibilidad")
        verbose_name_plural = _("reglas de disponibilidad")
        ordering = ("room__name", "weekday", "start_time")

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        try:
            self.weekdays = normalize_weekdays(self.weekdays)
        except ValidationError as exc:
            errors["weekdays"] = _validation_message(exc, "weekdays")

        if not self.name.strip():
            errors["name"] = _("El nombre es obligatorio.")
        if not self.weekdays:
            self.weekdays = [int(self.weekday)]
        else:
            self.weekday = self.weekdays[0]
        if self.start_time >= self.end_time:
            errors["end_time"] = _("La hora fin debe ser mayor que la hora inicio.")
        if self.end_date and self.end_date < self.start_date:
            errors["end_date"] = _(
                "La fecha fin no puede ser menor que la fecha inicio."
            )

        if self.room_id and self.is_active and not self.is_deleted:
            conflicts = AvailabilityRule.objects.filter(
                room_id=self.room_id,
                is_active=True,
                is_deleted=False,
            ).exclude(pk=self.pk)

            for rule in conflicts:
                if not set(self.weekdays).intersection(rule_weekdays(rule)):
                    continue
                if (
                    rule.start_time == self.start_time
                    and rule.end_time == self.end_time
                ):
                    errors["start_time"] = _(
                        "Ya existe una regla activa para este consultorio, día "
                        "y horario."
                    )
                    break
                if rule.start_time < self.end_time and rule.end_time > self.start_time:
                    errors["start_time"] = _(
                        "La regla se traslapa con otra regla activa del mismo día."
                    )
                    break

        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


def normalize_weekdays(value: Any) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError({"weekdays": _("Los días deben enviarse como lista.")})

    normalized = sorted({int(item) for item in value})
    invalid_days = [day for day in normalized if day < 0 or day > 6]
    if invalid_days:
        raise ValidationError({"weekdays": _("Los días deben estar entre 0 y 6.")})
    return normalized


def rule_weekdays(rule: AvailabilityRule) -> list[int]:
    weekdays = normalize_weekdays(rule.weekdays)
    if weekdays:
        return weekdays
    return [int(rule.weekday)]


def _validation_message(exc: ValidationError, field: str) -> str:
    if hasattr(exc, "message_dict"):
        messages = exc.message_dict.get(field, exc.messages)
        return str(messages[0])
    return str(exc.messages[0])


class AvailabilityExceptionType(models.TextChoices):
    UNAVAILABLE = "unavailable", _("No disponible")
    MAINTENANCE = "maintenance", _("Mantenimiento")
    VACATION = "vacation", _("Vacaciones")
    HOLIDAY = "holiday", _("Festivo")
    OTHER = "other", _("Otro")


class AvailabilityException(BaseModel):
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        on_delete=models.PROTECT,
        related_name="availability_exceptions",
    )
    date = models.DateField(_("fecha"))
    start_time = models.TimeField(_("hora inicio"), blank=True, null=True)
    end_time = models.TimeField(_("hora fin"), blank=True, null=True)
    exception_type = models.CharField(
        _("tipo"),
        max_length=32,
        choices=AvailabilityExceptionType.choices,
        default=AvailabilityExceptionType.UNAVAILABLE,
    )
    reason = models.CharField(_("motivo"), max_length=240)

    class Meta:
        verbose_name = _("excepción de disponibilidad")
        verbose_name_plural = _("excepciones de disponibilidad")
        ordering = ("room__name", "date", "start_time")

    @property
    def is_full_day(self) -> bool:
        return self.start_time is None and self.end_time is None

    def __str__(self) -> str:
        return f"{self.room} - {self.date}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}

        has_start = self.start_time is not None
        has_end = self.end_time is not None

        if has_start != has_end:
            msg = _("Define ambas horas o deja ambas vacías para día completo.")
            errors["start_time"] = msg
            errors["end_time"] = msg
        elif has_start and has_end:
            start_time = self.start_time
            end_time = self.end_time
            if (
                start_time is not None
                and end_time is not None
                and start_time >= end_time
            ):
                errors["end_time"] = _("La hora fin debe ser mayor que la hora inicio.")

        if not self.reason.strip():
            errors["reason"] = _("El motivo es obligatorio.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


@dataclass(frozen=True)
class ReservationBlock:
    room: Any
    date: date
    start_time: time
    end_time: time
    status: str
    origin: str
    label: str = ""
    reservation: Any | None = None


class ReservationStatus(models.TextChoices):
    REQUESTED = "solicitada", _("Solicitada")
    PENDING_PAYMENT = "pendiente_pago", _("Pendiente de pago")
    PAID = "pagada", _("Pagada")
    CONFIRMED = "confirmada", _("Confirmada")
    CANCELLED = "cancelada", _("Cancelada")
    FINISHED = "finalizada", _("Finalizada")


ACTIVE_RESERVATION_STATUSES = {
    ReservationStatus.REQUESTED,
    ReservationStatus.PENDING_PAYMENT,
    ReservationStatus.PAID,
    ReservationStatus.CONFIRMED,
}


class ReservationBatchType(models.TextChoices):
    SINGLE = "single", _("Única")
    RECURRING = "recurring", _("Repetitiva")


class ReservationBatchStatus(models.TextChoices):
    REQUESTED = "requested", _("Por confirmar")
    CONFIRMED = "confirmed", _("Confirmada")
    PARTIALLY_CANCELLED = "partially_cancelled", _("Cancelada parcialmente")
    CANCELLED = "cancelled", _("Cancelada")
    EXPIRED = "expired", _("Vencida")


class ReservationDeadlinePolicy(models.TextChoices):
    PENDING_CONFIGURATION = "pending_configuration", _("Pendiente de configurar")
    HOURS_BEFORE = "hours_before", _("Horas antes")
    PREVIOUS_DAY = "previous_day", _("Día calendario anterior")
    EXCEPTION_HOURS = "exception_hours", _("Excepción: horas antes")
    ADMIN_OVERRIDE = "admin_override", _("Excepción administrativa")


class PaymentDeadlineExceptionType(models.TextChoices):
    HOURS_RULE = "hours_rule", _("Aplicar regla de horas")
    LATE_BOOKING = "late_booking", _("Reservación posterior al vencimiento")


class ReservationPaymentPolicy(VersionedConfiguration):
    clinic = models.ForeignKey(
        "catalog.Clinic",
        on_delete=models.PROTECT,
        related_name="reservation_payment_policies",
        verbose_name=_("clínica"),
    )
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        on_delete=models.PROTECT,
        related_name="reservation_payment_policies",
        verbose_name=_("consultorio específico"),
        blank=True,
        null=True,
    )
    hours_before_start = models.PositiveSmallIntegerField(
        _("horas antes del inicio"),
        default=4,
    )
    advance_rule_enabled = models.BooleanField(
        _("usar regla del día anterior para reservaciones anticipadas"),
        default=True,
    )
    automatic_cancellation = models.BooleanField(
        _("cancelar automáticamente al vencer"),
        default=True,
    )

    immutable_fields = VersionedConfiguration.immutable_fields + (
        "clinic_id",
        "room_id",
        "hours_before_start",
        "advance_rule_enabled",
        "automatic_cancellation",
    )

    class Meta:
        verbose_name = _("política de pago de reservación")
        verbose_name_plural = _("políticas de pago de reservaciones")
        ordering = ("clinic__name", "room__name", "-start_date", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("clinic", "room", "version"),
                name="scheduling_payment_policy_unique_version",
            )
        ]

    def __str__(self) -> str:
        scope = self.room or self.clinic
        return f"{scope} - {self.hours_before_start} horas v{self.version}"

    def version_scope(self) -> dict[str, Any]:
        return {"clinic_id": self.clinic_id, "room_id": self.room_id}

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        room = self.room
        if room is not None and self.clinic_id and room.clinic_id != self.clinic_id:
            errors["room"] = _("El consultorio debe pertenecer a la clínica elegida.")
        if errors:
            raise ValidationError(errors)
        if self.clinic_id:
            self.clean_active_overlap(clinic_id=self.clinic_id, room_id=self.room_id)


def generate_reservation_batch_reference() -> str:
    return uuid.uuid4().hex[:12].upper()


class ReservationBatch(BaseModel):
    reference = models.CharField(
        _("referencia"),
        max_length=12,
        unique=True,
        default=generate_reservation_batch_reference,
        editable=False,
    )
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        on_delete=models.PROTECT,
        related_name="reservation_batches",
        verbose_name=_("consultorio"),
    )
    tenant_doctor = models.ForeignKey(
        "catalog.TenantDoctorProfile",
        on_delete=models.PROTECT,
        related_name="reservation_batches",
        verbose_name=_("médico arrendatario"),
    )
    batch_type = models.CharField(
        _("tipo de reservación"),
        max_length=12,
        choices=ReservationBatchType.choices,
        default=ReservationBatchType.SINGLE,
    )
    status = models.CharField(
        _("estado"),
        max_length=24,
        choices=ReservationBatchStatus.choices,
        default=ReservationBatchStatus.REQUESTED,
    )
    recurrence_rule = models.JSONField(
        _("regla de recurrencia"),
        default=dict,
        blank=True,
    )
    occurrence_count = models.PositiveSmallIntegerField(
        _("número de ocurrencias"),
        default=1,
    )
    currency = models.CharField(_("moneda"), max_length=3, default=DEFAULT_CURRENCY)
    tariff_total = models.DecimalField(
        _("tarifa total"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    tariff_final = models.DecimalField(
        _("tarifa final"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    payment_deadline_at = models.DateTimeField(
        _("fecha límite de pago"),
        blank=True,
        null=True,
    )
    deadline_policy = models.CharField(
        _("política de vencimiento"),
        max_length=32,
        choices=ReservationDeadlinePolicy.choices,
        default=ReservationDeadlinePolicy.PENDING_CONFIGURATION,
    )
    deadline_policy_snapshot = models.JSONField(
        _("snapshot de política de vencimiento"),
        default=dict,
        blank=True,
    )
    cancellation_policy = models.ForeignKey(
        "billing.CancellationPolicy",
        on_delete=models.PROTECT,
        related_name="reservation_batches",
        verbose_name=_("política de cancelación aceptada"),
        blank=True,
        null=True,
    )
    cancellation_policy_snapshot = models.JSONField(
        _("snapshot de política de cancelación"),
        default=dict,
        blank=True,
    )
    cancellation_terms_accepted_at = models.DateTimeField(
        _("términos de cancelación aceptados en"),
        blank=True,
        null=True,
    )
    payment_proof_submitted_at = models.DateTimeField(
        _("comprobante enviado en"),
        blank=True,
        null=True,
    )
    expired_at = models.DateTimeField(_("vencida en"), blank=True, null=True)
    requested_at = models.DateTimeField(_("solicitada en"), default=timezone.now)
    notes = models.TextField(_("notas"), blank=True)

    class Meta:
        verbose_name = _("grupo de reservaciones")
        verbose_name_plural = _("grupos de reservaciones")
        ordering = ("-requested_at", "reference")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(occurrence_count__gte=1),
                name="scheduling_batch_occurrence_count_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(tariff_total__gte=0),
                name="scheduling_batch_tariff_total_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(tariff_final__gte=0),
                name="scheduling_batch_tariff_final_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reference} - {self.room}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if self.occurrence_count < 1:
            errors["occurrence_count"] = _("Debe existir al menos una ocurrencia.")
        if self.tariff_total < Decimal("0.00"):
            errors["tariff_total"] = _("La tarifa total no puede ser negativa.")
        if self.tariff_final < Decimal("0.00"):
            errors["tariff_final"] = _("La tarifa final no puede ser negativa.")
        if self.room_id and self.cancellation_policy_id:
            policy = self.cancellation_policy
            if policy is not None and policy.clinic_id != self.room.clinic_id:
                errors["cancellation_policy"] = _(
                    "La política debe pertenecer a la clínica del consultorio."
                )
            elif policy is not None and policy.room_id not in {None, self.room_id}:
                errors["cancellation_policy"] = _(
                    "La política no corresponde al consultorio seleccionado."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class PaymentDeadlineException(BaseModel):
    batch = models.OneToOneField(
        ReservationBatch,
        on_delete=models.PROTECT,
        related_name="deadline_exception",
        verbose_name=_("grupo de reservaciones"),
    )
    exception_type = models.CharField(
        _("tipo de excepción"),
        max_length=24,
        choices=PaymentDeadlineExceptionType.choices,
        default=PaymentDeadlineExceptionType.HOURS_RULE,
    )
    reason = models.TextField(_("motivo"))
    previous_deadline_at = models.DateTimeField(_("vencimiento anterior"))
    replacement_deadline_at = models.DateTimeField(_("nuevo vencimiento"))
    authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="authorized_payment_deadline_exceptions",
        verbose_name=_("autorizado por"),
    )
    applied_at = models.DateTimeField(_("aplicada en"), default=timezone.now)

    immutable_fields = (
        "batch_id",
        "exception_type",
        "reason",
        "previous_deadline_at",
        "replacement_deadline_at",
        "authorized_by_id",
        "applied_at",
    )

    class Meta:
        verbose_name = _("excepción de vencimiento")
        verbose_name_plural = _("excepciones de vencimiento")
        ordering = ("-applied_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    replacement_deadline_at__gt=models.F("previous_deadline_at")
                ),
                name="scheduling_deadline_exception_extends_deadline",
            )
        ]

    def __str__(self) -> str:
        return f"{self.batch.reference} - {self.get_exception_type_display()}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if not self.reason.strip():
            errors["reason"] = _("El motivo es obligatorio.")
        if self.replacement_deadline_at <= self.previous_deadline_at:
            errors["replacement_deadline_at"] = _(
                "El nuevo vencimiento debe ser posterior al vencimiento anterior."
            )
        if not self._state.adding and self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .values(*self.immutable_fields)
                .first()
            )
            if previous is not None:
                for field_name in self.immutable_fields:
                    if previous[field_name] != getattr(self, field_name):
                        errors[field_name] = _(
                            "La excepción es histórica y no puede modificarse."
                        )
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class Reservation(BaseModel):
    batch = models.ForeignKey(
        ReservationBatch,
        on_delete=models.PROTECT,
        related_name="reservations",
        verbose_name=_("grupo de reservaciones"),
        blank=True,
        null=True,
    )
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    tenant_doctor = models.ForeignKey(
        "catalog.TenantDoctorProfile",
        on_delete=models.PROTECT,
        related_name="reservations",
    )
    date = models.DateField(_("fecha"), default=timezone.localdate)
    start_time = models.TimeField(_("hora inicio"), default=time(8, 0))
    end_time = models.TimeField(_("hora fin"), default=time(9, 0))
    status = models.CharField(
        _("estado"),
        max_length=24,
        choices=ReservationStatus.choices,
        default=ReservationStatus.REQUESTED,
    )
    notes = models.TextField(_("notas"), blank=True)
    cancel_reason = models.TextField(_("motivo de cancelación"), blank=True)
    room_discount_percentage = models.DecimalField(
        _("descuento consultorio %"),
        max_digits=4,
        decimal_places=1,
        default=Decimal("0.0"),
    )
    tenant_discount_percentage = models.DecimalField(
        _("descuento médico arrendatario %"),
        max_digits=4,
        decimal_places=1,
        default=Decimal("0.0"),
    )
    tariff_total = models.DecimalField(
        _("tarifa total"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    tariff_final = models.DecimalField(
        _("tarifa final"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    requested_at = models.DateTimeField(_("solicitada en"), default=timezone.now)
    confirmed_at = models.DateTimeField(_("confirmada en"), blank=True, null=True)
    cancelled_at = models.DateTimeField(_("cancelada en"), blank=True, null=True)

    class Meta:
        verbose_name = _("reservación")
        verbose_name_plural = _("reservaciones")
        ordering = ("date", "start_time")

    def __str__(self) -> str:
        return f"{self.room} {self.date:%Y-%m-%d} {self.start_time:%H:%M}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}

        if self.start_time >= self.end_time:
            errors["end_time"] = _("La hora fin debe ser mayor que la hora inicio.")

        if self.batch_id:
            batch = self.batch
            if batch is not None and self.room_id and batch.room_id != self.room_id:
                errors["batch"] = _(
                    "El grupo y la reservación deben pertenecer al mismo consultorio."
                )
            if (
                batch is not None
                and self.tenant_doctor_id
                and batch.tenant_doctor_id != self.tenant_doctor_id
            ):
                errors["batch"] = _(
                    "El grupo y la reservación deben pertenecer al mismo médico."
                )

        if self.room_id and self.status in ACTIVE_RESERVATION_STATUSES:
            overlaps = Reservation.objects.filter(
                room_id=self.room_id,
                date=self.date,
                status__in=ACTIVE_RESERVATION_STATUSES,
                is_deleted=False,
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            ).exclude(pk=self.pk)
            if overlaps.exists():
                errors["start_time"] = _(
                    "Ya existe una reservación activa traslapada para este consultorio."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)
