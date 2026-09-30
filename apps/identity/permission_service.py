"""Permission matrix mutation services."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Model

from apps.astrotrace.services import record_event
from apps.identity.models import (
    ApplicationScreen,
    RoleScreenPermission,
    ScreenAccessLevel,
    UserRole,
)


@transaction.atomic
def update_permission_matrix(
    *,
    levels: dict[tuple[str, str], str],
    actor: Model,
) -> int:
    """Update every submitted matrix cell and trace effective changes."""

    screens = {
        screen.key: screen
        for screen in ApplicationScreen.objects.filter(
            is_active=True,
            is_deleted=False,
        )
    }
    updated = 0
    for (role, screen_key), requested_level in levels.items():
        screen = screens.get(screen_key)
        if screen is None:
            continue
        level = _protected_level(role, screen_key, requested_level)
        permission, created = RoleScreenPermission.objects.get_or_create(
            role=role,
            screen=screen,
            defaults={
                "access_level": level,
                "created_by": actor,
                "updated_by": actor,
            },
        )
        previous_level = None if created else permission.access_level
        if not created and previous_level == level and permission.is_active:
            continue
        permission.access_level = level
        permission.is_active = True
        permission.is_deleted = False
        permission.updated_by = actor  # type: ignore[assignment]
        permission.save()
        updated += 1
        record_event(
            event_type="identity.screen_permission.updated",
            object_label=f"{permission.get_role_display()} - {screen.label}",
            actor=actor,
            payload={
                "model": permission._meta.label,
                "id": str(permission.pk),
                "level": "legal",
                "role": role,
                "screen_key": screen.key,
                "screen": screen.label,
                "previous_access_level": previous_level or "",
                "access_level": level,
            },
        )
    return updated


def _protected_level(role: str, screen_key: str, requested_level: str) -> str:
    if role == UserRole.SUPERADMIN and screen_key == "permissions":
        if requested_level != ScreenAccessLevel.EDIT:
            raise ValidationError(
                "El administrador de sistemas debe conservar edición en Permisos."
            )
        return ScreenAccessLevel.EDIT
    return requested_level
