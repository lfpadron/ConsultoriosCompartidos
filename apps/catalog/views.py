"""Catalog CRUD views."""

from dataclasses import dataclass
from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Model, Q, QuerySet
from django.forms import ModelChoiceField, ModelForm
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.edit import FormMixin

from apps.astrotrace.services import record_event
from apps.catalog.forms import (
    ClinicForm,
    ConsultingRoomForm,
    EquipmentForm,
    OwnerProfileForm,
    RoomCatalogFilterForm,
    SpecialtyForm,
    TenantDoctorProfileForm,
)
from apps.catalog.models import (
    Clinic,
    ConsultingRoom,
    Equipment,
    OwnerProfile,
    Specialty,
    TenantDoctorProfile,
)
from apps.core.permissions import scope_queryset_for_user
from apps.identity.models import UserRole
from apps.vault.services.document_service import (
    get_document_field_for_object,
    get_documents_for_object,
)


@dataclass(frozen=True)
class CatalogResource:
    model: type[Any]
    form_class: type[ModelForm]
    singular_label: str
    plural_label: str
    list_url_name: str
    create_url_name: str
    detail_url_name: str
    update_url_name: str
    deactivate_url_name: str
    list_columns: tuple[tuple[str, str], ...]
    detail_fields: tuple[tuple[str, str], ...]
    select_related: tuple[str, ...] = ()
    prefetch_related: tuple[str, ...] = ()
    create_event_type: str | None = None
    update_event_type: str | None = None


CLINIC = CatalogResource(
    model=Clinic,
    form_class=ClinicForm,
    singular_label="Clínica",
    plural_label="Clínicas",
    list_url_name="clinics",
    create_url_name="clinic_create",
    detail_url_name="clinic_detail",
    update_url_name="clinic_update",
    deactivate_url_name="clinic_deactivate",
    list_columns=(
        ("Nombre", "name"),
        ("Teléfono", "phone"),
        ("Email", "email"),
        ("Zona horaria", "timezone"),
        ("Formato hora", "get_hour_format_display"),
        ("Estado", "is_active"),
    ),
    detail_fields=(
        ("Nombre", "name"),
        ("Dirección", "address"),
        ("Teléfono", "phone"),
        ("Email", "email"),
        ("Horario", "schedule_text"),
        ("Zona horaria", "timezone"),
        ("Formato hora", "get_hour_format_display"),
        ("Estado", "is_active"),
    ),
    create_event_type="clinic.created",
    update_event_type="clinic.updated",
)

SPECIALTY = CatalogResource(
    model=Specialty,
    form_class=SpecialtyForm,
    singular_label="Especialidad",
    plural_label="Especialidades",
    list_url_name="specialties",
    create_url_name="specialty_create",
    detail_url_name="specialty_detail",
    update_url_name="specialty_update",
    deactivate_url_name="specialty_deactivate",
    list_columns=(("Nombre", "name"), ("Estado", "is_active")),
    detail_fields=(
        ("Nombre", "name"),
        ("Descripción", "description"),
        ("Estado", "is_active"),
    ),
)

EQUIPMENT = CatalogResource(
    model=Equipment,
    form_class=EquipmentForm,
    singular_label="Equipamiento",
    plural_label="Equipamiento",
    list_url_name="equipment",
    create_url_name="equipment_create",
    detail_url_name="equipment_detail",
    update_url_name="equipment_update",
    deactivate_url_name="equipment_deactivate",
    list_columns=(("Nombre", "name"), ("Estado", "is_active")),
    detail_fields=(
        ("Nombre", "name"),
        ("Descripción", "description"),
        ("Estado", "is_active"),
    ),
)

OWNER = CatalogResource(
    model=OwnerProfile,
    form_class=OwnerProfileForm,
    singular_label="Propietario",
    plural_label="Propietarios",
    list_url_name="owners",
    create_url_name="owner_create",
    detail_url_name="owner_detail",
    update_url_name="owner_update",
    deactivate_url_name="owner_deactivate",
    list_columns=(
        ("Nombre", "display_name"),
        ("Usuario", "user.email"),
        ("Teléfono", "phone"),
        ("Estado", "is_active"),
    ),
    detail_fields=(
        ("Usuario", "user.email"),
        ("Nombre público", "display_name"),
        ("Cédula profesional", "professional_license"),
        ("RFC", "tax_id"),
        ("Teléfono", "phone"),
        ("Notas", "notes"),
        ("Estado", "is_active"),
    ),
    select_related=("user",),
    create_event_type="owner.created",
)

TENANT_DOCTOR = CatalogResource(
    model=TenantDoctorProfile,
    form_class=TenantDoctorProfileForm,
    singular_label="Médico Arrendatario",
    plural_label="Médicos Arrendatarios",
    list_url_name="tenant_doctors",
    create_url_name="tenant_doctor_create",
    detail_url_name="tenant_doctor_detail",
    update_url_name="tenant_doctor_update",
    deactivate_url_name="tenant_doctor_deactivate",
    list_columns=(
        ("Nombre", "display_name"),
        ("Usuario", "user.email"),
        ("Estado médico", "get_status_display"),
        ("Activo", "is_active"),
    ),
    detail_fields=(
        ("Usuario", "user.email"),
        ("Nombre público", "display_name"),
        ("Especialidades", "specialties"),
        ("Cédula profesional", "professional_license"),
        ("RFC", "tax_id"),
        ("Teléfono", "phone"),
        ("Estado médico", "get_status_display"),
        ("Notas", "notes"),
        ("Activo", "is_active"),
    ),
    select_related=("user",),
    prefetch_related=("specialties",),
    create_event_type="tenant_doctor.created",
)

ROOM = CatalogResource(
    model=ConsultingRoom,
    form_class=ConsultingRoomForm,
    singular_label="Consultorio",
    plural_label="Consultorios",
    list_url_name="rooms",
    create_url_name="room_create",
    detail_url_name="room_detail",
    update_url_name="room_update",
    deactivate_url_name="room_deactivate",
    list_columns=(
        ("Nombre", "name"),
        ("Clínica", "clinic"),
        ("Propietario", "owner"),
        ("Estado", "get_status_display"),
        ("Activo", "is_active"),
    ),
    detail_fields=(
        ("Clínica", "clinic"),
        ("Propietario", "owner"),
        ("Número / nombre", "name"),
        ("Descripción", "description"),
        ("Piso", "floor"),
        ("Capacidad", "capacity"),
        ("Especialidades permitidas", "allowed_specialties"),
        ("Especialidades excluidas", "excluded_specialties"),
        ("Equipamiento", "equipment"),
        ("Reglamento", "regulations_text"),
        ("Estado", "get_status_display"),
        ("Activo", "is_active"),
    ),
    select_related=("clinic", "owner", "owner__user"),
    prefetch_related=("allowed_specialties", "excluded_specialties", "equipment"),
    create_event_type="consulting_room.created",
    update_event_type="consulting_room.updated",
)


def is_related_collection(value: Any) -> bool:
    return hasattr(value, "all") and not isinstance(value, Model)


def resolve_value(instance: Model, field_path: str) -> str:
    value: Any = instance
    for attr in field_path.split("."):
        value = getattr(value, attr)
        if callable(value) and not is_related_collection(value):
            value = value()

    if isinstance(value, bool):
        return "Activo" if value else "Inactivo"

    if isinstance(value, QuerySet) or is_related_collection(value):
        items = [str(item) for item in value.all()]
        return ", ".join(items) if items else "Sin datos"

    return str(value) if value not in ("", None) else "Sin datos"


class CatalogBaseMixin(LoginRequiredMixin):
    resource: CatalogResource

    def get_queryset(self) -> QuerySet[Any]:
        queryset = self.resource.model._default_manager.filter(is_deleted=False)
        if self.resource.select_related:
            queryset = queryset.select_related(*self.resource.select_related)
        if self.resource.prefetch_related:
            queryset = queryset.prefetch_related(*self.resource.prefetch_related)
        request = cast(Any, self).request
        return scope_queryset_for_user(queryset, request.user)

    def add_resource_context(self, context: dict[str, Any]) -> dict[str, Any]:
        context["resource"] = self.resource
        context["page_title"] = self.resource.plural_label
        return context


class CatalogListView(CatalogBaseMixin, ListView):
    template_name = "catalog/list.html"
    context_object_name = "objects"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[Any]:
        queryset = super().get_queryset()
        self.filter_form = None
        if self.resource.model is ConsultingRoom:
            self.filter_form = RoomCatalogFilterForm(self.request.GET or None)
            self._scope_room_filter_form(self.filter_form)
            cleaned_data: dict[str, Any] = {}
            if self.filter_form.is_bound:
                self.filter_form.is_valid()
                cleaned_data = self.filter_form.cleaned_data
            self.search_query = (cleaned_data.get("q") or "").strip()
            clinic = cleaned_data.get("clinic")
            owner = cleaned_data.get("owner")
            room = cleaned_data.get("room")
            if clinic:
                queryset = queryset.filter(clinic=clinic)
            if owner:
                queryset = queryset.filter(owner=owner)
            if room:
                queryset = queryset.filter(pk=room.pk)
        else:
            self.search_query = self.request.GET.get("q", "").strip()
        if self.search_query:
            queryset = filter_catalog_queryset(
                queryset,
                self.resource.model,
                self.search_query,
            )
        return queryset

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = self.add_resource_context(super().get_context_data(**kwargs))
        objects = context["objects"]
        context["search_query"] = self.search_query
        context["filter_form"] = self.filter_form
        context["rows"] = [
            {
                "object": item,
                "cells": [
                    resolve_value(item, field_path)
                    for _, field_path in self.resource.list_columns
                ],
            }
            for item in objects
        ]
        return context

    def _scope_room_filter_form(self, filter_form: RoomCatalogFilterForm) -> None:
        owner_field = filter_form.fields["owner"]
        if (
            isinstance(owner_field, ModelChoiceField)
            and owner_field.queryset is not None
        ):
            owner_field.queryset = scope_queryset_for_user(
                owner_field.queryset,
                self.request.user,
            )
        room_field = filter_form.fields["room"]
        if isinstance(room_field, ModelChoiceField) and room_field.queryset is not None:
            room_field.queryset = scope_queryset_for_user(
                room_field.queryset,
                self.request.user,
            )


def filter_catalog_queryset(
    queryset: QuerySet[Any],
    model: type[Any],
    query: str,
) -> QuerySet[Any]:
    if model is ConsultingRoom:
        return queryset.filter(
            Q(name__icontains=query)
            | Q(clinic__name__icontains=query)
            | Q(owner__display_name__icontains=query)
            | Q(owner__user__email__icontains=query)
        )
    if model is OwnerProfile:
        return queryset.filter(
            Q(display_name__icontains=query)
            | Q(user__email__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(phone__icontains=query)
            | Q(tax_id__icontains=query)
            | Q(professional_license__icontains=query)
        )
    if model is TenantDoctorProfile:
        return queryset.filter(
            Q(display_name__icontains=query)
            | Q(user__email__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(phone__icontains=query)
            | Q(tax_id__icontains=query)
            | Q(professional_license__icontains=query)
            | Q(specialties__name__icontains=query)
        ).distinct()
    if model is Clinic:
        return queryset.filter(
            Q(name__icontains=query)
            | Q(address__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
        )
    if model in {Specialty, Equipment}:
        return queryset.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        )
    return queryset


class CatalogDetailView(CatalogBaseMixin, DetailView):
    template_name = "catalog/detail.html"
    context_object_name = "object"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = self.add_resource_context(super().get_context_data(**kwargs))
        instance = context["object"]
        context["field_rows"] = [
            (label, resolve_value(instance, field_path))
            for label, field_path in self.resource.detail_fields
        ]
        if isinstance(instance, OwnerProfile | TenantDoctorProfile | ConsultingRoom):
            context["related_documents"] = get_documents_for_object(instance)
            context["document_upload_field"] = get_document_field_for_object(instance)
            if isinstance(instance, OwnerProfile):
                context["timeline_url_name"] = "owner_timeline"
            elif isinstance(instance, TenantDoctorProfile):
                context["timeline_url_name"] = "tenant_doctor_timeline"
            elif isinstance(instance, ConsultingRoom):
                context["timeline_url_name"] = "room_timeline"
        return context


class CatalogFormView(CatalogBaseMixin, FormMixin, TemplateView):
    template_name = "catalog/form.html"
    form_class: type[ModelForm]
    object: Model | None = None
    is_create = False

    def get_form_class(self) -> type[ModelForm]:
        return self.resource.form_class

    def get_object(self) -> Model | None:
        if self.object is not None:
            return self.object
        pk = self.kwargs.get("pk")
        if pk is None:
            return None
        self.object = self.get_queryset().get(pk=pk)
        return self.object

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        instance = self.get_object()
        if instance is not None:
            kwargs["instance"] = instance
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = self.add_resource_context(super().get_context_data(**kwargs))
        action = "Alta" if self.is_create else "Edición"
        context["page_title"] = f"{action} de {self.resource.singular_label.lower()}"
        context["object"] = self.get_object()
        context["is_create"] = self.is_create
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form: ModelForm) -> HttpResponse:
        instance = form.save(commit=False)
        user = self.request.user
        if self.is_create and hasattr(instance, "created_by"):
            instance.created_by = user
        if hasattr(instance, "updated_by"):
            instance.updated_by = user

        instance.save()
        form.save_m2m()
        self.object = instance
        self._sync_profile_role(instance)
        self._record_trace(instance)
        messages.success(self.request, f"{self.resource.singular_label} guardado.")
        return redirect(self.get_success_url())

    def get_success_url(self) -> str:
        instance = self.get_object()
        if instance is None:
            return reverse(self.resource.list_url_name)
        return reverse(self.resource.detail_url_name, kwargs={"pk": instance.pk})

    def _record_trace(self, instance: Model) -> None:
        event_type = (
            self.resource.create_event_type
            if self.is_create
            else self.resource.update_event_type
        )
        if not event_type:
            return
        record_event(
            event_type=event_type,
            object_label=str(instance),
            actor=cast(Model, self.request.user),
            payload={"model": instance._meta.label, "id": str(instance.pk)},
        )

    @staticmethod
    def _sync_profile_role(instance: Model) -> None:
        if isinstance(instance, OwnerProfile):
            instance.user.role = UserRole.OWNER
            instance.user.save(update_fields=["role"])
        elif isinstance(instance, TenantDoctorProfile):
            instance.user.role = UserRole.TENANT_DOCTOR
            instance.user.save(update_fields=["role"])


class CatalogDeactivateView(CatalogBaseMixin, TemplateView):
    template_name = "catalog/confirm_deactivate.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = self.add_resource_context(super().get_context_data(**kwargs))
        context["object"] = self.get_queryset().get(pk=self.kwargs["pk"])
        context["page_title"] = f"Desactivar {self.resource.singular_label.lower()}"
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        instance = self.get_queryset().get(pk=self.kwargs["pk"])
        instance.is_active = False
        instance.updated_by = request.user
        instance.save(update_fields=["is_active", "updated_by", "updated_at"])
        if self.resource.update_event_type:
            record_event(
                event_type=self.resource.update_event_type,
                object_label=str(instance),
                actor=cast(Model, request.user),
                payload={
                    "model": instance._meta.label,
                    "id": str(instance.pk),
                    "action": "deactivate",
                },
            )
        messages.success(request, f"{self.resource.singular_label} desactivado.")
        return redirect(self.resource.list_url_name)


class ClinicListView(CatalogListView):
    resource = CLINIC


class ClinicCreateView(CatalogFormView):
    resource = CLINIC
    is_create = True


class ClinicUpdateView(CatalogFormView):
    resource = CLINIC


class ClinicDetailView(CatalogDetailView):
    resource = CLINIC


class ClinicDeactivateView(CatalogDeactivateView):
    resource = CLINIC


class RoomListView(CatalogListView):
    resource = ROOM


class RoomCreateView(CatalogFormView):
    resource = ROOM
    is_create = True


class RoomUpdateView(CatalogFormView):
    resource = ROOM


class RoomDetailView(CatalogDetailView):
    resource = ROOM


class RoomDeactivateView(CatalogDeactivateView):
    resource = ROOM


class SpecialtyListView(CatalogListView):
    resource = SPECIALTY


class SpecialtyCreateView(CatalogFormView):
    resource = SPECIALTY
    is_create = True


class SpecialtyUpdateView(CatalogFormView):
    resource = SPECIALTY


class SpecialtyDetailView(CatalogDetailView):
    resource = SPECIALTY


class SpecialtyDeactivateView(CatalogDeactivateView):
    resource = SPECIALTY


class EquipmentListView(CatalogListView):
    resource = EQUIPMENT


class EquipmentCreateView(CatalogFormView):
    resource = EQUIPMENT
    is_create = True


class EquipmentUpdateView(CatalogFormView):
    resource = EQUIPMENT


class EquipmentDetailView(CatalogDetailView):
    resource = EQUIPMENT


class EquipmentDeactivateView(CatalogDeactivateView):
    resource = EQUIPMENT


class OwnerListView(CatalogListView):
    resource = OWNER


class OwnerCreateView(CatalogFormView):
    resource = OWNER
    is_create = True


class OwnerUpdateView(CatalogFormView):
    resource = OWNER


class OwnerDetailView(CatalogDetailView):
    resource = OWNER


class OwnerDeactivateView(CatalogDeactivateView):
    resource = OWNER


class TenantDoctorListView(CatalogListView):
    resource = TENANT_DOCTOR


class TenantDoctorCreateView(CatalogFormView):
    resource = TENANT_DOCTOR
    is_create = True


class TenantDoctorUpdateView(CatalogFormView):
    resource = TENANT_DOCTOR


class TenantDoctorDetailView(CatalogDetailView):
    resource = TENANT_DOCTOR


class TenantDoctorDeactivateView(CatalogDeactivateView):
    resource = TENANT_DOCTOR
