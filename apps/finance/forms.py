"""Forms for finance screens."""

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
from apps.finance.models import Payment, PaymentStatus, RateRule, SettlementStatus
from apps.scheduling.models import Weekday


def set_model_queryset(field: forms.Field, queryset: QuerySet[Any]) -> None:
    if isinstance(field, forms.ModelChoiceField | forms.ModelMultipleChoiceField):
        field.queryset = queryset


class BootstrapModelForm(forms.ModelForm):
    checkbox_fields = {"is_active"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.user = kwargs.pop("user", None)
        self.filter_data = kwargs.pop("filter_data", None)
        super().__init__(*args, **kwargs)
        style_form_fields(self.fields)


def _clinic_queryset() -> QuerySet[Clinic]:
    return Clinic.objects.filter(is_deleted=False).order_by("name")


def _owner_queryset() -> QuerySet[OwnerProfile]:
    return (
        OwnerProfile.objects.filter(is_deleted=False)
        .select_related("user")
        .order_by("display_name", "user__email")
    )


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


class OperationalFinanceFilterForm(forms.Form):
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
        self.user = kwargs.pop("user", None)
        provided_initial = kwargs.pop("initial", {}) or {}
        kwargs["initial"] = {**date_range_initial(), **provided_initial}
        super().__init__(*args, **kwargs)
        self._set_querysets()
        style_form_fields(self.fields)

    def _set_querysets(self) -> None:
        if "clinic" in self.fields:
            set_model_queryset(self.fields["clinic"], _clinic_queryset())
        if "owner" in self.fields:
            owner_queryset = _owner_queryset()
            if self.user is not None:
                owner_queryset = scope_queryset_for_user(owner_queryset, self.user)
            set_model_queryset(self.fields["owner"], owner_queryset)
        if "room" in self.fields:
            room_queryset = _room_queryset(self.data if self.is_bound else None)
            if self.user is not None:
                room_queryset = scope_queryset_for_user(room_queryset, self.user)
            set_model_queryset(
                self.fields["room"],
                room_queryset,
            )
        if "tenant_doctor" in self.fields:
            set_model_queryset(
                self.fields["tenant_doctor"],
                TenantDoctorProfile.objects.filter(is_deleted=False)
                .select_related("user")
                .order_by("display_name", "user__email"),
            )

    def clean_weekdays(self) -> list[int]:
        return [int(day) for day in self.cleaned_data["weekdays"]]

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_to < date_from:
            self.add_error("date_to", "La fecha hasta no puede ser menor.")
        return cleaned_data


class RateRuleForm(BootstrapModelForm):
    clinic = forms.ModelChoiceField(
        label="Clínica",
        queryset=Clinic.objects.none(),
        required=False,
    )
    weekdays = forms.MultipleChoiceField(
        label="Días de semana",
        choices=Weekday.choices,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = RateRule
        fields = (
            "clinic",
            "room",
            "name",
            "weekdays",
            "start_time",
            "end_time",
            "start_date",
            "end_date",
            "price_type",
            "amount",
            "currency",
            "priority",
            "notes",
            "is_active",
        )
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "start_date": monday_date_input(),
            "end_date": monday_date_input(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        source_data = self.data if self.is_bound else self.filter_data
        clinic_queryset = _clinic_queryset()
        room_queryset = _room_queryset(source_data)
        if self.user is not None:
            clinic_queryset = scope_queryset_for_user(clinic_queryset, self.user)
            room_queryset = scope_queryset_for_user(room_queryset, self.user)
        if not self.instance._state.adding:
            self.initial.setdefault("clinic", self.instance.room.clinic_id)
        elif source_data:
            clinic_pk = selected_model_pk(source_data, "clinic")
            if clinic_pk:
                self.initial.setdefault("clinic", clinic_pk)
        self.fields["room"].label = "Consultorio"
        set_model_queryset(
            self.fields["clinic"],
            clinic_queryset,
        )
        set_model_queryset(
            self.fields["room"],
            room_queryset,
        )
        if not self.instance._state.adding and self.instance.weekdays:
            self.initial["weekdays"] = [str(day) for day in self.instance.weekdays]

    def clean_weekdays(self) -> list[int]:
        return [int(day) for day in self.cleaned_data["weekdays"]]


class RateRuleFilterForm(OperationalFinanceFilterForm):
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
    is_active = forms.ChoiceField(
        label="Estado",
        choices=(
            ("", "Todos"),
            ("true", "Activos"),
            ("false", "Inactivos"),
        ),
        required=False,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class PaymentRegistrationForm(BootstrapModelForm):
    class Meta:
        model = Payment
        fields = (
            "amount",
            "currency",
            "method",
            "reference",
            "payment_date",
            "receipt",
            "notes",
        )
        widgets = {
            "payment_date": monday_date_input(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class PaymentRejectForm(forms.Form):
    reason = forms.CharField(
        label="Motivo de rechazo",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs["class"] = "form-control"


class PaymentFilterForm(OperationalFinanceFilterForm):
    q = forms.CharField(label="Buscar", required=False)
    status = forms.ChoiceField(
        label="Estado",
        choices=(("", "Todos"), *PaymentStatus.choices),
        required=False,
    )
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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)


class SettlementGenerateForm(forms.Form):
    notes = forms.CharField(
        label="Notas",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["notes"].widget.attrs["class"] = "form-control"


class SettlementPaidForm(forms.Form):
    reference = forms.CharField(label="Referencia de pago")
    payment_date = forms.DateField(
        label="Fecha de pago",
        required=False,
        widget=monday_date_input(),
    )
    notes = forms.CharField(
        label="Notas",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


class SettlementFilterForm(OperationalFinanceFilterForm):
    q = forms.CharField(label="Buscar", required=False)
    status = forms.ChoiceField(
        label="Estado",
        choices=(("", "Todos"), *SettlementStatus.choices),
        required=False,
    )
    owner = forms.ModelChoiceField(
        label="Propietario",
        queryset=OwnerProfile.objects.none(),
        required=False,
    )
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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
