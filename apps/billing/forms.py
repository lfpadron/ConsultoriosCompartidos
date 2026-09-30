"""Forms for commercial configuration screens."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.forms import BaseFormSet

from apps.billing.models import (
    CancellationPolicy,
    OwnerCommissionRule,
    OwnerPayoutSchedule,
    OwnerSubscription,
    RoomFixedFeeRule,
    RoomMonthlyFee,
    TenantSubscription,
)
from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    OwnerProfile,
    TenantDoctorProfile,
)
from apps.core.form_utils import monday_date_input, selected_model_pk, style_form_fields
from apps.core.permissions import scope_queryset_for_user


def _set_queryset(field: forms.Field, queryset: QuerySet[Any]) -> None:
    if isinstance(field, forms.ModelChoiceField):
        field.queryset = queryset


class BillingModelForm(forms.ModelForm):
    """Style billing forms and retain the actor for scoped choices."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.user = kwargs.pop("user", None)
        self.filter_data = kwargs.pop("filter_data", None)
        super().__init__(*args, **kwargs)
        style_form_fields(self.fields)


class VersionedRuleForm(BillingModelForm):
    class Meta:
        widgets = {
            "start_date": monday_date_input(),
            "end_date": monday_date_input(),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def _scope_field(self, field_name: str, queryset: QuerySet[Any]) -> None:
        if self.user is not None:
            queryset = scope_queryset_for_user(queryset, self.user)
        _set_queryset(self.fields[field_name], queryset)

    def _configure_money(self) -> None:
        amount = self.fields.get("amount")
        if isinstance(amount, forms.DecimalField):
            amount.min_value = 0
            amount.widget.attrs.update({"min": "0", "step": "0.01"})

    def _configure_percentage(self) -> None:
        percentage = self.fields.get("percentage")
        if isinstance(percentage, forms.DecimalField):
            percentage.min_value = 0
            percentage.max_value = 100
            percentage.widget.attrs.update({"min": "0", "max": "100", "step": "0.1"})


class OwnerSubscriptionForm(VersionedRuleForm):
    class Meta(VersionedRuleForm.Meta):
        model = OwnerSubscription
        fields = (
            "owner",
            "billing_cycle",
            "amount",
            "currency",
            "start_date",
            "end_date",
            "notes",
        )
        help_texts = {
            "billing_cycle": (
                "Una suscripción anual cubre doce meses desde el inicio "
                "de cada periodo."
            ),
            "end_date": "Déjala vacía para vigencia indefinida.",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._scope_field(
            "owner",
            OwnerProfile.objects.filter(is_deleted=False)
            .select_related("user")
            .order_by("display_name", "user__email"),
        )
        self._configure_money()


class TenantSubscriptionForm(VersionedRuleForm):
    class Meta(VersionedRuleForm.Meta):
        model = TenantSubscription
        fields = (
            "tenant_doctor",
            "billing_cycle",
            "amount",
            "currency",
            "start_date",
            "end_date",
            "notes",
        )
        help_texts = {
            "billing_cycle": (
                "Una suscripción anual cubre doce meses desde el inicio "
                "de cada periodo."
            ),
            "end_date": "Déjala vacía para vigencia indefinida.",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._scope_field(
            "tenant_doctor",
            TenantDoctorProfile.objects.filter(is_deleted=False)
            .select_related("user")
            .order_by("display_name", "user__email"),
        )
        self._configure_money()


class OwnerCommissionRuleForm(VersionedRuleForm):
    class Meta(VersionedRuleForm.Meta):
        model = OwnerCommissionRule
        fields = (
            "owner",
            "percentage",
            "start_date",
            "end_date",
            "notes",
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._scope_field(
            "owner",
            OwnerProfile.objects.filter(is_deleted=False)
            .select_related("user")
            .order_by("display_name", "user__email"),
        )
        self._configure_percentage()


class OwnerPayoutScheduleForm(VersionedRuleForm):
    class Meta(VersionedRuleForm.Meta):
        model = OwnerPayoutSchedule
        fields = (
            "owner",
            "frequency",
            "start_date",
            "end_date",
            "notes",
        )
        help_texts = {
            "frequency": (
                "Semanal corta el domingo; quincenal los días 15 y último; "
                "mensual el último día del mes."
            )
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._scope_field(
            "owner",
            OwnerProfile.objects.filter(is_deleted=False)
            .select_related("user")
            .order_by("display_name", "user__email"),
        )


class RoomRuleForm(VersionedRuleForm):
    clinic = forms.ModelChoiceField(
        label="Clínica",
        queryset=Clinic.objects.none(),
        required=False,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        clinic_queryset = Clinic.objects.filter(is_deleted=False).order_by("name")
        room_queryset = ConsultingRoom.objects.filter(is_deleted=False).select_related(
            "clinic", "owner"
        )
        source_data = self.data if self.is_bound else self.filter_data
        clinic_pk = selected_model_pk(source_data, "clinic")
        if clinic_pk:
            room_queryset = room_queryset.filter(clinic_id=clinic_pk)
            self.initial.setdefault("clinic", clinic_pk)
        if self.user is not None:
            clinic_queryset = scope_queryset_for_user(clinic_queryset, self.user)
            room_queryset = scope_queryset_for_user(room_queryset, self.user)
        _set_queryset(self.fields["clinic"], clinic_queryset)
        _set_queryset(
            self.fields["room"],
            room_queryset.order_by("clinic__name", "name"),
        )
        self._configure_money()


class RoomFixedFeeRuleForm(RoomRuleForm):
    class Meta(VersionedRuleForm.Meta):
        model = RoomFixedFeeRule
        fields = (
            "clinic",
            "room",
            "fee_type",
            "amount",
            "currency",
            "payment_mode",
            "start_date",
            "end_date",
            "notes",
        )
        help_texts = {
            "end_date": "Déjala vacía para vigencia indefinida.",
            "payment_mode": "Las cuotas no se prorratean.",
        }


class RoomMonthlyFeeForm(RoomRuleForm):
    billing_month = forms.DateField(
        label="Mes de cobro",
        input_formats=["%Y-%m"],
        widget=forms.DateInput(attrs={"type": "month"}, format="%Y-%m"),
    )

    class Meta(VersionedRuleForm.Meta):
        model = RoomMonthlyFee
        fields = (
            "clinic",
            "room",
            "fee_type",
            "billing_month",
            "amount",
            "currency",
            "payment_mode",
            "notes",
        )
        help_texts = {
            "payment_mode": "Las cuotas no se prorratean.",
        }


class CancellationPolicyForm(VersionedRuleForm):
    class Meta(VersionedRuleForm.Meta):
        model = CancellationPolicy
        fields = (
            "clinic",
            "room",
            "name",
            "start_date",
            "end_date",
            "notes",
        )
        help_texts = {
            "room": "Déjalo vacío para aplicar la política a toda la clínica.",
            "end_date": "Déjala vacía para vigencia indefinida.",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        clinic_queryset = Clinic.objects.filter(is_deleted=False).order_by("name")
        room_queryset = ConsultingRoom.objects.filter(is_deleted=False).select_related(
            "clinic"
        )
        source_data = self.data if self.is_bound else self.filter_data
        clinic_pk = selected_model_pk(source_data, "clinic")
        if clinic_pk:
            room_queryset = room_queryset.filter(clinic_id=clinic_pk)
            self.initial.setdefault("clinic", clinic_pk)
        if self.user is not None:
            clinic_queryset = scope_queryset_for_user(clinic_queryset, self.user)
            room_queryset = scope_queryset_for_user(room_queryset, self.user)
        _set_queryset(self.fields["clinic"], clinic_queryset)
        _set_queryset(
            self.fields["room"],
            room_queryset.order_by("clinic__name", "name"),
        )


class CancellationPenaltyRuleForm(forms.Form):
    days_before = forms.IntegerField(
        label="Días naturales antes",
        min_value=0,
        widget=forms.NumberInput(attrs={"min": "0", "step": "1"}),
    )
    percentage = forms.DecimalField(
        label="Penalización",
        min_value=Decimal("0.0"),
        max_value=Decimal("100.0"),
        max_digits=4,
        decimal_places=1,
        widget=forms.NumberInput(attrs={"min": "0", "max": "100", "step": "0.1"}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        style_form_fields(self.fields)


class BaseCancellationPenaltyFormSet(BaseFormSet):
    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        rows = [
            form.cleaned_data
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE", False)
        ]
        if not rows:
            raise ValidationError("Agrega al menos una regla de penalización.")

        days = [row["days_before"] for row in rows]
        if len(days) != len(set(days)):
            raise ValidationError("No se permiten días de anticipación duplicados.")
        if 0 not in days:
            raise ValidationError(
                "Agrega la penalización para cancelación el mismo día."
            )

        ordered = sorted(rows, key=lambda row: row["days_before"])
        previous = ordered[0]
        for current in ordered[1:]:
            if current["percentage"] > previous["percentage"]:
                raise ValidationError(
                    "La penalización no puede aumentar cuando hay más días de "
                    "anticipación."
                )
            previous = current


CancellationPenaltyFormSet = forms.formset_factory(
    CancellationPenaltyRuleForm,
    formset=BaseCancellationPenaltyFormSet,
    extra=1,
    can_delete=True,
)
