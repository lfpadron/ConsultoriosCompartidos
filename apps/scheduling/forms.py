"""Forms for scheduling screens."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, cast

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
from apps.finance.models import PriceType
from apps.finance.services.pricing_engine import (
    BlockPrice,
    PricingConfigurationError,
    calculate_block_price,
)
from apps.scheduling.models import (
    AvailabilityException,
    AvailabilityRule,
    Reservation,
    Weekday,
)
from apps.scheduling.services import BLOCK_STATUS_FREE, generate_availability_blocks

TIME_CHOICE_STEP_MINUTES = 30
MIN_HOURLY_RESERVATION_MINUTES = 60


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
    tenant_doctor_pk = selected_model_pk(data, "tenant_doctor")
    if clinic_pk:
        queryset = queryset.filter(clinic_id=clinic_pk)
    if owner_pk:
        queryset = queryset.filter(owner_id=owner_pk)
    if tenant_doctor_pk:
        assigned_rooms = ConsultingRoom.objects.filter(
            assigned_tenant_doctors__pk=tenant_doctor_pk,
            is_deleted=False,
        )
        if assigned_rooms.exists():
            queryset = queryset.filter(pk__in=assigned_rooms.values("pk"))
    return queryset.distinct().order_by("clinic__name", "owner__display_name", "name")


def _campus_choices(data: Any = None) -> list[tuple[str, str]]:
    queryset = ConsultingRoom.objects.filter(is_deleted=False).exclude(campus="")
    clinic_pk = selected_model_pk(data, "clinic")
    if clinic_pk:
        queryset = queryset.filter(clinic_id=clinic_pk)
    campuses = queryset.order_by("campus").values_list("campus", flat=True).distinct()
    return [("", "Todos"), *((campus, campus) for campus in campuses)]


def _tenant_doctor_queryset() -> QuerySet[TenantDoctorProfile]:
    return (
        TenantDoctorProfile.objects.filter(is_deleted=False)
        .select_related("user")
        .order_by("display_name", "user__email")
    )


def _tenant_doctor_profile_for_user(user: Any) -> TenantDoctorProfile | None:
    return cast(
        TenantDoctorProfile | None,
        getattr(user, "tenant_doctor_profile", None),
    )


def _parse_date_value(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value in ("", None):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_time_value(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    if value in ("", None):
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt).time()
        except ValueError:
            continue
    return None


def _time_value(value: time) -> str:
    return f"{value:%H:%M}"


def _slot_value(start_time: time, end_time: time) -> str:
    return f"{_time_value(start_time)}|{_time_value(end_time)}"


def _parse_slot_value(value: str) -> tuple[time, time] | None:
    parts = value.split("|")
    if len(parts) != 2:
        return None
    start_time = _parse_time_value(parts[0])
    end_time = _parse_time_value(parts[1])
    if start_time is None or end_time is None:
        return None
    return start_time, end_time


def _time_range_points(start_time: time, end_time: time) -> list[time]:
    current = datetime.combine(date.min, start_time)
    end = datetime.combine(date.min, end_time)
    points: list[time] = []
    while current <= end:
        points.append(current.time())
        current += timedelta(minutes=TIME_CHOICE_STEP_MINUTES)
    if not points or points[-1] != end_time:
        points.append(end_time)
    return points


def _add_minutes(value: time, minutes: int) -> time:
    return (datetime.combine(date.min, value) + timedelta(minutes=minutes)).time()


def _unique_time_choices(values: list[time], empty_label: str) -> list[tuple[str, str]]:
    unique_values = sorted({_time_value(value) for value in values})
    return [("", empty_label), *((value, value) for value in unique_values)]


def _price_type_label(price_type: str | None) -> str:
    return dict(PriceType.choices).get(price_type, "Sin tarifa")


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


class AvailabilityTariffFilterForm(forms.Form):
    q = forms.CharField(label="Buscar", required=False)
    clinic = forms.ModelChoiceField(
        label="Clínica",
        queryset=Clinic.objects.none(),
        required=False,
    )
    campus = forms.ChoiceField(
        label="Campus",
        choices=(("", "Todos"),),
        required=False,
    )
    owner = forms.ModelChoiceField(
        label="Médico propietario",
        queryset=OwnerProfile.objects.none(),
        required=False,
    )
    is_active = forms.ChoiceField(
        label="Estado",
        choices=(("", "Todos"), ("true", "Activos"), ("false", "Inactivos")),
        required=False,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        clinic_queryset = _clinic_queryset()
        owner_queryset = _owner_queryset()
        if user is not None:
            clinic_queryset = scope_queryset_for_user(clinic_queryset, user)
            owner_queryset = scope_queryset_for_user(owner_queryset, user)

        set_model_queryset(self.fields["clinic"], clinic_queryset)
        set_model_queryset(self.fields["owner"], owner_queryset)
        campus_field = cast(forms.ChoiceField, self.fields["campus"])
        campus_field.choices = _campus_choices(self.data if self.is_bound else None)
        style_form_fields(self.fields)


class AvailabilityTariffBlockForm(forms.Form):
    availability_rule_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    rate_rule_id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    weekday = forms.ChoiceField(label="Día de semana", choices=Weekday.choices)
    start_time = forms.TimeField(
        label="Hora de inicio",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    end_time = forms.TimeField(
        label="Hora de fin",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    price_type = forms.ChoiceField(
        label="Tipo de tarifa",
        choices=PriceType.choices,
    )
    amount = forms.DecimalField(
        label="Tarifa",
        min_value=Decimal("0.00"),
        max_digits=12,
        decimal_places=2,
    )
    start_date = forms.DateField(
        label="Fecha de inicio de vigencia",
        widget=monday_date_input(),
    )
    end_date = forms.DateField(
        label="Fecha de fin de vigencia",
        required=False,
        widget=monday_date_input(),
    )
    is_active = forms.BooleanField(label="Activo", required=False, initial=True)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        style_form_fields(self.fields)

    def clean_weekday(self) -> int:
        return int(self.cleaned_data["weekday"])

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_time and end_time and start_time >= end_time:
            self.add_error("end_time", "La hora de fin debe ser mayor.")
        if start_date and end_date and end_date < start_date:
            self.add_error(
                "end_date",
                "La fecha de fin no puede ser menor que la fecha de inicio.",
            )
        return cleaned_data


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

    def __init__(
        self,
        *args: Any,
        active_only: bool = True,
        default_tenant_doctor: bool = False,
        **kwargs: Any,
    ) -> None:
        user = kwargs.pop("user", None)
        provided_initial = kwargs.pop("initial", {}) or {}
        self.default_tenant_doctor = None
        if default_tenant_doctor and user is not None:
            self.default_tenant_doctor = _tenant_doctor_profile_for_user(user)
            if self.default_tenant_doctor and "tenant_doctor" not in provided_initial:
                provided_initial["tenant_doctor"] = self.default_tenant_doctor.pk
        kwargs["initial"] = {**date_range_initial(), **provided_initial}
        super().__init__(*args, **kwargs)
        source_data = self.data if self.is_bound else kwargs["initial"]
        clinic_queryset = _clinic_queryset(active_only=active_only)
        owner_queryset = _owner_queryset()
        tenant_doctor_queryset = _tenant_doctor_queryset()
        room_queryset = _room_queryset(source_data, active_only=active_only)
        if user is not None:
            clinic_queryset = scope_queryset_for_user(clinic_queryset, user)
            owner_queryset = scope_queryset_for_user(owner_queryset, user)
            room_queryset = scope_queryset_for_user(room_queryset, user)
            tenant_doctor_queryset = scope_queryset_for_user(
                tenant_doctor_queryset, user
            )
        set_model_queryset(
            self.fields["clinic"],
            clinic_queryset,
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
        self.fields["clinic"].widget.attrs["onchange"] = (
            "this.form.querySelector('[name=room]').value='';"
            "this.form.requestSubmit();"
        )


class ReservationFilterForm(OperationalFilterForm):
    pass


class ReservationRequestForm(BootstrapModelForm):
    clinic_info = forms.CharField(
        label="Texto informativo de la clínica",
        required=False,
        disabled=True,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    price_type_display = forms.CharField(
        label="Tipo de tarifa",
        required=False,
        disabled=True,
    )
    rate_amount_display = forms.CharField(
        label="Tarifa",
        required=False,
        disabled=True,
    )
    total_rate_display = forms.CharField(
        label="Tarifa total",
        required=False,
        disabled=True,
    )
    block_slot = forms.ChoiceField(
        label="Bloque disponible",
        choices=(("", "Selecciona un bloque"),),
        required=False,
    )
    availability_start_time = forms.TimeField(
        required=False,
        widget=forms.HiddenInput,
    )
    availability_end_time = forms.TimeField(
        required=False,
        widget=forms.HiddenInput,
    )
    source = forms.CharField(required=False, widget=forms.HiddenInput)

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
        self.fields["room"].label = "Consultorio"
        self.fields["tenant_doctor"].label = "Médico arrendatario"
        self.fields["start_time"].label = "Hora de inicio"
        self.fields["end_time"].label = "Hora fin"

        source_data = self.data if self.is_bound else self.initial
        room_queryset = _room_queryset(source_data, active_only=True)
        tenant_doctor_queryset = (
            TenantDoctorProfile.objects.filter(
                is_active=True,
                is_deleted=False,
                status=TenantDoctorStatus.AUTHORIZED,
            )
            .select_related("user")
            .order_by("display_name")
        )
        if self.user is not None:
            room_queryset = scope_queryset_for_user(room_queryset, self.user)
            tenant_doctor_queryset = scope_queryset_for_user(
                tenant_doctor_queryset,
                self.user,
            )
            tenant_profile = _tenant_doctor_profile_for_user(self.user)
            if tenant_profile and not selected_model_pk(source_data, "tenant_doctor"):
                self.initial.setdefault("tenant_doctor", tenant_profile.pk)

        set_model_queryset(
            self.fields["room"],
            room_queryset,
        )
        set_model_queryset(
            self.fields["tenant_doctor"],
            tenant_doctor_queryset,
        )
        self.selected_room = self._selected_room(source_data)
        self.selected_date = _parse_date_value(source_data.get("date"))
        self.available_blocks = self._free_blocks(
            self.selected_room,
            self.selected_date,
        )
        self.selected_start_time, self.selected_end_time = self._selected_interval(
            source_data
        )
        self.schedule_start_time = _parse_time_value(
            source_data.get("availability_start_time")
        )
        self.schedule_end_time = _parse_time_value(
            source_data.get("availability_end_time")
        )
        self.schedule_blocks = self._schedule_blocks()
        self.selected_pricing = self._pricing_for_selected_interval()
        self.schedule_mode = (
            self.selected_pricing.price_type
            if self.selected_pricing and self.selected_pricing.price_type
            else self._first_available_price_type()
        )
        self.schedule_message = self._schedule_message()
        self.pricing_options = self._pricing_options()
        self._configure_schedule_fields()
        self._configure_display_fields()
        style_form_fields(self.fields)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        room = cleaned_data.get("room")
        reservation_date = cleaned_data.get("date")
        if not room or not reservation_date:
            return cleaned_data

        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        block_slot = cleaned_data.get("block_slot")
        if isinstance(block_slot, str) and block_slot:
            slot_interval = _parse_slot_value(block_slot)
            if slot_interval is None:
                self.add_error("block_slot", "Selecciona un bloque disponible.")
                return cleaned_data
            start_time, end_time = slot_interval
            cleaned_data["start_time"] = start_time
            cleaned_data["end_time"] = end_time

        if start_time is None or end_time is None:
            self.add_error("start_time", "Selecciona un horario disponible.")
            return cleaned_data
        if start_time >= end_time:
            self.add_error("end_time", "La hora fin debe ser mayor.")
            return cleaned_data

        free_blocks = self._free_blocks(room, reservation_date)
        schedule_blocks = self.schedule_blocks or free_blocks
        if not self._interval_within_free_block(schedule_blocks, start_time, end_time):
            self.add_error(
                "start_time",
                "El horario seleccionado no está disponible para este consultorio.",
            )
            return cleaned_data

        try:
            pricing = calculate_block_price(
                consulting_room=room,
                date=reservation_date,
                start_time=start_time,
                end_time=end_time,
            )
        except PricingConfigurationError as exc:
            self.add_error("start_time", str(exc))
            return cleaned_data

        if pricing.applied_rule is None:
            self.add_error(
                "start_time",
                "No hay tarifa configurada para el horario seleccionado.",
            )
        elif pricing.price_type == PriceType.BLOCK and not self._interval_matches_block(
            schedule_blocks,
            start_time,
            end_time,
        ):
            self.add_error(
                "block_slot",
                "La tarifa por bloque requiere seleccionar un bloque completo.",
            )
        return cleaned_data

    def _selected_room(self, source_data: Any) -> ConsultingRoom | None:
        room_pk = selected_model_pk(source_data, "room")
        if room_pk is None:
            return None
        return (
            ConsultingRoom.objects.filter(pk=room_pk, is_active=True, is_deleted=False)
            .select_related("clinic", "owner")
            .first()
        )

    @staticmethod
    def _free_blocks(
        room: ConsultingRoom | None,
        reservation_date: date | None,
    ) -> list[Any]:
        if room is None or reservation_date is None:
            return []
        return [
            block
            for block in generate_availability_blocks(
                room,
                reservation_date,
                reservation_date,
            )
            if block.status == BLOCK_STATUS_FREE
        ]

    def _selected_interval(self, source_data: Any) -> tuple[time | None, time | None]:
        block_slot = source_data.get("block_slot")
        if isinstance(block_slot, str) and block_slot:
            interval = _parse_slot_value(block_slot)
            if interval is not None:
                return interval

        start_time = _parse_time_value(source_data.get("start_time"))
        end_time = _parse_time_value(source_data.get("end_time"))
        if start_time and end_time:
            return start_time, end_time
        if self.available_blocks:
            first_block = self.available_blocks[0]
            return first_block.start_time, first_block.end_time
        return None, None

    def _schedule_blocks(self) -> list[Any]:
        if self.schedule_start_time and self.schedule_end_time:
            matching_blocks = [
                block
                for block in self.available_blocks
                if block.start_time <= self.schedule_start_time
                and self.schedule_end_time <= block.end_time
            ]
            if matching_blocks:
                return matching_blocks

        if self.selected_start_time and self.selected_end_time:
            matching_blocks = [
                block
                for block in self.available_blocks
                if block.start_time <= self.selected_start_time
                and self.selected_end_time <= block.end_time
            ]
            if matching_blocks:
                selected_block = matching_blocks[0]
                self.schedule_start_time = selected_block.start_time
                self.schedule_end_time = selected_block.end_time
                return [selected_block]

        return self.available_blocks

    def _pricing_for_selected_interval(self) -> BlockPrice | None:
        if (
            self.selected_room is None
            or self.selected_date is None
            or self.selected_start_time is None
            or self.selected_end_time is None
        ):
            return None
        try:
            return calculate_block_price(
                consulting_room=self.selected_room,
                date=self.selected_date,
                start_time=self.selected_start_time,
                end_time=self.selected_end_time,
            )
        except PricingConfigurationError:
            return None

    def _first_available_price_type(self) -> str | None:
        if self.selected_room is None or self.selected_date is None:
            return None
        for block in self.schedule_blocks:
            try:
                pricing = calculate_block_price(
                    consulting_room=self.selected_room,
                    date=self.selected_date,
                    start_time=block.start_time,
                    end_time=block.end_time,
                )
            except PricingConfigurationError:
                continue
            if pricing.price_type:
                return pricing.price_type
        return None

    def _schedule_message(self) -> str:
        if self.selected_room is None or self.selected_date is None:
            return "Selecciona consultorio y fecha para ver los horarios disponibles."
        if not self.available_blocks:
            return (
                "No hay horarios disponibles para el consultorio y fecha "
                "seleccionados."
            )
        if self.selected_pricing is None or self.selected_pricing.applied_rule is None:
            return "No hay tarifa configurada para el horario seleccionado."
        return ""

    def _pricing_options(self) -> dict[str, Any]:
        ranges: list[dict[str, str]] = []
        block_slots: list[dict[str, str]] = []
        if self.selected_room is None or self.selected_date is None:
            return {"ranges": ranges, "block_slots": block_slots}

        for block in self.available_blocks:
            try:
                pricing = calculate_block_price(
                    consulting_room=self.selected_room,
                    date=self.selected_date,
                    start_time=block.start_time,
                    end_time=block.end_time,
                )
            except PricingConfigurationError:
                continue
            if pricing.applied_rule is None or pricing.base_rate is None:
                continue
            option = {
                "value": _slot_value(block.start_time, block.end_time),
                "start": _time_value(block.start_time),
                "end": _time_value(block.end_time),
                "price_type": pricing.price_type or "",
                "price_type_label": _price_type_label(pricing.price_type),
                "base_rate": str(pricing.base_rate),
                "subtotal": str(pricing.subtotal or Decimal("0.00")),
                "currency": pricing.currency,
            }
            ranges.append(option)
            if pricing.price_type == PriceType.BLOCK:
                block_slots.append(option)
        return {"ranges": ranges, "block_slots": block_slots}

    def _configure_schedule_fields(self) -> None:
        block_choices = [
            (
                option["value"],
                (
                    f"{option['start']} - {option['end']} "
                    f"({option['subtotal']} {option['currency']})"
                ),
            )
            for option in self.pricing_options["block_slots"]
        ]
        self.fields["block_slot"].choices = [
            ("", "Selecciona un bloque"),
            *block_choices,
        ]

        start_points: list[time] = []
        end_points: list[time] = []
        for block in self.schedule_blocks:
            points = _time_range_points(block.start_time, block.end_time)
            latest_start = _add_minutes(
                block.end_time,
                -MIN_HOURLY_RESERVATION_MINUTES,
            )
            earliest_end = _add_minutes(
                block.start_time,
                MIN_HOURLY_RESERVATION_MINUTES,
            )
            start_points.extend(point for point in points if point <= latest_start)
            end_points.extend(point for point in points if point >= earliest_end)

        self.fields["start_time"].widget = forms.Select(
            choices=_unique_time_choices(start_points, "Selecciona hora inicio")
        )
        self.fields["end_time"].widget = forms.Select(
            choices=_unique_time_choices(end_points, "Selecciona hora fin")
        )
        if self.schedule_start_time:
            self.initial["availability_start_time"] = _time_value(
                self.schedule_start_time
            )
        if self.schedule_end_time:
            self.initial["availability_end_time"] = _time_value(self.schedule_end_time)
        if self.selected_start_time:
            self.initial["start_time"] = _time_value(self.selected_start_time)
        if self.selected_end_time:
            self.initial["end_time"] = _time_value(self.selected_end_time)

        if self.schedule_mode == PriceType.BLOCK:
            selected_value = ""
            if self.selected_start_time and self.selected_end_time:
                selected_value = _slot_value(
                    self.selected_start_time,
                    self.selected_end_time,
                )
            self.initial["block_slot"] = selected_value
            self.fields["block_slot"].required = True
            self.fields["start_time"].widget = forms.HiddenInput()
            self.fields["end_time"].widget = forms.HiddenInput()
        else:
            self.fields["block_slot"].widget = forms.HiddenInput()

    def _configure_display_fields(self) -> None:
        clinic_info = ""
        if self.selected_room is not None:
            clinic_info = self.selected_room.clinic.schedule_text
        self.fields["clinic_info"].initial = (
            clinic_info or "Sin información registrada."
        )
        self.fields["source"].initial = self.initial.get("source", "calendar")

        pricing = self.selected_pricing
        if pricing and pricing.applied_rule and pricing.base_rate is not None:
            suffix = "/h" if pricing.price_type == PriceType.HOURLY else " por bloque"
            self.fields["price_type_display"].initial = _price_type_label(
                pricing.price_type
            )
            self.fields["rate_amount_display"].initial = (
                f"{pricing.base_rate} {pricing.currency}{suffix}"
            )
            self.fields["total_rate_display"].initial = (
                f"{pricing.subtotal} {pricing.currency}"
            )
        else:
            self.fields["price_type_display"].initial = "Sin tarifa"
            self.fields["rate_amount_display"].initial = "Sin tarifa configurada"
            self.fields["total_rate_display"].initial = "Sin tarifa configurada"

    @staticmethod
    def _interval_within_free_block(
        blocks: list[Any],
        start_time: time,
        end_time: time,
    ) -> bool:
        return any(
            block.start_time <= start_time and end_time <= block.end_time
            for block in blocks
        )

    @staticmethod
    def _interval_matches_block(
        blocks: list[Any],
        start_time: time,
        end_time: time,
    ) -> bool:
        return any(
            block.start_time == start_time and block.end_time == end_time
            for block in blocks
        )


class ReservationCancelForm(forms.Form):
    reason = forms.CharField(
        label="Motivo de cancelación",
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )
