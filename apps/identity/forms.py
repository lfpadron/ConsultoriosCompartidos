"""Authentication and user management forms."""

from typing import Any, cast

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.db.models import Q, QuerySet
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Clinic, OwnerProfile
from apps.core.form_utils import style_form_fields
from apps.identity.models import (
    ApplicationScreen,
    RoleScreenPermission,
    ScreenAccessLevel,
    UserRole,
)
from apps.identity.services import sync_user_roles


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(
        label=_("Correo electrónico"),
        widget=forms.EmailInput(
            attrs={
                "autofocus": True,
                "class": "form-control",
                "autocomplete": "email",
            }
        ),
    )
    password = forms.CharField(
        label=_("Contraseña"),
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "autocomplete": "current-password",
            }
        ),
    )


class ManagedUserFilterForm(forms.Form):
    q = forms.CharField(label="Buscar", required=False)
    role = forms.ChoiceField(
        label="Grupo",
        choices=(("", "Todos"), *UserRole.choices),
        required=False,
    )
    is_active = forms.ChoiceField(
        label="Estado",
        choices=(("", "Todos"), ("1", "Activos"), ("0", "Inactivos")),
        required=False,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        style_form_fields(self.fields)


class ManagedUserForm(forms.ModelForm):
    additional_roles = forms.MultipleChoiceField(
        label=_("Roles adicionales"),
        choices=UserRole.choices,
        required=False,
        help_text=_(
            "Permite que una misma persona opere, por ejemplo, como propietaria "
            "y médica arrendataria."
        ),
    )
    temporary_password = forms.CharField(
        label=_("Contraseña temporal"),
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=_(
            "Se guardará como contraseña inicial y el usuario deberá cambiarla "
            "al entrar."
        ),
    )
    send_invitation = forms.BooleanField(
        label=_("Enviar invitación por correo"),
        required=False,
        initial=True,
    )

    class Meta:
        model = get_user_model()
        fields = (
            "email",
            "first_name",
            "last_name",
            "phone",
            "secondary_email",
            "secondary_phone",
            "role",
            "additional_roles",
            "assigned_clinics",
            "assigned_owners",
            "temporary_password",
            "must_change_password",
            "send_invitation",
            "is_active",
        )
        labels = {
            "role": _("Rol principal"),
            "assigned_clinics": _("Clínicas asignadas"),
            "assigned_owners": _("Médicos propietarios asignados"),
        }

    def __init__(
        self,
        *args: Any,
        current_user: Any,
        **kwargs: Any,
    ) -> None:
        self.current_user = current_user
        super().__init__(*args, **kwargs)
        self.is_create = self.instance.pk is None
        if self.is_create:
            self.fields["temporary_password"].required = True
            self.fields["must_change_password"].initial = True
        role_field = cast(forms.ChoiceField, self.fields["role"])
        additional_roles_field = cast(
            forms.MultipleChoiceField,
            self.fields["additional_roles"],
        )
        clinics_field = cast(
            forms.ModelMultipleChoiceField,
            self.fields["assigned_clinics"],
        )
        owners_field = cast(
            forms.ModelMultipleChoiceField,
            self.fields["assigned_owners"],
        )
        role_field.choices = self._role_choices_for_current_user()
        additional_roles_field.choices = self._role_choices_for_current_user()
        if self.instance.pk:
            additional_roles_field.initial = sorted(
                self.instance.get_role_values() - {self.instance.role}
            )
        clinics_field.queryset = self._clinic_queryset()
        owners_field.queryset = self._owner_queryset()
        clinics_field.required = False
        owners_field.required = False
        style_form_fields(self.fields)

    def clean_email(self) -> str:
        email = get_user_model().objects.normalize_email(self.cleaned_data["email"])
        return email.lower()

    def clean_secondary_email(self) -> str:
        email = self.cleaned_data.get("secondary_email", "")
        if not email:
            return ""
        return get_user_model().objects.normalize_email(email).lower()

    def clean_role(self) -> str:
        role = self.cleaned_data["role"]
        allowed_roles = {
            value for value, _label in self._role_choices_for_current_user()
        }
        if role not in allowed_roles:
            raise forms.ValidationError(_("No puedes asignar ese grupo de usuario."))
        return role

    def clean_additional_roles(self) -> list[str]:
        roles = set(self.cleaned_data.get("additional_roles") or [])
        allowed_roles = {
            value for value, _label in self._role_choices_for_current_user()
        }
        invalid_roles = roles - allowed_roles
        if invalid_roles:
            raise forms.ValidationError(_("No puedes asignar uno de esos roles."))
        primary_role = self.cleaned_data.get("role")
        roles.discard(primary_role)
        return sorted(roles)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        role = cleaned_data.get("role")
        all_roles = {role, *(cleaned_data.get("additional_roles") or [])}
        all_roles.discard(None)
        clinics = cleaned_data.get("assigned_clinics")
        owners = cleaned_data.get("assigned_owners")

        if UserRole.ADMIN in all_roles and not clinics:
            self.add_error(
                "assigned_clinics",
                _("Un administrador de negocio debe estar asignado a una clínica."),
            )
        if UserRole.ASSISTANT in all_roles and not owners:
            self.add_error(
                "assigned_owners",
                _("Un asistente administrativo debe estar asignado a un propietario."),
            )
        if UserRole.SUPERADMIN in all_roles:
            superadmins = get_user_model().objects.filter(is_active=True).filter(
                Q(role=UserRole.SUPERADMIN)
                | Q(
                    role_assignments__role=UserRole.SUPERADMIN,
                    role_assignments__is_active=True,
                    role_assignments__is_deleted=False,
                )
            )
            if self.instance.pk:
                superadmins = superadmins.exclude(pk=self.instance.pk)
            if superadmins.distinct().count() >= 3:
                self.add_error(
                    "role",
                    _(
                        "Solo puede haber tres administradores de sistemas "
                        "activos a la vez."
                    ),
                )
        return cleaned_data

    def save(self, commit: bool = True) -> Any:
        user = super().save(commit=False)
        role = self.cleaned_data["role"]
        all_roles = {role, *(self.cleaned_data.get("additional_roles") or [])}
        temporary_password = self.cleaned_data.get("temporary_password")
        user.is_staff = UserRole.SUPERADMIN in all_roles
        user.is_superuser = UserRole.SUPERADMIN in all_roles
        if temporary_password:
            user.set_password(temporary_password)
            user.must_change_password = True
        if commit:
            user.save()
            self.save_m2m()
            sync_user_roles(
                user=user,
                roles=all_roles,
                actor=self.current_user,
            )
        return user

    def _role_choices_for_current_user(self) -> tuple[tuple[str, Any], ...]:
        current_roles = self.current_user.get_role_values()
        if UserRole.SUPERADMIN in current_roles:
            allowed_roles = {
                UserRole.SUPERADMIN,
                UserRole.ADMIN,
                UserRole.OWNER,
                UserRole.TENANT_DOCTOR,
                UserRole.ASSISTANT,
                UserRole.OPERATOR,
                UserRole.RECEPTIONIST,
                UserRole.AUDITOR,
            }
        elif UserRole.ADMIN in current_roles:
            allowed_roles = {
                UserRole.ADMIN,
                UserRole.OWNER,
                UserRole.TENANT_DOCTOR,
                UserRole.ASSISTANT,
                UserRole.OPERATOR,
                UserRole.RECEPTIONIST,
                UserRole.AUDITOR,
            }
        elif UserRole.OWNER in current_roles:
            allowed_roles = {UserRole.ASSISTANT}
        else:
            allowed_roles = set()
        return tuple(
            choice for choice in UserRole.choices if choice[0] in allowed_roles
        )

    def _clinic_queryset(self) -> QuerySet[Clinic]:
        queryset = Clinic.objects.filter(is_deleted=False).order_by("name")
        if self.current_user.has_role(UserRole.ADMIN):
            assigned = self.current_user.assigned_clinics.filter(is_deleted=False)
            if assigned.exists():
                queryset = queryset.filter(pk__in=assigned.values("pk"))
        return queryset

    def _owner_queryset(self) -> QuerySet[OwnerProfile]:
        queryset = OwnerProfile.objects.filter(is_deleted=False).select_related("user")
        current_roles = self.current_user.get_role_values()
        if UserRole.OWNER in current_roles:
            owner = getattr(self.current_user, "owner_profile", None)
            if owner is not None:
                queryset = queryset.filter(pk=owner.pk)
            else:
                queryset = queryset.none()
        elif UserRole.ADMIN in current_roles:
            assigned = self.current_user.assigned_clinics.filter(is_deleted=False)
            if assigned.exists():
                queryset = queryset.filter(consulting_rooms__clinic__in=assigned)
        return queryset.order_by("display_name", "user__email").distinct()


class ForcedPasswordChangeForm(forms.Form):
    new_password1 = forms.CharField(
        label=_("Nueva contraseña"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=password_validation.password_validators_help_text_html(),
    )
    new_password2 = forms.CharField(
        label=_("Confirmar nueva contraseña"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, user: Any, *args: Any, **kwargs: Any) -> None:
        self.user = user
        super().__init__(*args, **kwargs)
        style_form_fields(self.fields)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        password1 = cleaned_data.get("new_password1")
        password2 = cleaned_data.get("new_password2")
        if password1 and password2 and password1 != password2:
            self.add_error("new_password2", _("Las contraseñas no coinciden."))
        if password2:
            password_validation.validate_password(password2, self.user)
        return cleaned_data

    def save(self) -> Any:
        self.user.set_password(self.cleaned_data["new_password1"])
        self.user.save()
        return self.user


class ProfilePasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label=_("Contraseña actual"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    new_password1 = forms.CharField(
        label=_("Nueva contraseña"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=password_validation.password_validators_help_text_html(),
    )
    new_password2 = forms.CharField(
        label=_("Confirmar nueva contraseña"),
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, user: Any, *args: Any, **kwargs: Any) -> None:
        super().__init__(user, *args, **kwargs)
        style_form_fields(self.fields)


class PermissionMatrixForm(forms.Form):
    """Dynamic role-by-screen permission matrix."""

    def __init__(
        self,
        *args: Any,
        screens: QuerySet[ApplicationScreen],
        can_edit: bool,
        **kwargs: Any,
    ) -> None:
        self.screens = list(screens)
        self.can_edit = can_edit
        super().__init__(*args, **kwargs)
        permissions = {
            (permission.role, permission.screen_id): permission.access_level
            for permission in RoleScreenPermission.objects.filter(
                screen__in=self.screens,
                is_deleted=False,
            )
        }
        for screen in self.screens:
            for role, _label in UserRole.choices:
                field_name = self.field_name(role, screen.key)
                is_protected = (
                    role == UserRole.SUPERADMIN and screen.key == "permissions"
                )
                initial = permissions.get(
                    (role, screen.pk),
                    ScreenAccessLevel.EDIT if is_protected else ScreenAccessLevel.NONE,
                )
                self.fields[field_name] = forms.ChoiceField(
                    label=f"{screen.label} - {dict(UserRole.choices)[role]}",
                    choices=ScreenAccessLevel.choices,
                    initial=initial,
                    disabled=not can_edit or is_protected,
                    widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
                )

    @staticmethod
    def field_name(role: str, screen_key: str) -> str:
        return f"permission__{role}__{screen_key}"

    def access_level_for(self, role: str, screen_key: str) -> str:
        return self.cleaned_data[self.field_name(role, screen_key)]
