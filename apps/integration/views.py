"""Views for simulated access-control integration."""

from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Model, Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.edit import FormMixin

from apps.core.permissions import scope_queryset_for_user
from apps.integration.forms import AccessRevokeForm
from apps.integration.models import AccessCredential
from apps.integration.services.access_simulator import (
    expire_old_credentials,
    provision_access_for_reservation,
    revoke_access_credential,
    simulate_access_use,
)
from apps.scheduling.models import Reservation


class AccessCredentialListView(LoginRequiredMixin, ListView):
    template_name = "integration/access_list.html"
    context_object_name = "credentials"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[AccessCredential]:
        queryset = AccessCredential.objects.filter(is_deleted=False).select_related(
            "reservation",
            "tenant_doctor",
            "tenant_doctor__user",
            "room",
            "room__clinic",
        )
        self.search_query = self.request.GET.get("q", "").strip()
        if self.search_query:
            queryset = queryset.filter(
                Q(simulated_code__icontains=self.search_query)
                | Q(reservation__room__name__icontains=self.search_query)
                | Q(tenant_doctor__display_name__icontains=self.search_query)
                | Q(tenant_doctor__user__email__icontains=self.search_query)
                | Q(room__name__icontains=self.search_query)
                | Q(room__clinic__name__icontains=self.search_query)
            )
        return scope_queryset_for_user(queryset, self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Accesos simulados"
        context["search_query"] = self.search_query
        return context


class AccessCredentialDetailView(LoginRequiredMixin, DetailView):
    template_name = "integration/access_detail.html"
    context_object_name = "credential"

    def get_queryset(self) -> QuerySet[AccessCredential]:
        queryset = AccessCredential.objects.filter(is_deleted=False).select_related(
            "reservation",
            "tenant_doctor",
            "tenant_doctor__user",
            "room",
            "room__clinic",
            "created_by",
            "updated_by",
        )
        return scope_queryset_for_user(queryset, self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Detalle de acceso simulado"
        return context


class AccessProvisionView(LoginRequiredMixin, TemplateView):
    template_name = "integration/access_provision.html"

    def get_reservation(self) -> Reservation:
        return Reservation.objects.select_related(
            "room",
            "room__clinic",
            "tenant_doctor",
            "tenant_doctor__user",
        ).get(pk=self.kwargs["reservation_pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Habilitar acceso simulado"
        context["reservation"] = self.get_reservation()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        reservation = self.get_reservation()
        try:
            credential = provision_access_for_reservation(
                reservation=reservation,
                user=cast(Model, request.user),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("reservation_detail", pk=reservation.pk)

        messages.success(request, "Acceso simulado habilitado.")
        return redirect("access_credential_detail", pk=credential.pk)


class AccessUseView(LoginRequiredMixin, TemplateView):
    template_name = "integration/access_use.html"

    def get_credential(self) -> AccessCredential:
        return AccessCredential.objects.select_related(
            "reservation",
            "tenant_doctor",
            "room",
        ).get(pk=self.kwargs["pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Simular uso de acceso"
        context["credential"] = self.get_credential()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        credential = self.get_credential()
        try:
            simulate_access_use(credential=credential, user=cast(Model, request.user))
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("access_credential_detail", pk=credential.pk)

        messages.success(request, "Uso de acceso simulado registrado.")
        return redirect("access_credential_detail", pk=credential.pk)


class AccessRevokeView(LoginRequiredMixin, FormMixin, TemplateView):
    template_name = "integration/access_revoke.html"
    form_class = AccessRevokeForm

    def get_credential(self) -> AccessCredential:
        return AccessCredential.objects.select_related(
            "reservation",
            "tenant_doctor",
            "room",
        ).get(pk=self.kwargs["pk"], is_deleted=False)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Revocar acceso simulado"
        context["credential"] = self.get_credential()
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            credential = self.get_credential()
            try:
                revoke_access_credential(
                    credential=credential,
                    user=cast(Model, request.user),
                    reason=form.cleaned_data["reason"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
                return self.form_invalid(form)

            messages.success(request, "Acceso simulado revocado.")
            return redirect("access_credential_detail", pk=credential.pk)
        return self.form_invalid(form)


class AccessExpireView(LoginRequiredMixin, TemplateView):
    template_name = "integration/access_expire.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Expirar accesos vencidos"
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        expired_count = expire_old_credentials()
        messages.success(request, f"Credenciales expiradas: {expired_count}.")
        return redirect("access_credentials")
