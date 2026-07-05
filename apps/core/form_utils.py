"""Shared form helpers for operational filters."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from django import forms
from django.utils import timezone

DATE_INPUT_ATTRS = {
    "type": "date",
    "lang": "es-MX",
    "data-week-start": "1",
}


def monday_date_input() -> forms.DateInput:
    """Return a localized native date input hinting Monday as week start."""
    return forms.DateInput(attrs=DATE_INPUT_ATTRS.copy())


def default_operational_date_range(today: date | None = None) -> tuple[date, date]:
    """Default from this week's Monday to the Sunday of current month-end week."""
    current_day = today or timezone.localdate()
    start_date = current_day - timedelta(days=current_day.weekday())
    last_day = date(
        current_day.year,
        current_day.month,
        monthrange(current_day.year, current_day.month)[1],
    )
    end_date = last_day + timedelta(days=6 - last_day.weekday())
    return start_date, end_date


def date_range_initial(today: date | None = None) -> dict[str, date]:
    start_date, end_date = default_operational_date_range(today)
    return {"date_from": start_date, "date_to": end_date}


def django_weekday_values(weekdays: Iterable[int | str]) -> list[int]:
    """Map Python weekdays Monday=0 to Django week_day Sunday=1."""
    return [((int(day) + 1) % 7) + 1 for day in weekdays]


def selected_model_pk(data: Any, field_name: str) -> UUID | None:
    if not data:
        return None
    value = data.get(field_name)
    if value in ("", None):
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


def style_form_fields(fields: dict[str, forms.Field]) -> None:
    for field in fields.values():
        widget = field.widget
        if isinstance(widget, forms.CheckboxSelectMultiple):
            widget.attrs["class"] = "weekday-checkbox-input"
        elif isinstance(widget, forms.CheckboxInput):
            widget.attrs["class"] = "form-check-input"
        elif isinstance(widget, forms.Select | forms.SelectMultiple):
            widget.attrs["class"] = "form-select"
        else:
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} form-control".strip()

        if isinstance(widget, forms.DateInput):
            for attr_name, attr_value in DATE_INPUT_ATTRS.items():
                widget.attrs.setdefault(attr_name, attr_value)
