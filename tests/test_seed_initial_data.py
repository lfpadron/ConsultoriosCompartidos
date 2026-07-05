from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.identity.models import UserRole


@pytest.mark.django_db
def test_seed_initial_data_creates_superadmin(monkeypatch: Any) -> None:
    monkeypatch.setenv("ADMIN_EMAIL", "root@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "segura-123")
    monkeypatch.setenv("ADMIN_FIRST_NAME", "Root")
    monkeypatch.setenv("ADMIN_LAST_NAME", "Admin")

    call_command("seed_initial_data")

    user = get_user_model().objects.get(email="root@example.com")
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.role == UserRole.SUPERADMIN
    assert user.check_password("segura-123")


@pytest.mark.django_db
def test_seed_initial_data_requires_env(monkeypatch: Any) -> None:
    for name in (
        "ADMIN_EMAIL",
        "ADMIN_PASSWORD",
        "ADMIN_FIRST_NAME",
        "ADMIN_LAST_NAME",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(CommandError):
        call_command("seed_initial_data")
