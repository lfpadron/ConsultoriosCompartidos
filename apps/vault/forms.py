"""Forms for document vault screens."""

from typing import Any

from django import forms
from django.db.models import QuerySet

from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    OwnerProfile,
    TenantDoctorProfile,
)
from apps.core.form_utils import (
    date_range_initial,
    monday_date_input,
    selected_model_pk,
    style_form_fields,
)
from apps.core.permissions import scope_queryset_for_user
from apps.finance.models import Payment, Settlement
from apps.scheduling.models import Reservation, Weekday
from apps.vault.models import DocumentStatus, DocumentType


def set_model_queryset(field: forms.Field, queryset: QuerySet[Any]) -> None:
    if isinstance(field, forms.ModelChoiceField):
        field.queryset = queryset


class BootstrapFormMixin:
    fields: dict[str, forms.Field]

    def _apply_bootstrap(self) -> None:
        style_form_fields(self.fields)


def _room_queryset(data: Any = None) -> QuerySet[Any]:
    queryset = ConsultingRoom.objects.filter(is_deleted=False).select_related(
        "clinic",
        "owner",
        "owner__user",
    )
    clinic_pk = selected_model_pk(data, "clinic")
    owner_pk = selected_model_pk(data, "owner")
    if clinic_pk:
        queryset = queryset.filter(clinic_id=clinic_pk)
    if owner_pk:
        queryset = queryset.filter(owner_id=owner_pk)
    return queryset.order_by("clinic__name", "owner__display_name", "name")


class DocumentUploadForm(BootstrapFormMixin, forms.Form):
    title = forms.CharField(label="Título", max_length=180)
    document_type = forms.ChoiceField(
        label="Tipo de documento",
        choices=DocumentType.choices,
    )
    file = forms.FileField(label="Archivo")
    notes = forms.CharField(
        label="Notas",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    owner = forms.ModelChoiceField(
        label="Propietario",
        queryset=OwnerProfile.objects.none(),
        required=False,
    )
    tenant_doctor = forms.ModelChoiceField(
        label="Médico arrendatario",
        queryset=TenantDoctorProfile.objects.none(),
        required=False,
    )
    room = forms.ModelChoiceField(
        label="Consultorio",
        queryset=ConsultingRoom.objects.none(),
        required=False,
    )
    reservation = forms.ModelChoiceField(
        label="Reservación",
        queryset=Reservation.objects.none(),
        required=False,
    )
    payment = forms.ModelChoiceField(
        label="Pago",
        queryset=Payment.objects.none(),
        required=False,
    )
    settlement = forms.ModelChoiceField(
        label="Liquidación",
        queryset=Settlement.objects.none(),
        required=False,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        set_model_queryset(
            self.fields["owner"],
            OwnerProfile.objects.filter(is_deleted=False)
            .select_related("user")
            .order_by("display_name", "user__email"),
        )
        set_model_queryset(
            self.fields["tenant_doctor"],
            TenantDoctorProfile.objects.filter(is_deleted=False)
            .select_related("user")
            .order_by("display_name", "user__email"),
        )
        set_model_queryset(
            self.fields["room"],
            ConsultingRoom.objects.filter(is_deleted=False).order_by(
                "clinic__name", "name"
            ),
        )
        set_model_queryset(
            self.fields["reservation"],
            Reservation.objects.filter(is_deleted=False).order_by(
                "-date",
                "-start_time",
            ),
        )
        set_model_queryset(
            self.fields["payment"],
            Payment.objects.filter(is_deleted=False).order_by("-payment_date"),
        )
        set_model_queryset(
            self.fields["settlement"],
            Settlement.objects.filter(is_deleted=False).order_by("-generated_at"),
        )
        self._apply_bootstrap()

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        has_linked_entity = any(
            cleaned_data.get(field_name) is not None
            for field_name in (
                "owner",
                "tenant_doctor",
                "room",
                "reservation",
                "payment",
                "settlement",
            )
        )
        if not has_linked_entity:
            raise forms.ValidationError("Vincula el documento al menos a una entidad.")
        return cleaned_data


class DocumentFilterForm(BootstrapFormMixin, forms.Form):
    q = forms.CharField(label="Buscar", required=False)
    clinic = forms.ModelChoiceField(
        label="Clínica",
        queryset=Clinic.objects.none(),
        required=False,
    )
    owner = forms.ModelChoiceField(
        label="Médico propietario",
        queryset=OwnerProfile.objects.none(),
        required=False,
    )
    room = forms.ModelChoiceField(
        label="Consultorio",
        queryset=ConsultingRoom.objects.none(),
        required=False,
    )
    tenant_doctor = forms.ModelChoiceField(
        label="Médico arrendatario",
        queryset=TenantDoctorProfile.objects.none(),
        required=False,
    )
    document_type = forms.ChoiceField(
        label="Tipo",
        choices=(("", "Todos"), *DocumentType.choices),
        required=False,
    )
    status = forms.ChoiceField(
        label="Estado",
        choices=(("", "Todos"), *DocumentStatus.choices),
        required=False,
    )
    entity = forms.CharField(label="Entidad", required=False)
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
    weekdays = forms.MultipleChoiceField(
        label="Días de semana",
        choices=Weekday.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        user = kwargs.pop("user", None)
        provided_initial = kwargs.pop("initial", {}) or {}
        kwargs["initial"] = {**date_range_initial(), **provided_initial}
        super().__init__(*args, **kwargs)
        owner_queryset = (
            OwnerProfile.objects.filter(is_deleted=False)
            .select_related("user")
            .order_by("display_name", "user__email")
        )
        room_queryset = _room_queryset(self.data if self.is_bound else None)
        if user is not None:
            owner_queryset = scope_queryset_for_user(owner_queryset, user)
            room_queryset = scope_queryset_for_user(room_queryset, user)
        set_model_queryset(
            self.fields["clinic"],
            Clinic.objects.filter(is_deleted=False).order_by("name"),
        )
        set_model_queryset(self.fields["owner"], owner_queryset)
        set_model_queryset(self.fields["room"], room_queryset)
        set_model_queryset(
            self.fields["tenant_doctor"],
            TenantDoctorProfile.objects.filter(is_deleted=False)
            .select_related("user")
            .order_by("display_name", "user__email"),
        )
        self._apply_bootstrap()

    def clean_weekdays(self) -> list[int]:
        return [int(day) for day in self.cleaned_data["weekdays"]]

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_to < date_from:
            self.add_error("date_to", "La fecha hasta no puede ser menor.")
        return cleaned_data


class DocumentRejectForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(
        label="Motivo de rechazo",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._apply_bootstrap()
