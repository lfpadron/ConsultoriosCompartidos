from typing import Any

from django.contrib.auth import get_user_model


def test_custom_user_uses_email_as_username(db: Any) -> None:
    user_model = get_user_model()

    user = user_model.objects.create_user(
        email="medico@example.com",
        password="segura-123",
        first_name="Ada",
        last_name="Lovelace",
    )

    assert user.email == "medico@example.com"
    assert not hasattr(user, "username")
    assert user.USERNAME_FIELD == "email"
    assert user.check_password("segura-123")
    assert user.is_active is True


def test_login_uses_email(client: Any, db: Any) -> None:
    user_model = get_user_model()
    user_model.objects.create_user(
        email="admin@example.com",
        password="segura-123",
        first_name="Grace",
        last_name="Hopper",
    )

    response = client.post(
        "/login/",
        {"username": "admin@example.com", "password": "segura-123"},
    )

    assert response.status_code == 302
    assert response.url == "/"
