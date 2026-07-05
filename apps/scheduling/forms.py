"""Forms for scheduling screens."""

from typing import Any

from django import forms
from django.db.models import QuerySet

from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    OwnerProfile,
    TenantDoctorProfile,
    TenantDoctorStatus,
)
from apps.core.form_utils import (
    date_range_initial,
    monday_date_input,
    selected_model_pk,
    style_form_fields,
)
from apps.core.permissions import scope_queryset_for_user
from apps.scheduling.models import (
    AvailabilityException,
    AvailabilityRule,
    Reservation,
    Weekday,
)


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


def _clinic_queryset(*, active_only: bool = False) -> QuerySet[Clinic]:
    queryset = Clinic.objects.filter(is_deleted=False)
    if active_only:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("name")


def _owner_queryset() -> QuerySet[OwnerProfile]:
    return (
        OwnerProfile.objects.filter(is_deleted=False)
        .select_related("user")
        .order_by("display_name", "user__email")
    )


def _room_queryset(data: Any = None, *, active_only: bool = False) -> QuerySet[Any]:
    queryset = ConsultingRoom.objects.filter(is_deleted=False).select_related(
        "clinic",
        "owner",
        "owner__user",
    )
    if active_only:
        queryset = queryset.filter(is_active=True)

    clinic_pk = selected_model_pk(data, "clinic")
    owner_pk = selected_model_pk(data, "owner")
    if clinic_pk:
        queryset = queryset.filter(clinic_id=clinic_pk)
    if owner_pk:
        queryset = queryset.filter(owner_id=owner_pk)
    return queryset.order_by("clinic__name", "owner__display_name", "name")


def _tenant_doctor_queryset() -> QuerySet[TenantDoctorProfile]:
    return (
        TenantDoctorProfile.objects.filter(is_deleted=False)
        .select_related("user")
        .order_by("display_name", "user__email")
    )


class AvailabilityRuleForm(BootstrapModelForm):
    clinic = forms.ModelChoiceField(
        label="Clínica",
        queryset=Clinic.objects.none(),
        required=False,
    )
    weekdays = forms.MultipleChoiceField(
        label="Días de semana",
        choices=Weekday.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = AvailabilityRule
        fields = (
            "clinic",
            "room",
            "name",
            "weekdays",
            "start_time",
            "end_time",
            "start_date",
            "end_date",
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
        clinic_queryset = _clinic_queryset(active_only=True)
        room_queryset = _room_queryset(source_data, active_only=True)
        if self.user is not None:
            clinic_queryset = scope_queryset_for_user(clinic_queryset, self.user)
            room_queryset = scope_queryset_for_user(room_queryset, self.user)
        if not self.instance._state.adding:
            self.initial.setdefault("clinic", self.instance.room.clinic_id)
            self.initial["weekdays"] = [
                str(day) for day in self.instance.weekdays or [self.instance.weekday]
            ]
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

    def clean_weekdays(self) -> list[int]:
        weekdays = self.cleaned_data["weekdays"]
        if weekdays:
            return [int(day) for day in weekdays]
        legacy_weekday = self.data.get("weekday") if self.is_bound else None
        if isinstance(legacy_weekday, str) and legacy_weekday:
            return [int(legacy_weekday)]
        raise forms.ValidationError("Selecciona al menos un día de semana.")


class AvailabilityExceptionForm(BootstrapModelForm):
    class Meta:
        model = AvailabilityException
        fields = (
            "room",
            "date",
            "start_time",
            "end_time",
            "exception_type",
            "reason",
            "is_active",
        )
        widgets = {
            "date": monday_date_input(),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["room"].label = "Consultorio"
        set_model_queryset(
            self.fields["room"],
            _room_queryset(),
        )


class OperationalFilterForm(forms.Form):
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

    def __init__(self, *args: Any, active_only: bool = True, **kwargs: Any) -> None:
        user = kwargs.pop("user", None)
        provided_initial = kwargs.pop("initial", {}) or {}
        kwargs["initial"] = {**date_range_initial(), **provided_initial}
        super().__init__(*args, **kwargs)
        owner_queryset = _owner_queryset()
        tenant_doctor_queryset = _tenant_doctor_queryset()
        room_queryset = _room_queryset(
            self.data if self.is_bound else None, active_only=active_only
        )
        if user is not None:
            owner_queryset = scope_queryset_for_user(owner_queryset, user)
            room_queryset = scope_queryset_for_user(room_queryset, user)
            tenant_doctor_queryset = scope_queryset_for_user(
                tenant_doctor_queryset, user
            )
        set_model_queryset(
            self.fields["clinic"],
            _clinic_queryset(active_only=active_only),
        )
        set_model_queryset(self.fields["owner"], owner_queryset)
        set_model_queryset(self.fields["room"], room_queryset)
        set_model_queryset(self.fields["tenant_doctor"], tenant_doctor_queryset)
        style_form_fields(self.fields)

    def clean_weekdays(self) -> list[int]:
        return [int(day) for day in self.cleaned_data["weekdays"]]

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_to < date_from:
            self.add_error("date_to", "La fecha hasta no puede ser menor.")
        return cleaned_data


class WeeklyCalendarFilterForm(OperationalFilterForm):
    week = forms.DateField(
        label="Semana",
        required=False,
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields.pop("q", None)


class ReservationFilterForm(OperationalFilterForm):
    pass


class ReservationRequestForm(BootstrapModelForm):
    class Meta:
        model = Reservation
        fields = (
            "room",
            "date",
            "start_time",
            "end_time",
            "tenant_doctor",
            "notes",
        )
        widgets = {
            "date": monday_date_input(),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        set_model_queryset(
            self.fields["room"],
            _room_queryset(active_only=True),
        )
        set_model_queryset(
            self.fields["tenant_doctor"],
            TenantDoctorProfile.objects.filter(
                is_active=True,
                is_deleted=False,
                status=TenantDoctorStatus.AUTHORIZED,
            )
            .select_related("user")
            .order_by("display_name"),
        )


class ReservationCancelForm(forms.Form):
    reason = forms.CharField(
        label="Motivo de cancelación",
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )
