"""User management views."""

from typing import Any, cast

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Model, Q, QuerySet
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBase,
)
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, FormView, ListView, TemplateView
from django.views.generic.edit import FormMixin

from apps.astrotrace.services import record_event
from apps.core.permissions import can_edit_screen, get_user_roles
from apps.identity.forms import (
    ForcedPasswordChangeForm,
    ManagedUserFilterForm,
    ManagedUserForm,
    PermissionMatrixForm,
    ProfilePasswordChangeForm,
)
from apps.identity.models import ApplicationScreen, CustomUser, UserRole
from apps.identity.permission_service import update_permission_matrix
from apps.identity.services import send_user_invitation


class UserManagementPermissionMixin(LoginRequiredMixin):
    """Scope user management after screen access is enforced by middleware."""

    def get_queryset(self) -> QuerySet[CustomUser]:
        return scope_users_for_manager(
            CustomUser.objects.all()
            .prefetch_related(
                "assigned_clinics",
                "assigned_owners",
                "role_assignments",
            )
            .order_by("email"),
            cast(Any, self).request.user,
        )


class UserListView(UserManagementPermissionMixin, ListView):
    model = CustomUser
    template_name = "identity/user_list.html"
    context_object_name = "users"
    paginate_by = 25

    def get_queryset(self) -> QuerySet[CustomUser]:
        queryset = super().get_queryset()
        self.filter_form = ManagedUserFilterForm(self.request.GET or None)
        cleaned_data: dict[str, Any] = {}
        if self.filter_form.is_bound:
            self.filter_form.is_valid()
            cleaned_data = self.filter_form.cleaned_data

        self.search_query = (cleaned_data.get("q") or "").strip()
        role = cleaned_data.get("role")
        is_active = cleaned_data.get("is_active")

        if self.search_query:
            queryset = queryset.filter(
                Q(email__icontains=self.search_query)
                | Q(first_name__icontains=self.search_query)
                | Q(last_name__icontains=self.search_query)
                | Q(phone__icontains=self.search_query)
            )
        if role:
            queryset = queryset.filter(
                Q(role=role)
                | Q(
                    role_assignments__role=role,
                    role_assignments__is_active=True,
                    role_assignments__is_deleted=False,
                )
            )
        if is_active in {"0", "1"}:
            queryset = queryset.filter(is_active=is_active == "1")
        return queryset.distinct()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = "Usuarios"
        context["filter_form"] = self.filter_form
        return context


class UserDetailView(UserManagementPermissionMixin, DetailView):
    model = CustomUser
    template_name = "identity/user_detail.html"
    context_object_name = "managed_user"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        user = context["managed_user"]
        context["page_title"] = "Detalle de usuario"
        context["assigned_clinics"] = user.assigned_clinics.filter(is_deleted=False)
        context["assigned_owners"] = user.assigned_owners.filter(is_deleted=False)
        context["role_labels"] = [
            dict(UserRole.choices)[role] for role in sorted(user.get_role_values())
        ]
        context["owner_profile"] = getattr(user, "owner_profile", None)
        context["tenant_doctor_profile"] = getattr(user, "tenant_doctor_profile", None)
        return context


class UserFormView(UserManagementPermissionMixin, FormMixin, TemplateView):
    template_name = "identity/user_form.html"
    form_class = ManagedUserForm
    object: CustomUser | None = None
    is_create = False

    def get_object(self) -> CustomUser | None:
        if self.object is not None:
            return self.object
        pk = self.kwargs.get("pk")
        if pk is None:
            return None
        self.object = self.get_queryset().get(pk=pk)
        return self.object

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["current_user"] = self.request.user
        instance = self.get_object()
        if instance is not None:
            kwargs["instance"] = instance
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["page_title"] = (
            "Alta de usuario" if self.is_create else "Editar usuario"
        )
        context["managed_user"] = self.get_object()
        context["is_create"] = self.is_create
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form: ManagedUserForm) -> HttpResponse:
        user = form.save()
        self.object = user
        event_type = (
            "identity.user_created" if self.is_create else "identity.user_updated"
        )
        record_event(
            event_type=event_type,
            object_label=user.email,
            actor=cast(Model, self.request.user),
            payload={
                "user_id": str(user.pk),
                "primary_role": user.role,
                "roles": sorted(user.get_role_values()),
            },
        )

        temporary_password = form.cleaned_data.get("temporary_password") or ""
        if form.cleaned_data.get("send_invitation"):
            send_user_invitation(
                user=user,
                actor=cast(Model, self.request.user),
                request=self.request,
                temporary_password=temporary_password,
            )
            messages.success(self.request, "Usuario guardado e invitación enviada.")
        else:
            messages.success(self.request, "Usuario guardado.")
        return redirect(self.get_success_url())

    def get_success_url(self) -> str:
        user = self.get_object()
        if user is None:
            return reverse("users")
        return reverse("user_detail", kwargs={"pk": user.pk})


class UserCreateView(UserFormView):
    is_create = True


class UserUpdateView(UserFormView):
    pass


class UserDeactivateView(UserManagementPermissionMixin, TemplateView):
    template_name = "identity/user_deactivate.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["managed_user"] = self.get_queryset().get(pk=self.kwargs["pk"])
        context["page_title"] = "Desactivar usuario"
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        user = self.get_queryset().get(pk=self.kwargs["pk"])
        if user.pk == request.user.pk:
            messages.error(request, "No puedes desactivar tu propio usuario.")
            return redirect("user_detail", pk=user.pk)
        user.is_active = False
        user.save(update_fields=["is_active"])
        record_event(
            event_type="identity.user_deactivated",
            object_label=user.email,
            actor=cast(Model, request.user),
            payload={"user_id": str(user.pk), "role": user.role},
        )
        messages.success(request, "Usuario desactivado.")
        return redirect("users")


class UserSendInvitationView(UserManagementPermissionMixin, View):
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        user = self.get_queryset().get(pk=self.kwargs["pk"])
        send_user_invitation(
            user=user, actor=cast(Model, request.user), request=request
        )
        messages.success(request, "Invitación enviada.")
        return redirect("user_detail", pk=user.pk)


class ForcedPasswordChangeView(LoginRequiredMixin, FormView):
    template_name = "identity/force_password_change.html"
    form_class = ForcedPasswordChangeForm

    def dispatch(
        self,
        request: HttpRequest,
        *args: Any,
        **kwargs: Any,
    ) -> HttpResponseBase:
        if not getattr(request.user, "must_change_password", False):
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form: ForcedPasswordChangeForm) -> HttpResponse:
        user = form.save()
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])
        update_session_auth_hash(self.request, user)
        record_event(
            event_type="identity.password_changed",
            object_label=user.email,
            actor=cast(Model, user),
            payload={"forced": True},
        )
        messages.success(self.request, "Contraseña actualizada.")
        return redirect("dashboard")


class ProfileView(LoginRequiredMixin, FormView):
    template_name = "identity/profile.html"
    form_class = ProfilePasswordChangeForm

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        profile_user = cast(CustomUser, self.request.user)
        context["page_title"] = "Perfil"
        context["profile_user"] = profile_user
        context["role_labels"] = [
            dict(UserRole.choices)[role]
            for role in sorted(profile_user.get_role_values())
        ]
        return context

    def form_valid(self, form: ProfilePasswordChangeForm) -> HttpResponse:
        user = form.save()
        update_session_auth_hash(self.request, user)
        record_event(
            event_type="identity.profile.password_changed",
            object_label=user.email,
            actor=cast(Model, user),
            payload={"user_id": str(user.pk), "role": user.role, "field": "password"},
        )
        messages.success(self.request, "Contraseña actualizada.")
        return redirect("profile")


def scope_users_for_manager(
    queryset: QuerySet[CustomUser],
    manager: Any,
) -> QuerySet[CustomUser]:
    roles = get_user_roles(manager)
    if UserRole.SUPERADMIN in roles:
        return queryset
    if UserRole.ADMIN in roles:
        scoped = queryset.exclude(
            Q(role=UserRole.SUPERADMIN)
            | Q(
                role_assignments__role=UserRole.SUPERADMIN,
                role_assignments__is_active=True,
                role_assignments__is_deleted=False,
            )
        )
        clinics = manager.assigned_clinics.filter(is_deleted=False)
        if not clinics.exists():
            return scoped
        return scoped.filter(
            Q(pk=manager.pk)
            | Q(assigned_clinics__in=clinics)
            | Q(owner_profile__consulting_rooms__clinic__in=clinics)
            | Q(tenant_doctor_profile__assigned_rooms__clinic__in=clinics)
            | Q(assigned_owners__consulting_rooms__clinic__in=clinics)
        ).distinct()
    if UserRole.OWNER in roles:
        owner = getattr(manager, "owner_profile", None)
        if owner is None:
            return queryset.filter(pk=manager.pk)
        return queryset.filter(
            Q(pk=manager.pk)
            | (
                Q(assigned_owners=owner)
                & (
                    Q(role=UserRole.ASSISTANT)
                    | Q(
                        role_assignments__role=UserRole.ASSISTANT,
                        role_assignments__is_active=True,
                        role_assignments__is_deleted=False,
                    )
                )
            )
        ).distinct()
    return queryset.filter(pk=manager.pk)


class PermissionMatrixView(LoginRequiredMixin, FormMixin, TemplateView):
    template_name = "identity/permission_matrix.html"
    form_class = PermissionMatrixForm

    def get_screens(self) -> QuerySet[ApplicationScreen]:
        return ApplicationScreen.objects.filter(
            is_active=True,
            is_deleted=False,
        ).order_by("sort_order", "label")

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["screens"] = self.get_screens()
        kwargs["can_edit"] = can_edit_screen(self.request.user, "permissions")
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        form = context["form"]
        rows = []
        for screen in form.screens:
            cells = []
            for role, role_label in UserRole.choices:
                cells.append(
                    {
                        "role": role,
                        "role_label": role_label,
                        "field": form[form.field_name(role, screen.key)],
                        "is_protected": (
                            role == UserRole.SUPERADMIN
                            and screen.key == "permissions"
                        ),
                    }
                )
            rows.append({"screen": screen, "cells": cells})
        context.update(
            {
                "page_title": "Gestión de permisos",
                "roles": UserRole.choices,
                "matrix_rows": rows,
                "can_edit_permissions": can_edit_screen(
                    self.request.user,
                    "permissions",
                ),
            }
        )
        return context

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form: PermissionMatrixForm) -> HttpResponse:
        levels = {
            (role, screen.key): form.access_level_for(role, screen.key)
            for screen in form.screens
            for role, _label in UserRole.choices
        }
        updated = update_permission_matrix(
            levels=levels,
            actor=cast(Model, self.request.user),
        )
        messages.success(
            self.request,
            f"Permisos actualizados: {updated} cambio(s).",
        )
        return redirect("permissions")
