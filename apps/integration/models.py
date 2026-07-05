"""External integration models prepared for future adapters."""

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel
from apps.scheduling.models import ReservationStatus


class ExternalSystemType(models.TextChoices):
    ACCESS_CONTROL = "access_control", _("Control de accesos")
    EMAIL = "email", _("Correo")
    SMS = "sms", _("SMS")
    WHATSAPP = "whatsapp", _("WhatsApp")
    PAYMENT_GATEWAY = "payment_gateway", _("Pasarela de pago")
    EXTERNAL_API = "external_api", _("API externa")


class ExternalSystem(BaseModel):
    name = models.CharField(_("nombre"), max_length=160)
    system_type = models.CharField(
        _("tipo"),
        max_length=40,
        choices=ExternalSystemType.choices,
    )
    base_url = models.URLField(_("URL base"), blank=True)
    is_enabled = models.BooleanField(_("habilitado"), default=False)

    class Meta:
        verbose_name = _("sistema externo")
        verbose_name_plural = _("sistemas externos")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class HttpMethod(models.TextChoices):
    GET = "GET", "GET"
    POST = "POST", "POST"
    PUT = "PUT", "PUT"
    PATCH = "PATCH", "PATCH"
    DELETE = "DELETE", "DELETE"


class IntegrationEndpoint(BaseModel):
    system = models.ForeignKey(
        ExternalSystem,
        on_delete=models.CASCADE,
        related_name="endpoints",
    )
    name = models.CharField(_("nombre"), max_length=160)
    path = models.CharField(_("ruta"), max_length=255)
    method = models.CharField(
        _("método"),
        max_length=8,
        choices=HttpMethod.choices,
        default=HttpMethod.POST,
    )

    class Meta:
        verbose_name = _("endpoint de integración")
        verbose_name_plural = _("endpoints de integración")
        ordering = ("system__name", "name")

    def __str__(self) -> str:
        return f"{self.system} - {self.name}"


class AccessCredentialStatus(models.TextChoices):
    PENDING = "pendiente", _("Pendiente")
    ENABLED = "habilitada", _("Habilitada")
    USED = "usada", _("Usada")
    REVOKED = "revocada", _("Revocada")
    EXPIRED = "expirada", _("Expirada")


ACTIVE_ACCESS_CREDENTIAL_STATUSES = (
    AccessCredentialStatus.PENDING,
    AccessCredentialStatus.ENABLED,
    AccessCredentialStatus.USED,
)


class AccessCredential(BaseModel):
    reservation = models.ForeignKey(
        "scheduling.Reservation",
        on_delete=models.PROTECT,
        related_name="access_credentials",
        verbose_name=_("reservación"),
    )
    tenant_doctor = models.ForeignKey(
        "catalog.TenantDoctorProfile",
        on_delete=models.PROTECT,
        related_name="access_credentials",
        verbose_name=_("médico arrendatario"),
    )
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        on_delete=models.PROTECT,
        related_name="access_credentials",
        verbose_name=_("consultorio"),
    )
    status = models.CharField(
        _("estado"),
        max_length=24,
        choices=AccessCredentialStatus.choices,
        default=AccessCredentialStatus.PENDING,
    )
    simulated_code = models.CharField(
        _("código simulado"),
        max_length=32,
        unique=True,
    )
    valid_from = models.DateTimeField(_("válida desde"))
    valid_until = models.DateTimeField(_("válida hasta"))
    enabled_at = models.DateTimeField(_("habilitada en"), blank=True, null=True)
    used_at = models.DateTimeField(_("usada en"), blank=True, null=True)
    revoked_at = models.DateTimeField(_("revocada en"), blank=True, null=True)
    expired_at = models.DateTimeField(_("expirada en"), blank=True, null=True)
    notes = models.TextField(_("notas"), blank=True)

    class Meta:
        verbose_name = _("credencial de acceso")
        verbose_name_plural = _("credenciales de acceso")
        ordering = ("-valid_from", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("reservation",),
                condition=Q(
                    status__in=ACTIVE_ACCESS_CREDENTIAL_STATUSES,
                    is_deleted=False,
                ),
                name="integration_unique_active_access_credential",
            )
        ]

    def __str__(self) -> str:
        return f"{self.simulated_code} - {self.reservation}"

    @property
    def is_active_credential(self) -> bool:
        return self.status in ACTIVE_ACCESS_CREDENTIAL_STATUSES and not self.is_deleted

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}

        if self.valid_from >= self.valid_until:
            errors["valid_until"] = _(
                "La vigencia final debe ser mayor que la inicial."
            )

        if self.reservation_id:
            if self.reservation.status != ReservationStatus.CONFIRMED:
                errors["reservation"] = _(
                    "Sólo se puede generar acceso para una reservación confirmada."
                )
            if self.tenant_doctor_id and (
                self.tenant_doctor_id != self.reservation.tenant_doctor_id
            ):
                errors["tenant_doctor"] = _(
                    "El médico no corresponde a la reservación."
                )
            if self.room_id and self.room_id != self.reservation.room_id:
                errors["room"] = _("El consultorio no corresponde a la reservación.")

            if self.status in ACTIVE_ACCESS_CREDENTIAL_STATUSES and not self.is_deleted:
                duplicate_active = (
                    AccessCredential.objects.filter(
                        reservation_id=self.reservation_id,
                        status__in=ACTIVE_ACCESS_CREDENTIAL_STATUSES,
                        is_deleted=False,
                    )
                    .exclude(pk=self.pk)
                    .exists()
                )
                if duplicate_active:
                    errors["reservation"] = _(
                        "La reservación ya tiene una credencial activa."
                    )

        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)
