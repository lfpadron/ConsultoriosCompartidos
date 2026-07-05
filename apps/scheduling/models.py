"""Scheduling models prepared for availability and calendar rules."""

from dataclasses import dataclass
from datetime import date, time
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

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


class Reservation(BaseModel):
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
