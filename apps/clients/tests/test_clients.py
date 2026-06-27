import pytest
from rest_framework.test import APIClient

from apps.clients.models import Client
from apps.users.models import User


@pytest.fixture
def auth_client(db):
    user = User.objects.create_user(username="tester", password="testpass123")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def test_duplicate_phone_is_rejected(auth_client):
    Client.objects.create(name="Marco Alca", phone_number="983266763")
    response = auth_client.post(
        "/api/clients/",
        {"name": "Otro Cliente", "phone_number": "983266763"},
        format="json",
    )
    assert response.status_code == 400


def test_search_is_partial_and_case_insensitive(auth_client):
    Client.objects.create(name="Marco Alca", phone_number="983266763")
    Client.objects.create(name="Lucia Perez", phone_number="912345678")
    response = auth_client.get("/api/clients/?search=mar")
    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["name"] == "Marco Alca"
