"""Authentication and user management forms."""

from typing import Any, cast

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Clinic, OwnerProfile
from apps.core.form_utils import style_form_fields
from apps.identity.models import UserRole


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
            "assigned_clinics",
            "assigned_owners",
            "temporary_password",
            "must_change_password",
            "send_invitation",
            "is_active",
        )
        labels = {
            "role": _("Grupo"),
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
        clinics_field = cast(
            forms.ModelMultipleChoiceField,
            self.fields["assigned_clinics"],
        )
        owners_field = cast(
            forms.ModelMultipleChoiceField,
            self.fields["assigned_owners"],
        )
        role_field.choices = self._role_choices_for_current_user()
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

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        role = cleaned_data.get("role")
        clinics = cleaned_data.get("assigned_clinics")
        owners = cleaned_data.get("assigned_owners")

        if role == UserRole.ADMIN and not clinics:
            self.add_error(
                "assigned_clinics",
                _("Un administrador de negocio debe estar asignado a una clínica."),
            )
        if role == UserRole.ASSISTANT and not owners:
            self.add_error(
                "assigned_owners",
                _("Un asistente administrativo debe estar asignado a un propietario."),
            )
        return cleaned_data

    def save(self, commit: bool = True) -> Any:
        user = super().save(commit=False)
        role = self.cleaned_data["role"]
        temporary_password = self.cleaned_data.get("temporary_password")
        user.is_staff = role == UserRole.SUPERADMIN
        user.is_superuser = role == UserRole.SUPERADMIN
        if temporary_password:
            user.set_password(temporary_password)
            user.must_change_password = True
        if commit:
            user.save()
            self.save_m2m()
        return user

    def _role_choices_for_current_user(self) -> tuple[tuple[str, Any], ...]:
        current_role = getattr(self.current_user, "role", "")
        if current_role == UserRole.SUPERADMIN:
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
        elif current_role == UserRole.ADMIN:
            allowed_roles = {
                UserRole.ADMIN,
                UserRole.OWNER,
                UserRole.TENANT_DOCTOR,
                UserRole.ASSISTANT,
                UserRole.OPERATOR,
                UserRole.RECEPTIONIST,
                UserRole.AUDITOR,
            }
        elif current_role == UserRole.OWNER:
            allowed_roles = {UserRole.ASSISTANT}
        else:
            allowed_roles = set()
        return tuple(
            choice for choice in UserRole.choices if choice[0] in allowed_roles
        )

    def _clinic_queryset(self) -> QuerySet[Clinic]:
        queryset = Clinic.objects.filter(is_deleted=False).order_by("name")
        if getattr(self.current_user, "role", "") == UserRole.ADMIN:
            assigned = self.current_user.assigned_clinics.filter(is_deleted=False)
            if assigned.exists():
                queryset = queryset.filter(pk__in=assigned.values("pk"))
        return queryset

    def _owner_queryset(self) -> QuerySet[OwnerProfile]:
        queryset = OwnerProfile.objects.filter(is_deleted=False).select_related("user")
        current_role = getattr(self.current_user, "role", "")
        if current_role == UserRole.OWNER:
            owner = getattr(self.current_user, "owner_profile", None)
            if owner is not None:
                queryset = queryset.filter(pk=owner.pk)
            else:
                queryset = queryset.none()
        elif current_role == UserRole.ADMIN:
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
