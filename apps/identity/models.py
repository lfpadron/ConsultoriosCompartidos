"""Identity models."""

import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

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
