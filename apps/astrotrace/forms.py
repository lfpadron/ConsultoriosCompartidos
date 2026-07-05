"""Forms for AstroTrace timeline screens."""

from typing import Any

from django import forms
from django.contrib.auth import get_user_model

from apps.astrotrace.services.timeline_service import LEVEL_CHOICES
from apps.core.form_utils import date_range_initial, monday_date_input


class TimelineFilterForm(forms.Form):
    q = forms.CharField(label="Buscar", required=False)
    date_from = forms.DateField(
        label="Fecha desde",
        required=False,
        widget=monday_date_input(),
    )
    date_to = forms.DateField(
        label="Fecha hasta",
        required=False,
        widget=monday_date_input(),
    )
    level = forms.ChoiceField(
        label="Nivel",
        choices=(("", "Todos"), *LEVEL_CHOICES),
        required=False,
    )
    module = forms.CharField(label="Módulo", required=False)
    action = forms.CharField(label="Acción", required=False)
    user = forms.ModelChoiceField(
        label="Usuario",
        queryset=get_user_model().objects.none(),
        required=False,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        provided_initial = kwargs.pop("initial", {}) or {}
        kwargs["initial"] = {**date_range_initial(), **provided_initial}
        super().__init__(*args, **kwargs)
        user_field = self.fields["user"]
        assert isinstance(user_field, forms.ModelChoiceField)
        user_field.queryset = get_user_model().objects.order_by("email")
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs["class"] = "form-select"
            else:
                field.widget.attrs["class"] = "form-control"
