"""Traceability models prepared for visual evidence timelines."""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class TraceEvent(BaseModel):
    occurred_at = models.DateTimeField(_("ocurrió en"), default=timezone.now)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="trace_events",
    )
    event_type = models.CharField(_("tipo de evento"), max_length=120)
    object_label = models.CharField(_("objeto"), max_length=220)
    payload = models.JSONField(_("datos"), default=dict, blank=True)

    class Meta:
        verbose_name = _("evento de trazabilidad")
        verbose_name_plural = _("eventos de trazabilidad")
        ordering = ("-occurred_at",)

    def __str__(self) -> str:
        return f"{self.event_type} - {self.object_label}"


class Evidence(BaseModel):
    event = models.ForeignKey(
        TraceEvent,
        on_delete=models.CASCADE,
        related_name="evidence",
    )
    document = models.ForeignKey(
        "vault.DocumentAsset",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="trace_evidence",
    )
    label = models.CharField(_("etiqueta"), max_length=180)
    metadata = models.JSONField(_("metadatos"), default=dict, blank=True)

    class Meta:
        verbose_name = _("evidencia")
        verbose_name_plural = _("evidencias")
        ordering = ("event__occurred_at", "label")

    def __str__(self) -> str:
        return self.label
