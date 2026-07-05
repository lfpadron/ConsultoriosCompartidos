"""Filter forms for MVP reports."""

from typing import Any

from django import forms
from django.db.models import QuerySet

from apps.astrotrace.services.timeline_service import LEVEL_CHOICES
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
)
from apps.finance.models import PaymentStatus, SettlementStatus
from apps.vault.models import DocumentStatus, DocumentType


def set_model_queryset(field: forms.Field, queryset: QuerySet[Any]) -> None:
    if isinstance(field, forms.ModelChoiceField):
        field.queryset = queryset


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


class BootstrapReportFilterForm(forms.Form):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        provided_initial = kwargs.pop("initial", {}) or {}
        kwargs["initial"] = {**date_range_initial(), **provided_initial}
        super().__init__(*args, **kwargs)
        self._set_querysets()
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs["class"] = "form-select"
            else:
                field.widget.attrs["class"] = "form-control"

    def _set_querysets(self) -> None:
        if "clinic" in self.fields:
            set_model_queryset(
                self.fields["clinic"],
                Clinic.objects.filter(is_deleted=False).order_by("name"),
            )
        if "room" in self.fields:
            set_model_queryset(
                self.fields["room"],
                _room_queryset(self.data if self.is_bound else None),
            )
        if "owner" in self.fields:
            set_model_queryset(
                self.fields["owner"],
                OwnerProfile.objects.filter(is_deleted=False)
                .select_related("user")
                .order_by("display_name", "user__email"),
            )
        if "tenant_doctor" in self.fields:
            set_model_queryset(
                self.fields["tenant_doctor"],
                TenantDoctorProfile.objects.filter(is_deleted=False)
                .select_related("user")
                .order_by("display_name", "user__email"),
            )


class DateRangeMixin(forms.Form):
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


class RoomOwnerMixin(forms.Form):
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
        label="Propietario",
        queryset=OwnerProfile.objects.none(),
        required=False,
    )


class OccupancyReportFilterForm(
    DateRangeMixin, RoomOwnerMixin, BootstrapReportFilterForm
):
    pass


class IncomeReportFilterForm(DateRangeMixin, RoomOwnerMixin, BootstrapReportFilterForm):
    pass


class PaymentsReportFilterForm(DateRangeMixin, BootstrapReportFilterForm):
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
    tenant_doctor = forms.ModelChoiceField(
        label="Médico arrendatario",
        queryset=TenantDoctorProfile.objects.none(),
        required=False,
    )
    status = forms.ChoiceField(
        label="Estado de pago",
        choices=(("", "Todos"), *PaymentStatus.choices),
        required=False,
    )


class SettlementsReportFilterForm(
    DateRangeMixin,
    RoomOwnerMixin,
    BootstrapReportFilterForm,
):
    status = forms.ChoiceField(
        label="Estado liquidación",
        choices=(("", "Todos"), *SettlementStatus.choices),
        required=False,
    )


class DocumentsReportFilterForm(DateRangeMixin, BootstrapReportFilterForm):
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
        label="Propietario",
        queryset=OwnerProfile.objects.none(),
        required=False,
    )
    tenant_doctor = forms.ModelChoiceField(
        label="Médico arrendatario",
        queryset=TenantDoctorProfile.objects.none(),
        required=False,
    )
    status = forms.ChoiceField(
        label="Estado documento",
        choices=(("", "Todos"), *DocumentStatus.choices),
        required=False,
    )
    document_type = forms.ChoiceField(
        label="Tipo documento",
        choices=(("", "Todos"), *DocumentType.choices),
        required=False,
    )


class TraceabilityReportFilterForm(DateRangeMixin, BootstrapReportFilterForm):
    level = forms.ChoiceField(
        label="Nivel AstroTrace",
        choices=(("", "Todos"), *LEVEL_CHOICES),
        required=False,
    )
