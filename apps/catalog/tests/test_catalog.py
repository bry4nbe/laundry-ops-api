import pytest
from django.db import IntegrityError
from rest_framework.test import APIClient

from apps.catalog.models import CatalogItem, ServiceType
from apps.users.models import User


@pytest.fixture
def auth_client(db):
    user = User.objects.create_user(username="tester", password="testpass123")
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def test_list_requires_authentication():
    api = APIClient()
    response = api.get("/api/catalog/items/")
    assert response.status_code == 401


def test_list_is_ordered_by_name(auth_client):
    CatalogItem.objects.create(
        name="Zapato", service_type=ServiceType.PER_GARMENT, base_price="10.00"
    )
    CatalogItem.objects.create(
        name="Camisa", service_type=ServiceType.PER_GARMENT, base_price="5.00"
    )
    response = auth_client.get("/api/catalog/items/")
    assert response.status_code == 200
    names = [item["name"] for item in response.data]
    assert names == ["Camisa", "Zapato"]


def test_is_active_filter_excludes_inactive(auth_client):
    CatalogItem.objects.create(
        name="Camisa",
        service_type=ServiceType.PER_GARMENT,
        base_price="5.00",
        is_active=True,
    )
    CatalogItem.objects.create(
        name="Pantalon",
        service_type=ServiceType.PER_GARMENT,
        base_price="8.00",
        is_active=False,
    )
    response = auth_client.get("/api/catalog/items/?is_active=true")
    assert response.status_code == 200
    names = [item["name"] for item in response.data]
    assert names == ["Camisa"]


def test_duplicate_name_raises_integrity_error(db):
    CatalogItem.objects.create(
        name="Camisa", service_type=ServiceType.PER_GARMENT, base_price="5.00"
    )
    with pytest.raises(IntegrityError):
        CatalogItem.objects.create(
            name="Camisa", service_type=ServiceType.PER_GARMENT, base_price="6.00"
        )
