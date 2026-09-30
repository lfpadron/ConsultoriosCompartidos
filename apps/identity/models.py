"""Identity models."""

import uuid
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel
from apps.identity.managers import CustomUserManager


class UserRole(models.TextChoices):
    SUPERADMIN = "superadmin", _("Administrador de sistemas")
    ADMIN = "admin", _("Administrador de negocio")
    OPERATOR = "operator", _("Operador")
    RECEPTIONIST = "receptionist", _("Recepcionista")
    OWNER = "owner", _("Médico propietario")
    TENANT_DOCTOR = "tenant_doctor", _("Médico arrendatario")
    ASSISTANT = "assistant", _("Asistente administrativo")
    AUDITOR = "auditor", _("Auditor")


class ScreenAccessLevel(models.TextChoices):
    NONE = "none", _("Sin acceso")
    READ = "read", _("Lectura")
    EDIT = "edit", _("Edición")


class CustomUser(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(_("correo electrónico"), unique=True)
    first_name = models.CharField(_("nombre"), max_length=150)
    last_name = models.CharField(_("apellidos"), max_length=150)
    phone = models.CharField(_("teléfono"), max_length=40, blank=True)
    secondary_email = models.EmailField(_("correo alterno"), blank=True)
    secondary_phone = models.CharField(_("teléfono alterno"), max_length=40, blank=True)
    role = models.CharField(
        _("rol"),
        max_length=32,
        choices=UserRole.choices,
        default=UserRole.TENANT_DOCTOR,
    )
    assigned_clinics = models.ManyToManyField(
        "catalog.Clinic",
        blank=True,
        related_name="assigned_users",
        verbose_name=_("clínicas asignadas"),
    )
    assigned_owners = models.ManyToManyField(
        "catalog.OwnerProfile",
        blank=True,
        related_name="assistant_users",
        verbose_name=_("propietarios asignados"),
    )
    must_change_password = models.BooleanField(
        _("forzar cambio de contraseña"),
        default=False,
    )
    invitation_sent_at = models.DateTimeField(
        _("invitación enviada en"),
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(_("activo"), default=True)
    is_staff = models.BooleanField(_("staff"), default=False)
    date_joined = models.DateTimeField(_("fecha de registro"), default=timezone.now)

    objects = CustomUserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = _("usuario")
        verbose_name_plural = _("usuarios")
        ordering = ("email",)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def clean(self) -> None:
        super().clean()
        self.email = type(self).objects.normalize_email(self.email).lower()
        if self.secondary_email:
            self.secondary_email = (
                type(self).objects.normalize_email(self.secondary_email).lower()
            )
        if self.role == UserRole.SUPERADMIN and self.is_active:
            active_superadmins = type(self).objects.filter(
                role=UserRole.SUPERADMIN,
                is_active=True,
            )
            if self.pk:
                active_superadmins = active_superadmins.exclude(pk=self.pk)
            if active_superadmins.count() >= 3:
                raise ValidationError(
                    {
                        "role": _(
                            "Solo puede haber tres administradores de sistema "
                            "activos a la vez."
                        )
                    }
                )

    def __str__(self) -> str:
        return self.email

    def get_role_values(self) -> set[str]:
        roles = {self.role}
        if self.pk:
            roles.update(
                self.role_assignments.filter(
                    is_active=True,
                    is_deleted=False,
                ).values_list("role", flat=True)
            )
        return roles

    def has_role(self, role: str) -> bool:
        return role in self.get_role_values()

    @property
    def role_display_names(self) -> list[str]:
        labels = dict(UserRole.choices)
        return [str(labels[role]) for role in sorted(self.get_role_values())]


class UserRoleAssignment(BaseModel):
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="role_assignments",
        verbose_name=_("usuario"),
    )
    role = models.CharField(
        _("rol"),
        max_length=32,
        choices=UserRole.choices,
    )

    class Meta:
        verbose_name = _("rol asignado")
        verbose_name_plural = _("roles asignados")
        ordering = ("user__email", "role")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "role"),
                name="identity_unique_user_role_assignment",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} - {self.get_role_display()}"


class ApplicationScreen(BaseModel):
    key = models.SlugField(_("clave"), max_length=80, unique=True)
    label = models.CharField(_("nombre"), max_length=120)
    url_name = models.CharField(_("nombre de ruta"), max_length=120, unique=True)
    icon = models.CharField(_("icono"), max_length=80, blank=True)
    sort_order = models.PositiveSmallIntegerField(_("orden"), default=0)

    class Meta:
        verbose_name = _("pantalla")
        verbose_name_plural = _("pantallas")
        ordering = ("sort_order", "label")

    def __str__(self) -> str:
        return self.label


class RoleScreenPermission(BaseModel):
    screen = models.ForeignKey(
        ApplicationScreen,
        on_delete=models.PROTECT,
        related_name="role_permissions",
        verbose_name=_("pantalla"),
    )
    role = models.CharField(
        _("rol"),
        max_length=32,
        choices=UserRole.choices,
    )
    access_level = models.CharField(
        _("nivel de acceso"),
        max_length=12,
        choices=ScreenAccessLevel.choices,
        default=ScreenAccessLevel.NONE,
    )

    class Meta:
        verbose_name = _("permiso de pantalla")
        verbose_name_plural = _("permisos de pantalla")
        ordering = ("screen__sort_order", "role")
        constraints = [
            models.UniqueConstraint(
                fields=("screen", "role"),
                name="identity_unique_role_screen_permission",
            )
        ]

    @property
    def is_protected_permission(self) -> bool:
        return self.role == UserRole.SUPERADMIN and self.screen.key == "permissions"

    def clean(self) -> None:
        super().clean()
        if (
            self.is_protected_permission
            and self.access_level != ScreenAccessLevel.EDIT
        ):
            raise ValidationError(
                {
                    "access_level": _(
                        "El administrador de sistemas debe conservar acceso de "
                        "edición a la gestión de permisos."
                    )
                }
            )

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        if self.is_protected_permission:
            raise ValidationError(
                _(
                    "No se puede eliminar el permiso protegido del administrador "
                    "de sistemas."
                )
            )
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return (
            f"{self.get_role_display()} - {self.screen}: "
            f"{self.get_access_level_display()}"
        )
