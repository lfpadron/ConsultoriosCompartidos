"""Document vault models prepared for local or MinIO-backed storage."""

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel
from apps.core.validators import validate_sha256


def document_upload_path(instance: DocumentAsset, filename: str) -> str:
    created_at = instance.created_at or timezone.now()
    return f"vault/{created_at:%Y/%m}/{filename}"


class DocumentType(models.TextChoices):
    CONTRACT = "contrato", _("Contrato")
    INE = "ine", _("INE")
    RFC = "rfc", _("RFC")
    PROFESSIONAL_LICENSE = "cedula_profesional", _("Cédula profesional")
    ADDRESS_PROOF = "comprobante_domicilio", _("Comprobante domicilio")
    PAYMENT_RECEIPT = "comprobante_pago", _("Comprobante pago")
    REGULATIONS = "reglamento", _("Reglamento")
    PHOTO = "fotografia", _("Fotografía")
    OTHER = "otro", _("Otro")


class DocumentStatus(models.TextChoices):
    RECEIVED = "recibido", _("Recibido")
    IN_REVIEW = "en_revision", _("En revisión")
    APPROVED = "aprobado", _("Aprobado")
    REJECTED = "rechazado", _("Rechazado")
    REPLACED = "reemplazado", _("Reemplazado")
    CANCELLED = "cancelado", _("Cancelado")


class DocumentAsset(BaseModel):
    title = models.CharField(_("título"), max_length=180)
    document_type = models.CharField(
        _("tipo de documento"),
        max_length=32,
        choices=DocumentType.choices,
        default=DocumentType.OTHER,
    )
    file = models.FileField(_("archivo"), upload_to=document_upload_path)
    original_name = models.CharField(_("nombre original"), max_length=255, blank=True)
    mime_type = models.CharField(_("MIME type"), max_length=120, blank=True)
    size_bytes = models.PositiveBigIntegerField(_("tamaño bytes"), default=0)
    sha256_hash = models.CharField(
        _("SHA-256"),
        max_length=64,
        validators=[validate_sha256],
        db_index=True,
        blank=True,
    )
    version = models.PositiveIntegerField(_("versión"), default=1)
    status = models.CharField(
        _("estado"),
        max_length=24,
        choices=DocumentStatus.choices,
        default=DocumentStatus.RECEIVED,
    )
    notes = models.TextField(_("notas"), blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_documents",
    )
    reviewed_at = models.DateTimeField(_("revisado en"), blank=True, null=True)
    rejection_reason = models.TextField(_("motivo de rechazo"), blank=True)
    owner = models.ForeignKey(
        "catalog.OwnerProfile",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="documents",
    )
    tenant_doctor = models.ForeignKey(
        "catalog.TenantDoctorProfile",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="documents",
    )
    room = models.ForeignKey(
        "catalog.ConsultingRoom",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="documents",
    )
    reservation = models.ForeignKey(
        "scheduling.Reservation",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="documents",
    )
    payment = models.ForeignKey(
        "finance.Payment",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="documents",
    )
    settlement = models.ForeignKey(
        "finance.Settlement",
        blank=True,
        null=True,
        on_delete=models.PROTECT,
        related_name="documents",
    )
    metadata = models.JSONField(_("metadatos"), default=dict, blank=True)

    class Meta:
        verbose_name = _("documento")
        verbose_name_plural = _("documentos")
        ordering = ("-created_at", "title", "-version")

    def __str__(self) -> str:
        return f"{self.title} v{self.version}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}

        if not self.title.strip():
            errors["title"] = _("El título es obligatorio.")
        if self.version < 1:
            errors["version"] = _("La versión debe ser mayor o igual a 1.")
        if self.status == DocumentStatus.REJECTED and not (
            self.rejection_reason.strip()
        ):
            errors["rejection_reason"] = _("El motivo de rechazo es obligatorio.")
        if not any(
            (
                self.owner_id,
                self.tenant_doctor_id,
                self.room_id,
                self.reservation_id,
                self.payment_id,
                self.settlement_id,
            )
        ):
            errors["owner"] = _("Vincula el documento al menos a una entidad.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)
