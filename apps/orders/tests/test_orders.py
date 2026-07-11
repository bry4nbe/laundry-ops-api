from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import CatalogItem, ServiceType
from apps.clients.models import Client
from apps.orders import services
from apps.orders.models import DryCleaningStatus, Order
from apps.users.models import User


@pytest.fixture
def auth_client(db):
    user = User.objects.create_user(username="tester", password="testpass123")
    api = APIClient()
    api.force_authenticate(user=user)
    return api, user


@pytest.fixture
def client_obj(db):
    return Client.objects.create(name="Marco Alca", phone_number="983266763")


@pytest.fixture
def shirt(db):
    return CatalogItem.objects.create(
        name="Camisa", service_type=ServiceType.PER_GARMENT, base_price="5.00"
    )


@pytest.fixture
def suit(db):
    return CatalogItem.objects.create(
        name="Terno",
        service_type=ServiceType.PER_GARMENT,
        base_price="20.00",
        is_dry_cleaning=True,
    )


@pytest.fixture
def inactive_item(db):
    return CatalogItem.objects.create(
        name="Descontinuado",
        service_type=ServiceType.PER_GARMENT,
        base_price="1.00",
        is_active=False,
    )


def make_order(client_obj, user, items_data):
    return services.create_order(
        client=client_obj, items_data=items_data, notes="", created_by=user
    )


# --- create ---


def test_create_order_calculates_subtotals_and_total(auth_client, client_obj, shirt, suit):
    api, _ = auth_client
    response = api.post(
        "/api/orders/",
        {
            "client": client_obj.id,
            "items": [
                {"catalog_item": shirt.id, "quantity": "2"},
                {"catalog_item": suit.id, "quantity": "1"},
            ],
        },
        format="json",
    )
    assert response.status_code == 201
    data = response.data
    assert Decimal(data["items"][0]["subtotal"]) == Decimal("10.00")
    assert Decimal(data["items"][1]["subtotal"]) == Decimal("20.00")
    assert Decimal(data["total_amount"]) == Decimal("30.00")


def test_order_number_format(auth_client, client_obj, shirt):
    api, _ = auth_client
    response = api.post(
        "/api/orders/",
        {"client": client_obj.id, "items": [{"catalog_item": shirt.id, "quantity": "1"}]},
        format="json",
    )
    order = Order.objects.get(id=response.data["id"])
    assert order.order_number == f"ORD-{order.id:05d}"


def test_item_without_unit_price_uses_base_price_snapshot(auth_client, client_obj, shirt):
    api, _ = auth_client
    response = api.post(
        "/api/orders/",
        {"client": client_obj.id, "items": [{"catalog_item": shirt.id, "quantity": "1"}]},
        format="json",
    )
    assert Decimal(response.data["items"][0]["unit_price"]) == Decimal("5.00")


def test_item_with_unit_price_is_respected(auth_client, client_obj, shirt):
    api, _ = auth_client
    response = api.post(
        "/api/orders/",
        {
            "client": client_obj.id,
            "items": [{"catalog_item": shirt.id, "quantity": "1", "unit_price": "7.50"}],
        },
        format="json",
    )
    assert Decimal(response.data["items"][0]["unit_price"]) == Decimal("7.50")


def test_dry_cleaning_status_initialized_from_catalog_item(
    auth_client, client_obj, shirt, suit
):
    api, _ = auth_client
    response = api.post(
        "/api/orders/",
        {
            "client": client_obj.id,
            "items": [
                {"catalog_item": shirt.id, "quantity": "1"},
                {"catalog_item": suit.id, "quantity": "1"},
            ],
        },
        format="json",
    )
    items = {item["catalog_item"]: item for item in response.data["items"]}
    assert items[shirt.id]["dry_cleaning_status"] is None
    assert items[suit.id]["dry_cleaning_status"] == DryCleaningStatus.RECEIVED


def test_inactive_catalog_item_returns_400(auth_client, client_obj, inactive_item):
    api, _ = auth_client
    response = api.post(
        "/api/orders/",
        {
            "client": client_obj.id,
            "items": [{"catalog_item": inactive_item.id, "quantity": "1"}],
        },
        format="json",
    )
    assert response.status_code == 400


def test_create_is_atomic_when_an_item_is_invalid(
    auth_client, client_obj, shirt, inactive_item
):
    api, _ = auth_client
    response = api.post(
        "/api/orders/",
        {
            "client": client_obj.id,
            "items": [
                {"catalog_item": shirt.id, "quantity": "1"},
                {"catalog_item": inactive_item.id, "quantity": "1"},
            ],
        },
        format="json",
    )
    assert response.status_code == 400
    assert Order.objects.count() == 0


def test_create_requires_at_least_one_item(auth_client, client_obj):
    api, _ = auth_client
    response = api.post(
        "/api/orders/", {"client": client_obj.id, "items": []}, format="json"
    )
    assert response.status_code == 400


# --- update ---


def test_update_upsert_keeps_id_and_dry_cleaning_status(auth_client, client_obj, shirt, suit):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [
            {"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None},
            {"catalog_item": suit, "quantity": Decimal("1"), "unit_price": None},
        ],
    )
    suit_item = order.items.get(catalog_item=suit)
    suit_item.dry_cleaning_status = DryCleaningStatus.SENT
    suit_item.save()
    shirt_item = order.items.get(catalog_item=shirt)

    response = api.patch(
        f"/api/orders/{order.id}/",
        {"items": [{"id": shirt_item.id, "catalog_item": shirt.id, "quantity": "3"}]},
        format="json",
    )
    assert response.status_code == 200
    order.refresh_from_db()
    assert order.items.count() == 1
    remaining_item = order.items.get(id=shirt_item.id)
    assert remaining_item.quantity == Decimal("3.00")
    assert remaining_item.dry_cleaning_status is None


def test_update_item_without_id_creates_new(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    )
    existing_item = order.items.first()

    response = api.patch(
        f"/api/orders/{order.id}/",
        {
            "items": [
                {"id": existing_item.id, "catalog_item": shirt.id, "quantity": "1"},
                {"catalog_item": shirt.id, "quantity": "2"},
            ]
        },
        format="json",
    )
    assert response.status_code == 200
    order.refresh_from_db()
    assert order.items.count() == 2


def test_update_recalculates_total(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    )
    existing_item = order.items.first()

    response = api.patch(
        f"/api/orders/{order.id}/",
        {"items": [{"id": existing_item.id, "catalog_item": shirt.id, "quantity": "4"}]},
        format="json",
    )
    assert response.status_code == 200
    assert Decimal(response.data["total_amount"]) == Decimal("20.00")


def test_patch_delivered_order_returns_409(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    )
    order.delivered_at = timezone.now()
    order.save()

    response = api.patch(f"/api/orders/{order.id}/", {"notes": "cambio"}, format="json")
    assert response.status_code == 409


# --- deliver ---


def test_deliver_sets_delivered_at(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    )
    response = api.post(f"/api/orders/{order.id}/deliver/")
    assert response.status_code == 200
    order.refresh_from_db()
    assert order.delivered_at is not None


def test_deliver_twice_returns_409(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    )
    api.post(f"/api/orders/{order.id}/deliver/")
    response = api.post(f"/api/orders/{order.id}/deliver/")
    assert response.status_code == 409


def test_deliver_cancelled_order_returns_409(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    )
    order.cancelled_at = timezone.now()
    order.save()
    response = api.post(f"/api/orders/{order.id}/deliver/")
    assert response.status_code == 409


# --- dry cleaning ---


def test_dry_cleaning_updates_status(auth_client, client_obj, suit):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": suit, "quantity": Decimal("1"), "unit_price": None}]
    )
    item = order.items.first()
    response = api.patch(
        f"/api/orders/{order.id}/items/{item.id}/dry-cleaning/",
        {"dry_cleaning_status": DryCleaningStatus.SENT},
        format="json",
    )
    assert response.status_code == 200
    item.refresh_from_db()
    assert item.dry_cleaning_status == DryCleaningStatus.SENT


def test_dry_cleaning_on_non_dry_cleaning_item_returns_400(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    )
    item = order.items.first()
    response = api.patch(
        f"/api/orders/{order.id}/items/{item.id}/dry-cleaning/",
        {"dry_cleaning_status": DryCleaningStatus.SENT},
        format="json",
    )
    assert response.status_code == 400


def test_dry_cleaning_allowed_on_delivered_order(auth_client, client_obj, suit):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": suit, "quantity": Decimal("1"), "unit_price": None}]
    )
    order.delivered_at = timezone.now()
    order.save()
    item = order.items.first()
    response = api.patch(
        f"/api/orders/{order.id}/items/{item.id}/dry-cleaning/",
        {"dry_cleaning_status": DryCleaningStatus.SENT},
        format="json",
    )
    assert response.status_code == 200


def test_dry_cleaning_on_cancelled_order_returns_409(auth_client, client_obj, suit):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": suit, "quantity": Decimal("1"), "unit_price": None}]
    )
    order.cancelled_at = timezone.now()
    order.save()
    item = order.items.first()
    response = api.patch(
        f"/api/orders/{order.id}/items/{item.id}/dry-cleaning/",
        {"dry_cleaning_status": DryCleaningStatus.SENT},
        format="json",
    )
    assert response.status_code == 409


# --- list ---


def test_list_filter_delivered_false_excludes_delivered_and_cancelled(
    auth_client, client_obj, shirt
):
    api, user = auth_client
    active = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    )
    delivered = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    )
    delivered.delivered_at = timezone.now()
    delivered.save()
    cancelled = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    )
    cancelled.cancelled_at = timezone.now()
    cancelled.save()

    response = api.get("/api/orders/?delivered=false")
    assert response.status_code == 200
    ids = [order["id"] for order in response.data["results"]]
    assert ids == [active.id]


def test_list_filter_by_client(auth_client, client_obj, shirt):
    api, user = auth_client
    other_client = Client.objects.create(name="Otro Cliente", phone_number="911111111")
    order_for_client = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    )
    make_order(
        other_client, user, [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    )

    response = api.get(f"/api/orders/?client={client_obj.id}")
    assert response.status_code == 200
    ids = [order["id"] for order in response.data["results"]]
    assert ids == [order_for_client.id]


def test_list_requires_authentication():
    api = APIClient()
    response = api.get("/api/orders/")
    assert response.status_code == 401
