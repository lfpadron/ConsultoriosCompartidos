"""Custom user manager."""

from typing import Any, cast

from django.contrib.auth.base_user import BaseUserManager


class CustomUserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(
        self, email: str, password: str | None, **extra_fields: Any
    ) -> Any:
        if not email:
            msg = "El correo electrónico es obligatorio."
            raise ValueError(msg)

        normalized_email = self.normalize_email(email).lower()
        user = cast(Any, self.model(email=normalized_email, **extra_fields))
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(
        self, email: str, password: str | None = None, **extra_fields: Any
    ) -> Any:
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(
        self, email: str, password: str | None = None, **extra_fields: Any
    ) -> Any:
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "superadmin")

        if extra_fields.get("is_staff") is not True:
            msg = "El superusuario debe tener is_staff=True."
            raise ValueError(msg)
        if extra_fields.get("is_superuser") is not True:
            msg = "El superusuario debe tener is_superuser=True."
            raise ValueError(msg)

        return self._create_user(email, password, **extra_fields)
