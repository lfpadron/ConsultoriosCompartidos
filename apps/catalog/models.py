"""Business catalog models."""

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


def validate_non_empty_name(value: str) -> None:
    if not value.strip():
        raise ValidationError({"name": _("El nombre es obligatorio.")})


class Clinic(BaseModel):
    name = models.CharField(_("nombre"), max_length=180)
    address = models.TextField(_("dirección"), blank=True)
    phone = models.CharField(_("teléfono"), max_length=40, blank=True)
    email = models.EmailField(_("correo electrónico"), blank=True)
    schedule_text = models.TextField(_("horario"), blank=True)
    timezone = models.CharField(max_length=64, default="America/Mexico_City")
    hour_format = models.CharField(
        _("formato de hora"),
        max_length=8,
        choices=(("24h", _("24 horas")), ("12h", _("AM/PM"))),
        default="24h",
    )

    class Meta:
        verbose_name = _("clínica")
        verbose_name_plural = _("clínicas")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        validate_non_empty_name(self.name)


class Specialty(BaseModel):
    name = models.CharField(_("nombre"), max_length=160, unique=True)
    description = models.TextField(_("descripción"), blank=True)

    class Meta:
        verbose_name = _("especialidad")
        verbose_name_plural = _("especialidades")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        validate_non_empty_name(self.name)


class Equipment(BaseModel):
    name = models.CharField(_("nombre"), max_length=160, unique=True)
    description = models.TextField(_("descripción"), blank=True)

    class Meta:
        verbose_name = _("equipamiento")
        verbose_name_plural = _("equipamiento")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        validate_non_empty_name(self.name)


class OwnerProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owner_profile",
    )
    display_name = models.CharField(_("nombre público"), max_length=180, blank=True)
    professional_license = models.CharField(
        _("cédula profesional"), max_length=80, blank=True
    )
    tax_id = models.CharField(_("RFC"), max_length=20, blank=True)
    phone = models.CharField(_("teléfono"), max_length=40, blank=True)
    notes = models.TextField(_("notas"), blank=True)

    class Meta:
        verbose_name = _("propietario")
        verbose_name_plural = _("propietarios")
        ordering = ("display_name",)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.display_name:
            self.display_name = self.user.full_name or self.user.email
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.display_name or self.user.email


class TenantDoctorStatus(models.TextChoices):
    PENDING = "pending", _("Pendiente")
    AUTHORIZED = "authorized", _("Autorizado")
    SUSPENDED = "suspended", _("Suspendido")
    BLOCKED = "blocked", _("Bloqueado")


class TenantDoctorProfile(BaseModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="tenant_doctor_profile",
    )
    display_name = models.CharField(_("nombre público"), max_length=180, blank=True)
    professional_license = models.CharField(
        _("cédula profesional"), max_length=80, blank=True
    )
    specialties = models.ManyToManyField(Specialty, blank=True, related_name="doctors")
    tax_id = models.CharField(_("RFC"), max_length=20, blank=True)
    phone = models.CharField(_("teléfono"), max_length=40, blank=True)
    status = models.CharField(
        _("estado"),
        max_length=24,
        choices=TenantDoctorStatus.choices,
        default=TenantDoctorStatus.PENDING,
    )
    notes = models.TextField(_("notas"), blank=True)

    class Meta:
        verbose_name = _("médico arrendatario")
        verbose_name_plural = _("médicos arrendatarios")
        ordering = ("display_name",)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.display_name:
            self.display_name = self.user.full_name or self.user.email
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.display_name or self.user.email


class ConsultingRoomStatus(models.TextChoices):
    AVAILABLE = "available", _("Disponible")
    INACTIVE = "inactive", _("Inactivo")
    MAINTENANCE = "maintenance", _("Mantenimiento")


class ConsultingRoom(BaseModel):
    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.PROTECT,
        related_name="consulting_rooms",
    )
    owner = models.ForeignKey(
        OwnerProfile,
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="consulting_rooms",
    )
    name = models.CharField(_("nombre"), max_length=160)
    description = models.TextField(_("descripción"), blank=True)
    floor = models.CharField(_("piso"), max_length=40, blank=True)
    capacity = models.PositiveSmallIntegerField(_("capacidad"), default=1)
    allowed_specialties = models.ManyToManyField(
        Specialty,
        blank=True,
        related_name="allowed_consulting_rooms",
    )
    excluded_specialties = models.ManyToManyField(
        Specialty,
        blank=True,
        related_name="excluded_consulting_rooms",
    )
    equipment = models.ManyToManyField(
        Equipment,
        blank=True,
        related_name="consulting_rooms",
    )
    regulations_text = models.TextField(_("reglamento"), blank=True)
    status = models.CharField(
        _("estado"),
        max_length=24,
        choices=ConsultingRoomStatus.choices,
        default=ConsultingRoomStatus.AVAILABLE,
    )

    class Meta:
        verbose_name = _("consultorio")
        verbose_name_plural = _("consultorios")
        ordering = ("clinic__name", "name")

    def clean(self) -> None:
        super().clean()
        errors = {}
        if not self.name.strip():
            errors["name"] = _("El nombre es obligatorio.")

        if not self.clinic_id:
            errors["clinic"] = _("El consultorio debe tener una clínica.")
        if not self.owner_id:
            errors["owner"] = _("El consultorio debe tener un propietario.")

        if self.pk:
            allowed_ids = set(self.allowed_specialties.values_list("id", flat=True))
            excluded_ids = set(self.excluded_specialties.values_list("id", flat=True))
            if allowed_ids.intersection(excluded_ids):
                errors["excluded_specialties"] = _(
                    "Las especialidades permitidas y excluidas no deben traslaparse."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.clinic} - {self.name}"
