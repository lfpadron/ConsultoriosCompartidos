"""Clinic-aware date and time display helpers."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import template
from django.conf import settings
from django.utils import timezone

register = template.Library()


@register.filter(name="clinic_time")
def clinic_time(value: time | None, clinic_source: Any) -> str:
    if value is None:
        return "Sin datos"
    return format_time_for_clinic(value, clinic_source)


@register.filter(name="clinic_datetime")
def clinic_datetime(value: datetime | None, clinic_source: Any) -> str:
    if value is None:
        return "Sin datos"

    local_value = value
    clinic = _resolve_clinic(clinic_source)
    try:
        clinic_zone = ZoneInfo(getattr(clinic, "timezone", settings.TIME_ZONE))
    except ZoneInfoNotFoundError:
        clinic_zone = ZoneInfo(settings.TIME_ZONE)

    if timezone.is_naive(local_value):
        local_value = timezone.make_aware(local_value, clinic_zone)
    local_value = timezone.localtime(local_value, clinic_zone)
    return (
        f"{local_value:%d/%m/%Y} "
        f"{format_time_for_clinic(local_value.time(), clinic_source)}"
    )


def format_time_for_clinic(value: time, clinic_source: Any) -> str:
    clinic = _resolve_clinic(clinic_source)
    if getattr(clinic, "hour_format", "24h") == "12h":
        return value.strftime("%I:%M %p")
    return value.strftime("%H:%M")


def _resolve_clinic(clinic_source: Any) -> Any:
    if hasattr(clinic_source, "hour_format"):
        return clinic_source
    return getattr(clinic_source, "clinic", None)
