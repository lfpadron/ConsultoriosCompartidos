"""Forms for finance screens."""

from decimal import Decimal
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
from apps.finance.models import (
    AccountPartyType,
    AccountPayment,
    AccountPaymentCategory,
    AccountStatement,
    AccountStatementStatus,
    OwnerPayout,
    Payment,
    PaymentMethod,
    PaymentStatus,
    RateRule,
    RoomRateDiscount,
    SettlementStatus,
    TenantDoctorDiscount,
)
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


def _rate_rule_queryset(data: Any = None) -> QuerySet[RateRule]:
    queryset = RateRule.objects.filter(is_deleted=False).select_related(
        "room",
        "room__clinic",
        "room__owner",
    )
    room_pk = selected_model_pk(data, "room")
    if room_pk:
        queryset = queryset.filter(room_id=room_pk)
    return queryset.order_by("room__clinic__name", "room__name", "name")


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


class RoomRateDiscountForm(BootstrapModelForm):
    clinic = forms.ModelChoiceField(
        label="Clínica",
        queryset=Clinic.objects.none(),
        required=False,
    )

    class Meta:
        model = RoomRateDiscount
        fields = (
            "clinic",
            "room",
            "rate_rule",
            "percentage",
            "start_date",
            "end_date",
        )
        labels = {
            "room": "Consultorio",
            "rate_rule": "Regla tarifaria",
            "percentage": "Porcentaje de descuento",
            "start_date": "Fecha inicio de vigencia",
            "end_date": "Fecha fin de vigencia",
        }
        widgets = {
            "start_date": monday_date_input(),
            "end_date": monday_date_input(),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        source_data = self.data if self.is_bound else self.filter_data
        clinic_queryset = _clinic_queryset()
        room_queryset = _room_queryset(source_data)
        rate_rule_queryset = _rate_rule_queryset(source_data)
        if self.user is not None:
            clinic_queryset = scope_queryset_for_user(clinic_queryset, self.user)
            room_queryset = scope_queryset_for_user(room_queryset, self.user)
            rate_rule_queryset = scope_queryset_for_user(
                rate_rule_queryset,
                self.user,
            )
        set_model_queryset(self.fields["clinic"], clinic_queryset)
        set_model_queryset(self.fields["room"], room_queryset)
        set_model_queryset(self.fields["rate_rule"], rate_rule_queryset)
        self.fields["percentage"].min_value = Decimal("0.0")
        self.fields["percentage"].max_value = Decimal("99.0")


class TenantDoctorDiscountForm(BootstrapModelForm):
    class Meta:
        model = TenantDoctorDiscount
        fields = (
            "tenant_doctor",
            "percentage",
            "start_date",
            "end_date",
        )
        labels = {
            "tenant_doctor": "Médico arrendatario",
            "percentage": "Porcentaje de descuento",
            "start_date": "Fecha inicio de vigencia",
            "end_date": "Fecha fin de vigencia",
        }
        widgets = {
            "start_date": monday_date_input(),
            "end_date": monday_date_input(),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        tenant_queryset = (
            TenantDoctorProfile.objects.filter(is_deleted=False)
            .select_related("user")
            .order_by("display_name", "user__email")
        )
        if self.user is not None:
            tenant_queryset = scope_queryset_for_user(tenant_queryset, self.user)
        set_model_queryset(self.fields["tenant_doctor"], tenant_queryset)
        self.fields["percentage"].min_value = Decimal("0.0")
        self.fields["percentage"].max_value = Decimal("99.0")


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


class BatchPaymentSubmissionForm(PaymentRegistrationForm):
    credit_amount = forms.DecimalField(
        label="Saldo a favor a aplicar",
        required=False,
        min_value=Decimal("0.00"),
        decimal_places=2,
        max_digits=12,
        initial=Decimal("0.00"),
    )

    def __init__(
        self,
        *args: Any,
        required_total: Decimal,
        currency: str,
        available_credit: Decimal = Decimal("0.00"),
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.required_total = required_total
        self.available_credit = available_credit
        amount_field = self.fields["amount"]
        amount_field.initial = required_total
        amount_field.widget.attrs["min"] = f"{required_total:.2f}"
        self.fields["currency"].initial = currency
        self.fields["currency"].widget.attrs["readonly"] = True
        self.fields["credit_amount"].widget.attrs["max"] = f"{available_credit:.2f}"
        self.fields["credit_amount"].help_text = (
            f"Disponible: {available_credit:.2f} {currency}."
        )
        self.fields["receipt"].required = False
        self.fields["receipt"].help_text = (
            "Obligatorio cuando una parte del total se paga fuera del saldo a favor."
        )

    def clean_amount(self) -> Decimal:
        amount = self.cleaned_data["amount"]
        if amount < self.required_total:
            raise forms.ValidationError(
                f"El comprobante debe cubrir al menos {self.required_total:.2f}."
            )
        return amount

    def clean_currency(self) -> str:
        currency = self.cleaned_data["currency"]
        if currency != self.fields["currency"].initial:
            raise forms.ValidationError("La moneda debe coincidir con el grupo.")
        return currency

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        amount = cleaned_data.get("amount")
        credit_amount = cleaned_data.get("credit_amount") or Decimal("0.00")
        cleaned_data["credit_amount"] = credit_amount
        if amount is None:
            return cleaned_data
        maximum_credit = min(amount, self.required_total, self.available_credit)
        if credit_amount > maximum_credit:
            self.add_error(
                "credit_amount",
                f"Sólo hay {self.available_credit:.2f} disponibles para aplicar.",
            )
            return cleaned_data
        cash_amount = amount - credit_amount
        if cash_amount > Decimal("0.00"):
            if not cleaned_data.get("receipt"):
                self.add_error("receipt", "El comprobante es obligatorio.")
            if cleaned_data.get("method") == PaymentMethod.CREDIT:
                self.add_error(
                    "method",
                    "Selecciona el método usado para pagar el importe restante.",
                )
        else:
            cleaned_data["method"] = PaymentMethod.CREDIT
            cleaned_data["reference"] = (
                cleaned_data.get("reference") or "Aplicación de saldo a favor"
            )
        return cleaned_data


class PaymentRejectForm(forms.Form):
    reason = forms.CharField(
        label="Motivo de rechazo",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs["class"] = "form-control"


class AccountStatementFilterForm(forms.Form):
    party_type = forms.ChoiceField(
        label="Tipo de titular",
        choices=(("", "Todos"), *AccountPartyType.choices),
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
        label="Estado",
        choices=(("", "Todos"), *AccountStatementStatus.choices),
        required=False,
    )
    date_from = forms.DateField(
        label="Periodo desde",
        required=False,
        widget=monday_date_input(),
    )
    date_to = forms.DateField(
        label="Periodo hasta",
        required=False,
        widget=monday_date_input(),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        owner_queryset = _owner_queryset()
        tenant_queryset = TenantDoctorProfile.objects.filter(
            is_deleted=False
        ).select_related("user")
        if self.user is not None:
            owner_queryset = scope_queryset_for_user(owner_queryset, self.user)
            tenant_queryset = scope_queryset_for_user(tenant_queryset, self.user)
        set_model_queryset(self.fields["owner"], owner_queryset)
        set_model_queryset(
            self.fields["tenant_doctor"],
            tenant_queryset.order_by("display_name", "user__email"),
        )
        style_form_fields(self.fields)


class AccountStatementGenerationForm(forms.Form):
    party_type = forms.ChoiceField(
        label="Tipo de titular",
        choices=AccountPartyType.choices,
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
    period_start = forms.DateField(
        label="Inicio del periodo",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    period_end = forms.DateField(
        label="Fin del periodo",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    currency = forms.CharField(label="Moneda", max_length=3, initial="MXN")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        owner_queryset = _owner_queryset()
        tenant_queryset = TenantDoctorProfile.objects.filter(
            is_deleted=False
        ).select_related("user")
        if self.user is not None:
            owner_queryset = scope_queryset_for_user(owner_queryset, self.user)
            tenant_queryset = scope_queryset_for_user(tenant_queryset, self.user)
        set_model_queryset(self.fields["owner"], owner_queryset)
        set_model_queryset(self.fields["tenant_doctor"], tenant_queryset)
        style_form_fields(self.fields)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        party_type = cleaned_data.get("party_type")
        owner = cleaned_data.get("owner")
        tenant_doctor = cleaned_data.get("tenant_doctor")
        if party_type == AccountPartyType.OWNER:
            if owner is None:
                self.add_error("owner", "Selecciona al propietario.")
            if tenant_doctor is not None:
                self.add_error(
                    "tenant_doctor",
                    "No selecciones un arrendatario para este corte.",
                )
        elif party_type == AccountPartyType.TENANT:
            if tenant_doctor is None:
                self.add_error("tenant_doctor", "Selecciona al médico arrendatario.")
            if owner is not None:
                self.add_error(
                    "owner",
                    "No selecciones un propietario para este corte.",
                )
        period_start = cleaned_data.get("period_start")
        period_end = cleaned_data.get("period_end")
        if period_start and period_end and period_end < period_start:
            self.add_error("period_end", "El fin no puede ser anterior al inicio.")
        cleaned_data["currency"] = (cleaned_data.get("currency") or "MXN").upper()
        return cleaned_data


class AccountPaymentForm(BootstrapModelForm):
    class Meta:
        model = AccountPayment
        fields = (
            "category",
            "amount",
            "method",
            "reference",
            "payment_date",
            "receipt",
            "notes",
        )
        widgets = {
            "payment_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(
        self,
        *args: Any,
        account_statement: AccountStatement,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.account_statement = account_statement
        choices: tuple[tuple[Any, Any], ...]
        if account_statement.party_type == AccountPartyType.OWNER:
            choices = (
                (
                    AccountPaymentCategory.OWNER_SUBSCRIPTION,
                    AccountPaymentCategory.OWNER_SUBSCRIPTION.label,
                ),
                (
                    AccountPaymentCategory.OWNER_FEE,
                    AccountPaymentCategory.OWNER_FEE.label,
                ),
            )
        else:
            choices = (
                (
                    AccountPaymentCategory.TENANT_SUBSCRIPTION,
                    AccountPaymentCategory.TENANT_SUBSCRIPTION.label,
                ),
            )
        category_field = self.fields["category"]
        method_field = self.fields["method"]
        if isinstance(category_field, forms.ChoiceField):
            category_field.choices = choices
        if isinstance(method_field, forms.ChoiceField):
            method_field.choices = tuple(
                choice
                for choice in PaymentMethod.choices
                if choice[0] != PaymentMethod.CREDIT
            )
        self.fields["receipt"].required = False
        self.fields["receipt"].help_text = (
            "Obligatorio para métodos distintos de efectivo."
        )
        self.fields["amount"].initial = account_statement.balance_due
        self.fields["amount"].widget.attrs["max"] = str(
            account_statement.balance_due
        )
        self.fields["amount"].help_text = (
            f"Máximo pendiente: {account_statement.balance_due} "
            f"{account_statement.currency}."
        )

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        if (
            cleaned_data.get("method") != PaymentMethod.CASH
            and not cleaned_data.get("receipt")
        ):
            self.add_error("receipt", "El comprobante es obligatorio.")
        return cleaned_data

    def clean_amount(self) -> Decimal:
        amount = self.cleaned_data["amount"]
        if amount > self.account_statement.balance_due:
            raise forms.ValidationError(
                "El importe no puede exceder el saldo por pagar."
            )
        return amount


class OwnerPayoutForm(BootstrapModelForm):
    class Meta:
        model = OwnerPayout
        fields = (
            "amount",
            "reference",
            "payment_date",
            "receipt",
            "notes",
        )
        widgets = {
            "payment_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(
        self,
        *args: Any,
        account_statement: AccountStatement,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.account_statement = account_statement
        self.fields["amount"].initial = account_statement.payout_due
        self.fields["amount"].widget.attrs["max"] = str(account_statement.payout_due)
        self.fields["amount"].help_text = (
            f"Máximo pendiente: {account_statement.payout_due} "
            f"{account_statement.currency}."
        )
        self.fields["receipt"].required = True

    def clean_amount(self) -> Decimal:
        amount = self.cleaned_data["amount"]
        if amount > self.account_statement.payout_due:
            raise forms.ValidationError(
                "El importe no puede exceder el saldo por entregar."
            )
        return amount


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
