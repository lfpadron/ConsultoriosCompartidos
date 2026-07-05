from typing import Any

import pytest

from tests.test_reservations import create_user


@pytest.mark.django_db
def test_dashboard_responds(client: Any) -> None:
    user = create_user("dashboard-view@example.com")
    client.force_login(user)

    response = client.get("/")
    content = response.content.decode()

    assert response.status_code == 200
    assert "Dashboard Ejecutivo" in content
    assert "UNA MISIÓN EN PROGRESO DE:" in content
    assert "astrogato-texto-derecha-centrado.png" in content
    assert 'id="footer-clock"' in content
    assert "--:--:--" in content


@pytest.mark.django_db
def test_construction_page_requires_login_and_responds(client: Any) -> None:
    response = client.get("/administracion/")
    assert response.status_code == 302

    user = create_user("construction-view@example.com")
    client.force_login(user)
    response = client.get("/administracion/")
    assert response.status_code == 200
    assert "En construcción" in response.content.decode()


def test_login_page_responds(client: Any) -> None:
    response = client.get("/login/")

    assert response.status_code == 200
    assert "Correo electrónico" in response.content.decode()
