"""Filters for the business administration center."""

from typing import Any

from django import forms
from django.db.models import Q, QuerySet

from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    OwnerProfile,
    TenantDoctorProfile,
)
from apps.core.form_utils import selected_model_pk, style_form_fields
from apps.core.permissions import scope_queryset_for_user
from apps.scheduling.models import ReservationStatus


def _set_queryset(field: forms.Field, queryset: QuerySet[Any]) -> None:
    if isinstance(field, forms.ModelChoiceField):
        field.queryset = queryset


class AdministrationFilterForm(forms.Form):
    clinic = forms.ModelChoiceField(
        label="Clínica",
        queryset=Clinic.objects.none(),
        required=False,
    )
    room = forms.ModelChoiceField(
        label="Consultorio",
        queryset=ConsultingRoom.objects.none(),
        required=False,
    )
    owner = forms.ModelChoiceField(
        label="Médico propietario",
        queryset=OwnerProfile.objects.none(),
        required=False,
    )
    tenant_doctor = forms.ModelChoiceField(
        label="Médico arrendatario",
        queryset=TenantDoctorProfile.objects.none(),
        required=False,
    )
    date_from = forms.DateField(
        label="Fecha desde",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label="Fecha hasta",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    status = forms.ChoiceField(
        label="Estado de reservación",
        choices=(("", "Todos"), *ReservationStatus.choices),
        required=False,
    )

    def __init__(
        self,
        *args: Any,
        user: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        source_data = self.data if self.is_bound else self.initial
        clinics = scope_queryset_for_user(
            Clinic.objects.filter(is_deleted=False),
            user,
        ).order_by("name")
        rooms = ConsultingRoom.objects.filter(is_deleted=False).select_related(
            "clinic",
            "owner",
            "owner__user",
        )
        owners = OwnerProfile.objects.filter(is_deleted=False).select_related("user")
        tenants = TenantDoctorProfile.objects.filter(is_deleted=False).select_related(
            "user"
        )
        clinic_pk = selected_model_pk(source_data, "clinic")
        owner_pk = selected_model_pk(source_data, "owner")
        if clinic_pk:
            rooms = rooms.filter(clinic_id=clinic_pk)
            owners = owners.filter(consulting_rooms__clinic_id=clinic_pk).distinct()
            tenants = tenants.filter(
                Q(assigned_rooms__clinic_id=clinic_pk)
                | Q(reservations__room__clinic_id=clinic_pk)
            ).distinct()
        if owner_pk:
            rooms = rooms.filter(owner_id=owner_pk)

        _set_queryset(self.fields["clinic"], clinics)
        _set_queryset(
            self.fields["room"],
            scope_queryset_for_user(rooms, user).order_by("clinic__name", "name"),
        )
        _set_queryset(
            self.fields["owner"],
            scope_queryset_for_user(owners, user).order_by(
                "display_name", "user__email"
            ),
        )
        _set_queryset(
            self.fields["tenant_doctor"],
            scope_queryset_for_user(tenants, user).order_by(
                "display_name", "user__email"
            ),
        )
        self.fields["clinic"].widget.attrs["onchange"] = (
            "this.form.querySelector('[name=room]').value='';"
            "this.form.requestSubmit();"
        )
        style_form_fields(self.fields)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_to < date_from:
            self.add_error("date_to", "La fecha hasta no puede ser menor.")
        return cleaned_data
