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
    HttpResponseForbidden,
)
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, FormView, ListView, TemplateView
from django.views.generic.edit import FormMixin

from apps.astrotrace.services import record_event
from apps.identity.forms import (
    ForcedPasswordChangeForm,
    ManagedUserFilterForm,
    ManagedUserForm,
)
from apps.identity.models import CustomUser, UserRole
from apps.identity.services import send_user_invitation

MANAGER_ROLES = {UserRole.SUPERADMIN, UserRole.ADMIN, UserRole.OWNER}


class UserManagementPermissionMixin(LoginRequiredMixin):
    """Allow only operational user managers into the user CRUD."""

    def dispatch(
        self,
        request: HttpRequest,
        *args: Any,
        **kwargs: Any,
    ) -> HttpResponseBase:
        if getattr(request.user, "role", "") not in MANAGER_ROLES:
            return HttpResponseForbidden("No tienes permiso para administrar usuarios.")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self) -> QuerySet[CustomUser]:
        return scope_users_for_manager(
            CustomUser.objects.all()
            .prefetch_related("assigned_clinics", "assigned_owners")
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
            queryset = queryset.filter(role=role)
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
            payload={"user_id": str(user.pk), "role": user.role},
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


def scope_users_for_manager(
    queryset: QuerySet[CustomUser],
    manager: Any,
) -> QuerySet[CustomUser]:
    role = getattr(manager, "role", "")
    if role == UserRole.SUPERADMIN:
        return queryset
    if role == UserRole.ADMIN:
        scoped = queryset.exclude(role=UserRole.SUPERADMIN)
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
    if role == UserRole.OWNER:
        owner = getattr(manager, "owner_profile", None)
        if owner is None:
            return queryset.filter(pk=manager.pk)
        return queryset.filter(
            Q(pk=manager.pk) | Q(role=UserRole.ASSISTANT, assigned_owners=owner)
        ).distinct()
    return queryset.none()
