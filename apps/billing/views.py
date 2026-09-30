"""Administrative views for versioned commercial configuration."""

from __future__ import annotations

from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Model, QuerySet
from django.forms import ModelForm
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView
from django.views.generic.edit import FormView

from apps.astrotrace.services import record_event
from apps.billing.forms import (
    CancellationPenaltyFormSet,
    CancellationPolicyForm,
    OwnerCommissionRuleForm,
    OwnerPayoutScheduleForm,
    OwnerSubscriptionForm,
    RoomFixedFeeRuleForm,
    RoomMonthlyFeeForm,
    TenantSubscriptionForm,
)
from apps.billing.models import (
    CancellationPenaltyRule,
    CancellationPolicy,
    OwnerCommissionRule,
    OwnerPayoutSchedule,
    OwnerSubscription,
    RoomFixedFeeRule,
    RoomMonthlyFee,
    TenantSubscription,
    VersionedConfiguration,
)
from apps.core.permissions import scope_queryset_for_user


def _validity(instance: VersionedConfiguration) -> str:
    end_text = instance.end_date.isoformat() if instance.end_date else "Sin fin"
    return f"{instance.start_date.isoformat()} a {end_text}"


def _status(instance: Model) -> str:
    return "Activo" if getattr(instance, "is_active", False) else "Inactivo"


def _money(amount: Any, currency: str) -> str:
    return f"{amount} {currency}"


def _section(
    *,
    title: str,
    create_url: str,
    columns: tuple[str, ...],
    rows: list[dict[str, Any]],
    empty_message: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "create_url": create_url,
        "columns": columns,
        "rows": rows,
        "empty_message": empty_message,
    }


class BillingListView(LoginRequiredMixin, TemplateView):
    template_name = "billing/configuration_list.html"
    page_title = ""

    def scoped(self, queryset: QuerySet[Any]) -> QuerySet[Any]:
        return scope_queryset_for_user(
            queryset.filter(is_deleted=False),
            self.request.user,
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = self.page_title
        return context


class OwnerSubscriptionListView(BillingListView):
    page_title = "Suscripciones de propietarios"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        records = self.scoped(
            OwnerSubscription.objects.select_related("owner", "owner__user")
        )
        context["sections"] = [
            _section(
                title="Historial de suscripciones",
                create_url="owner_subscription_create",
                columns=(
                    "Propietario",
                    "Periodicidad",
                    "Importe",
                    "Vigencia",
                    "Versión",
                    "Estado",
                ),
                rows=[
                    {
                        "object": record,
                        "toggle_url": "owner_subscription_toggle",
                        "cells": (
                            record.owner,
                            record.get_billing_cycle_display(),
                            _money(record.amount, record.currency),
                            _validity(record),
                            record.version,
                            _status(record),
                        ),
                    }
                    for record in records
                ],
                empty_message="Sin suscripciones de propietarios registradas.",
            )
        ]
        return context


class TenantSubscriptionListView(BillingListView):
    page_title = "Suscripciones de médicos arrendatarios"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        records = self.scoped(
            TenantSubscription.objects.select_related(
                "tenant_doctor",
                "tenant_doctor__user",
            )
        )
        context["sections"] = [
            _section(
                title="Historial de suscripciones",
                create_url="tenant_subscription_create",
                columns=(
                    "Médico arrendatario",
                    "Periodicidad",
                    "Importe",
                    "Vigencia",
                    "Versión",
                    "Estado",
                ),
                rows=[
                    {
                        "object": record,
                        "toggle_url": "tenant_subscription_toggle",
                        "cells": (
                            record.tenant_doctor,
                            record.get_billing_cycle_display(),
                            _money(record.amount, record.currency),
                            _validity(record),
                            record.version,
                            _status(record),
                        ),
                    }
                    for record in records
                ],
                empty_message="Sin suscripciones de arrendatarios registradas.",
            )
        ]
        return context


class OwnerTermsListView(BillingListView):
    page_title = "Comisiones y pagos a propietarios"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        commissions = self.scoped(
            OwnerCommissionRule.objects.select_related("owner", "owner__user")
        )
        schedules = self.scoped(
            OwnerPayoutSchedule.objects.select_related("owner", "owner__user")
        )
        context["sections"] = [
            _section(
                title="Comisiones",
                create_url="owner_commission_create",
                columns=(
                    "Propietario",
                    "Comisión",
                    "Vigencia",
                    "Versión",
                    "Estado",
                ),
                rows=[
                    {
                        "object": record,
                        "toggle_url": "owner_commission_toggle",
                        "cells": (
                            record.owner,
                            f"{record.percentage}%",
                            _validity(record),
                            record.version,
                            _status(record),
                        ),
                    }
                    for record in commissions
                ],
                empty_message="Sin reglas de comisión registradas.",
            ),
            _section(
                title="Frecuencia de pago de ganancias",
                create_url="owner_payout_create",
                columns=(
                    "Propietario",
                    "Frecuencia",
                    "Vigencia",
                    "Versión",
                    "Estado",
                ),
                rows=[
                    {
                        "object": record,
                        "toggle_url": "owner_payout_toggle",
                        "cells": (
                            record.owner,
                            record.get_frequency_display(),
                            _validity(record),
                            record.version,
                            _status(record),
                        ),
                    }
                    for record in schedules
                ],
                empty_message="Sin frecuencias de pago registradas.",
            ),
        ]
        return context


class RoomFeesListView(BillingListView):
    page_title = "Cuotas por consultorio"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        fixed_fees = self.scoped(
            RoomFixedFeeRule.objects.select_related("room", "room__clinic")
        )
        monthly_fees = self.scoped(
            RoomMonthlyFee.objects.select_related("room", "room__clinic")
        )
        common_columns = (
            "Consultorio",
            "Cuota",
            "Importe",
            "Forma de pago",
        )
        context["sections"] = [
            _section(
                title="Cuotas fijas",
                create_url="room_fixed_fee_create",
                columns=common_columns + ("Vigencia", "Versión", "Estado"),
                rows=[
                    {
                        "object": record,
                        "toggle_url": "room_fixed_fee_toggle",
                        "cells": (
                            record.room,
                            record.get_fee_type_display(),
                            _money(record.amount, record.currency),
                            record.get_payment_mode_display(),
                            _validity(record),
                            record.version,
                            _status(record),
                        ),
                    }
                    for record in fixed_fees
                ],
                empty_message="Sin cuotas fijas registradas.",
            ),
            _section(
                title="Cuotas variables por mes",
                create_url="room_monthly_fee_create",
                columns=common_columns + ("Mes", "Versión", "Estado"),
                rows=[
                    {
                        "object": record,
                        "toggle_url": "room_monthly_fee_toggle",
                        "cells": (
                            record.room,
                            record.get_fee_type_display(),
                            _money(record.amount, record.currency),
                            record.get_payment_mode_display(),
                            record.billing_month.strftime("%Y-%m"),
                            record.version,
                            _status(record),
                        ),
                    }
                    for record in monthly_fees
                ],
                empty_message="Sin cuotas variables registradas.",
            ),
        ]
        return context


class CancellationPolicyListView(BillingListView):
    page_title = "Políticas de cancelación"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        policies = self.scoped(
            CancellationPolicy.objects.select_related(
                "clinic", "room"
            ).prefetch_related("penalty_rules")
        )
        context["sections"] = [
            _section(
                title="Historial de políticas",
                create_url="cancellation_policy_create",
                columns=(
                    "Alcance",
                    "Política",
                    "Penalizaciones",
                    "Vigencia",
                    "Versión",
                    "Estado",
                ),
                rows=[
                    {
                        "object": policy,
                        "toggle_url": "cancellation_policy_toggle",
                        "cells": (
                            policy.room or policy.clinic,
                            policy.name,
                            ", ".join(
                                (
                                    "Mismo día"
                                    if rule.days_before == 0
                                    else f"Hasta {rule.days_before} días antes"
                                )
                                + f": {rule.percentage}%"
                                for rule in policy.penalty_rules.all()
                            ),
                            _validity(policy),
                            policy.version,
                            _status(policy),
                        ),
                    }
                    for policy in policies
                ],
                empty_message="Sin políticas de cancelación registradas.",
            )
        ]
        return context


class ConfigurationCreateView(LoginRequiredMixin, FormView):
    template_name = "billing/configuration_form.html"
    form_class: type[ModelForm]
    page_title = ""
    cancel_url_name = ""
    event_prefix = ""
    success_message = "Configuración registrada."

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["filter_data"] = self.request.GET
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "page_title": self.page_title,
                "cancel_url_name": self.cancel_url_name,
            }
        )
        return context

    def form_valid(self, form: ModelForm) -> HttpResponse:
        instance = cast(VersionedConfiguration, form.save(commit=False))
        actor = cast(Model, self.request.user)
        user = cast(Any, self.request.user)
        instance.created_by = user
        instance.updated_by = user
        try:
            with transaction.atomic():
                instance.save()
                form.save_m2m()
                _record_configuration_event(
                    instance,
                    event_type=f"billing.{self.event_prefix}.created",
                    action="created",
                    actor=actor,
                )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(self.request, self.success_message)
        return redirect(self.cancel_url_name)


class OwnerSubscriptionCreateView(ConfigurationCreateView):
    form_class = OwnerSubscriptionForm
    page_title = "Nueva suscripción de propietario"
    cancel_url_name = "owner_subscriptions"
    event_prefix = "owner_subscription"


class TenantSubscriptionCreateView(ConfigurationCreateView):
    form_class = TenantSubscriptionForm
    page_title = "Nueva suscripción de médico arrendatario"
    cancel_url_name = "tenant_subscriptions"
    event_prefix = "tenant_subscription"


class OwnerCommissionCreateView(ConfigurationCreateView):
    form_class = OwnerCommissionRuleForm
    page_title = "Nueva regla de comisión"
    cancel_url_name = "owner_terms"
    event_prefix = "owner_commission"


class OwnerPayoutCreateView(ConfigurationCreateView):
    form_class = OwnerPayoutScheduleForm
    page_title = "Nueva frecuencia de pago"
    cancel_url_name = "owner_terms"
    event_prefix = "owner_payout_schedule"


class RoomFixedFeeCreateView(ConfigurationCreateView):
    form_class = RoomFixedFeeRuleForm
    page_title = "Nueva cuota fija"
    cancel_url_name = "room_fees"
    event_prefix = "room_fixed_fee"


class RoomMonthlyFeeCreateView(ConfigurationCreateView):
    form_class = RoomMonthlyFeeForm
    page_title = "Nueva cuota variable mensual"
    cancel_url_name = "room_fees"
    event_prefix = "room_monthly_fee"


class ConfigurationToggleView(LoginRequiredMixin, View):
    model: type[VersionedConfiguration]
    list_url_name = ""
    event_prefix = ""

    def get_queryset(self) -> QuerySet[Any]:
        queryset = self.model.objects.filter(is_deleted=False)
        return scope_queryset_for_user(queryset, self.request.user)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        instance = get_object_or_404(self.get_queryset(), pk=kwargs["pk"])
        instance.is_active = not instance.is_active
        instance.updated_by = cast(Any, request.user)
        action = "activated" if instance.is_active else "deactivated"
        try:
            with transaction.atomic():
                instance.save()
                _record_configuration_event(
                    instance,
                    event_type=f"billing.{self.event_prefix}.{action}",
                    action=action,
                    actor=cast(Model, request.user),
                )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            verb = "activada" if instance.is_active else "desactivada"
            messages.success(request, f"Configuración {verb}.")
        return redirect(self.list_url_name)


class OwnerSubscriptionToggleView(ConfigurationToggleView):
    model = OwnerSubscription
    list_url_name = "owner_subscriptions"
    event_prefix = "owner_subscription"


class TenantSubscriptionToggleView(ConfigurationToggleView):
    model = TenantSubscription
    list_url_name = "tenant_subscriptions"
    event_prefix = "tenant_subscription"


class OwnerCommissionToggleView(ConfigurationToggleView):
    model = OwnerCommissionRule
    list_url_name = "owner_terms"
    event_prefix = "owner_commission"


class OwnerPayoutToggleView(ConfigurationToggleView):
    model = OwnerPayoutSchedule
    list_url_name = "owner_terms"
    event_prefix = "owner_payout_schedule"


class RoomFixedFeeToggleView(ConfigurationToggleView):
    model = RoomFixedFeeRule
    list_url_name = "room_fees"
    event_prefix = "room_fixed_fee"


class RoomMonthlyFeeToggleView(ConfigurationToggleView):
    model = RoomMonthlyFee
    list_url_name = "room_fees"
    event_prefix = "room_monthly_fee"


class CancellationPolicyToggleView(ConfigurationToggleView):
    model = CancellationPolicy
    list_url_name = "cancellation_policies"
    event_prefix = "cancellation_policy"


class CancellationPolicyCreateView(LoginRequiredMixin, FormView):
    template_name = "billing/cancellation_policy_form.html"
    form_class = CancellationPolicyForm
    success_url = reverse_lazy("cancellation_policies")

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["filter_data"] = self.request.GET
        return kwargs

    def get_formset(self) -> Any:
        data = self.request.POST if self.request.method == "POST" else None
        initial = None if data is not None else [{"days_before": 0}]
        return CancellationPenaltyFormSet(
            data=data,
            initial=initial,
            prefix="penalties",
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Nueva política de cancelación"
        context["penalty_formset"] = kwargs.get(
            "penalty_formset",
            self.get_formset(),
        )
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        formset = self.get_formset()
        if not form.is_valid() or not formset.is_valid():
            return self.render_to_response(
                self.get_context_data(form=form, penalty_formset=formset)
            )

        actor = cast(Model, request.user)
        user = cast(Any, request.user)
        policy = cast(CancellationPolicy, form.save(commit=False))
        policy.created_by = user
        policy.updated_by = user
        rows = sorted(
            (
                row
                for row in formset.cleaned_data
                if row and not row.get("DELETE", False)
            ),
            key=lambda row: row["days_before"],
        )
        try:
            with transaction.atomic():
                policy.save()
                for row in rows:
                    rule = CancellationPenaltyRule.objects.create(
                        policy=policy,
                        days_before=row["days_before"],
                        percentage=row["percentage"],
                        created_by=user,
                        updated_by=user,
                    )
                    record_event(
                        event_type="billing.cancellation_penalty_rule.created",
                        object_label=str(rule),
                        actor=actor,
                        payload={
                            "model": rule._meta.label,
                            "id": str(rule.pk),
                            "policy_id": str(policy.pk),
                            "days_before": rule.days_before,
                            "percentage": str(rule.percentage),
                        },
                    )
                _record_configuration_event(
                    policy,
                    event_type="billing.cancellation_policy.created",
                    action="created",
                    actor=actor,
                    extra={
                        "penalties": [
                            {
                                "days_before": row["days_before"],
                                "percentage": str(row["percentage"]),
                            }
                            for row in rows
                        ]
                    },
                )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.render_to_response(
                self.get_context_data(form=form, penalty_formset=formset)
            )

        messages.success(request, "Política de cancelación registrada.")
        return redirect(self.success_url)


def _record_configuration_event(
    instance: VersionedConfiguration,
    *,
    event_type: str,
    action: str,
    actor: Model,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "model": instance._meta.label,
        "id": str(instance.pk),
        "version": instance.version,
        "action": action,
        "is_active": instance.is_active,
        "start_date": instance.start_date.isoformat(),
        "end_date": instance.end_date.isoformat() if instance.end_date else "",
    }
    payload.update(extra or {})
    record_event(
        event_type=event_type,
        object_label=str(instance),
        actor=actor,
        payload=payload,
    )
