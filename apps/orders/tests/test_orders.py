import json
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import APIException
from rest_framework.test import APIClient

from apps.catalog.models import CatalogItem, ServiceType
from apps.clients.models import Client
from apps.orders import services
from apps.orders.models import DryCleaningStatus, Order, OrderItem
from apps.users.models import User


@pytest.fixture
def auth_client(db):
    user = User.objects.create_user(
        username="tester", password="testpass123", email="tester@example.com"
    )
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


def test_create_order_calculates_subtotals_and_total(
    auth_client, client_obj, shirt, suit
):
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
        {
            "client": client_obj.id,
            "items": [{"catalog_item": shirt.id, "quantity": "1"}],
        },
        format="json",
    )
    order = Order.objects.get(id=response.data["id"])
    assert order.order_number == f"ORD-{order.id:05d}"


def test_item_without_unit_price_uses_base_price_snapshot(
    auth_client, client_obj, shirt
):
    api, _ = auth_client
    response = api.post(
        "/api/orders/",
        {
            "client": client_obj.id,
            "items": [{"catalog_item": shirt.id, "quantity": "1"}],
        },
        format="json",
    )
    assert Decimal(response.data["items"][0]["unit_price"]) == Decimal("5.00")


def test_item_with_unit_price_is_respected(auth_client, client_obj, shirt):
    api, _ = auth_client
    response = api.post(
        "/api/orders/",
        {
            "client": client_obj.id,
            "items": [
                {"catalog_item": shirt.id, "quantity": "1", "unit_price": "7.50"}
            ],
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


def test_update_upsert_keeps_id_and_dry_cleaning_status(
    auth_client, client_obj, shirt, suit
):
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
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
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
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    existing_item = order.items.first()

    response = api.patch(
        f"/api/orders/{order.id}/",
        {
            "items": [
                {"id": existing_item.id, "catalog_item": shirt.id, "quantity": "4"}
            ]
        },
        format="json",
    )
    assert response.status_code == 200
    assert Decimal(response.data["total_amount"]) == Decimal("20.00")


def test_patch_delivered_order_returns_409(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    order.delivered_at = timezone.now()
    order.save()

    response = api.patch(f"/api/orders/{order.id}/", {"notes": "cambio"}, format="json")
    assert response.status_code == 409


# --- deliver ---


def test_deliver_sets_delivered_at(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    response = api.post(f"/api/orders/{order.id}/deliver/")
    assert response.status_code == 200
    order.refresh_from_db()
    assert order.delivered_at is not None


def test_deliver_twice_returns_409(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    api.post(f"/api/orders/{order.id}/deliver/")
    response = api.post(f"/api/orders/{order.id}/deliver/")
    assert response.status_code == 409


def test_deliver_cancelled_order_returns_409(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    order.cancelled_at = timezone.now()
    order.save()
    response = api.post(f"/api/orders/{order.id}/deliver/")
    assert response.status_code == 409


# --- dry cleaning ---


def test_dry_cleaning_updates_status(auth_client, client_obj, suit):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": suit, "quantity": Decimal("1"), "unit_price": None}],
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


def test_dry_cleaning_on_non_dry_cleaning_item_returns_400(
    auth_client, client_obj, shirt
):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
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
        client_obj,
        user,
        [{"catalog_item": suit, "quantity": Decimal("1"), "unit_price": None}],
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
        client_obj,
        user,
        [{"catalog_item": suit, "quantity": Decimal("1"), "unit_price": None}],
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
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    delivered = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    delivered.delivered_at = timezone.now()
    delivered.save()
    cancelled = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
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
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    make_order(
        other_client,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )

    response = api.get(f"/api/orders/?client={client_obj.id}")
    assert response.status_code == 200
    ids = [order["id"] for order in response.data["results"]]
    assert ids == [order_for_client.id]


def test_list_requires_authentication():
    api = APIClient()
    response = api.get("/api/orders/")
    assert response.status_code == 401


def order_snapshot(order):
    order.refresh_from_db()
    return {
        "client": order.client_id,
        "notes": order.notes,
        "total_amount": order.total_amount,
        "delivered_at": order.delivered_at,
        "cancelled_at": order.cancelled_at,
        "items": list(
            order.items.order_by("id").values(
                "id",
                "catalog_item_id",
                "quantity",
                "unit_price",
                "subtotal",
                "dry_cleaning_status",
            )
        ),
    }


def test_order_workflow_with_real_jwt(shirt, db):
    user = User.objects.create_user(
        username="operator", name="Test Operator", password="testpass123", email=None
    )
    api = APIClient()
    login = api.post(
        "/api/auth/login/",
        {"username": user.username, "password": "testpass123"},
        format="json",
    )
    assert login.status_code == 200
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    client_response = api.post("/api/clients/", {"name": "Test Client"}, format="json")
    assert client_response.status_code == 201
    create = api.post(
        "/api/orders/",
        {
            "client": client_response.data["id"],
            "items": [{"catalog_item": shirt.id, "quantity": "2"}],
        },
        format="json",
    )
    assert create.status_code == 201
    order = Order.objects.get(pk=create.data["id"])
    assert order.created_by_id == user.id
    assert order.total_amount == Decimal("10.00")
    detail = api.get(f"/api/orders/{order.id}/")
    assert detail.status_code == 200
    assert detail.data["id"] == order.id
    assert Decimal(detail.data["total_amount"]) == order.total_amount
    update = api.patch(
        f"/api/orders/{order.id}/", {"notes": "Updated notes"}, format="json"
    )
    assert update.status_code == 200
    assert Decimal(update.data["total_amount"]) == Decimal("10.00")
    deliver = api.post(f"/api/orders/{order.id}/deliver/")
    assert deliver.status_code == 200
    assert deliver.data["status"] == "DELIVERED"
    rejected = api.patch(
        f"/api/orders/{order.id}/", {"notes": "Rejected notes"}, format="json"
    )
    assert rejected.status_code == 409
    order.refresh_from_db()
    assert order.notes == "Updated notes"
    assert order.delivered_at is not None


@pytest.mark.parametrize("field", ["client", "catalog_item"])
@pytest.mark.parametrize(
    "invalid_kind",
    ["missing", "text", "zero", "negative", "null", "boolean", "fractional"],
)
def test_create_rejects_invalid_related_ids(
    auth_client, client_obj, shirt, field, invalid_kind
):
    api, _ = auth_client
    valid_id = client_obj.id if field == "client" else shirt.id
    invalid_id = {
        "missing": valid_id + 10000,
        "text": "abc",
        "zero": 0,
        "negative": -1,
        "null": None,
        "boolean": True,
        "fractional": valid_id + 0.5,
    }[invalid_kind]
    data = {
        "client": client_obj.id,
        "items": [{"catalog_item": shirt.id, "quantity": "1"}],
    }
    if field == "client":
        data["client"] = invalid_id
    else:
        data["items"][0]["catalog_item"] = invalid_id

    response = api.post("/api/orders/", data, format="json")

    assert response.status_code == 400
    assert Order.objects.count() == 0
    assert OrderItem.objects.count() == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", "0"),
        ("quantity", "-1"),
        ("quantity", "0.001"),
        ("unit_price", "-0.01"),
    ],
)
def test_create_rejects_invalid_quantities_and_prices(
    auth_client, client_obj, shirt, field, value
):
    api, _ = auth_client
    item = {"catalog_item": shirt.id, "quantity": "1", field: value}

    response = api.post(
        "/api/orders/", {"client": client_obj.id, "items": [item]}, format="json"
    )

    assert response.status_code == 400
    assert Order.objects.count() == 0
    assert OrderItem.objects.count() == 0


def test_zero_unit_price_is_allowed(auth_client, client_obj, shirt):
    api, _ = auth_client
    response = api.post(
        "/api/orders/",
        {
            "client": client_obj.id,
            "items": [{"catalog_item": shirt.id, "quantity": "1", "unit_price": "0"}],
        },
        format="json",
    )
    assert response.status_code == 201
    order = Order.objects.get(pk=response.data["id"])
    assert order.total_amount == Decimal("0.00")
    assert order.items.get().unit_price == Decimal("0.00")


@pytest.mark.parametrize("amount_kind", ["subtotal", "total"])
def test_create_rejects_amount_overflow_without_partial_writes(
    auth_client, client_obj, shirt, amount_kind
):
    api, _ = auth_client
    if amount_kind == "subtotal":
        items = [
            {"catalog_item": shirt.id, "quantity": "9999.99", "unit_price": "999999.99"}
        ]
    else:
        items = [
            {"catalog_item": shirt.id, "quantity": "100", "unit_price": "500000"}
        ] * 2

    response = api.post(
        "/api/orders/", {"client": client_obj.id, "items": items}, format="json"
    )

    assert response.status_code == 400
    assert Order.objects.count() == 0
    assert OrderItem.objects.count() == 0


@pytest.mark.parametrize(
    "invalid_kind",
    ["missing", "text", "zero", "negative", "null", "boolean", "fractional"],
)
def test_update_rejects_invalid_item_ids_without_changes(
    auth_client, client_obj, shirt, invalid_kind
):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    item = order.items.get()
    before = order_snapshot(order)
    invalid_id = {
        "missing": item.id + 10000,
        "text": "abc",
        "zero": 0,
        "negative": -1,
        "null": None,
        "boolean": True,
        "fractional": item.id + 0.5,
    }[invalid_kind]

    response = api.patch(
        f"/api/orders/{order.id}/",
        {"items": [{"id": invalid_id, "catalog_item": shirt.id, "quantity": "2"}]},
        format="json",
    )

    assert response.status_code == 400
    assert order_snapshot(order) == before


def test_update_rejects_duplicate_item_ids_without_changes(
    auth_client, client_obj, shirt
):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    item = order.items.get()
    before = order_snapshot(order)

    response = api.patch(
        f"/api/orders/{order.id}/",
        {
            "items": [
                {"id": item.id, "catalog_item": shirt.id, "quantity": quantity}
                for quantity in ["2", "3"]
            ]
        },
        format="json",
    )

    assert response.status_code == 400
    assert order_snapshot(order) == before


def test_update_rejects_items_from_another_order_and_rolls_back(
    auth_client, client_obj, shirt
):
    api, user = auth_client
    items = [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}]
    order = make_order(client_obj, user, items)
    other_order = make_order(client_obj, user, items)
    before = order_snapshot(order)
    other_before = order_snapshot(other_order)

    response = api.patch(
        f"/api/orders/{order.id}/",
        {
            "notes": "Rejected notes",
            "items": [
                {"id": row.items.get().id, "catalog_item": shirt.id, "quantity": "2"}
                for row in [order, other_order]
            ],
        },
        format="json",
    )

    assert response.status_code == 400
    assert order_snapshot(order) == before
    assert order_snapshot(other_order) == other_before


def test_update_rejects_empty_items_without_changes(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    before = order_snapshot(order)

    response = api.patch(f"/api/orders/{order.id}/", {"items": []}, format="json")

    assert response.status_code == 400
    assert order_snapshot(order) == before


@pytest.mark.parametrize("missing_field", ["catalog_item", "quantity"])
def test_update_rejects_incomplete_items_without_changes(
    auth_client, client_obj, shirt, missing_field
):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    before = order_snapshot(order)
    item = {"id": order.items.get().id, "catalog_item": shirt.id, "quantity": "2"}
    del item[missing_field]

    response = api.patch(f"/api/orders/{order.id}/", {"items": [item]}, format="json")

    assert response.status_code == 400
    assert order_snapshot(order) == before


@pytest.mark.parametrize(
    ("field", "value"), [("quantity", "-1"), ("unit_price", "-0.01")]
)
def test_update_rejects_negative_amounts_without_changes(
    auth_client, client_obj, shirt, field, value
):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    before = order_snapshot(order)
    item = {
        "id": order.items.get().id,
        "catalog_item": shirt.id,
        "quantity": "2",
        field: value,
    }

    response = api.patch(f"/api/orders/{order.id}/", {"items": [item]}, format="json")

    assert response.status_code == 400
    assert order_snapshot(order) == before


@pytest.mark.parametrize("amount_kind", ["subtotal", "total"])
def test_update_rejects_amount_overflow_and_rolls_back(
    auth_client, client_obj, shirt, amount_kind
):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    before = order_snapshot(order)
    item = {
        "id": order.items.get().id,
        "catalog_item": shirt.id,
        "quantity": "9999.99",
        "unit_price": "999999.99",
    }
    items = [item]
    if amount_kind == "total":
        item.update(quantity="100", unit_price="500000")
        items.append(
            {"catalog_item": shirt.id, "quantity": "100", "unit_price": "500000"}
        )

    response = api.patch(
        f"/api/orders/{order.id}/",
        {"notes": "Rejected notes", "items": items},
        format="json",
    )

    assert response.status_code == 400
    assert order_snapshot(order) == before


@pytest.mark.parametrize("original_price", [None, Decimal("7.50"), Decimal("0")])
def test_update_preserves_price_snapshot_when_price_is_omitted(
    auth_client, client_obj, shirt, original_price
):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [
            {
                "catalog_item": shirt,
                "quantity": Decimal("1"),
                "unit_price": original_price,
            }
        ],
    )
    item = order.items.get()
    expected_price = item.unit_price
    shirt.base_price = Decimal("99.00")
    shirt.save(update_fields=["base_price"])

    response = api.patch(
        f"/api/orders/{order.id}/",
        {"items": [{"id": item.id, "catalog_item": shirt.id, "quantity": "2"}]},
        format="json",
    )

    assert response.status_code == 200
    item.refresh_from_db()
    order.refresh_from_db()
    assert item.unit_price == expected_price
    assert item.subtotal == expected_price * 2
    assert order.total_amount == sum(order.items.values_list("subtotal", flat=True))


@pytest.mark.parametrize("requested_price", [None, "9.00"])
def test_update_respects_explicit_price_override(
    auth_client, client_obj, shirt, requested_price
):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [
            {
                "catalog_item": shirt,
                "quantity": Decimal("1"),
                "unit_price": Decimal("7.50"),
            }
        ],
    )
    item = order.items.get()

    response = api.patch(
        f"/api/orders/{order.id}/",
        {
            "items": [
                {
                    "id": item.id,
                    "catalog_item": shirt.id,
                    "quantity": "2",
                    "unit_price": requested_price,
                }
            ]
        },
        format="json",
    )

    assert response.status_code == 200
    expected_price = (
        Decimal(shirt.base_price)
        if requested_price is None
        else Decimal(requested_price)
    )
    item.refresh_from_db()
    order.refresh_from_db()
    assert item.unit_price == expected_price
    assert order.total_amount == expected_price * 2


def test_create_rejects_negative_catalog_price(auth_client, client_obj, shirt):
    api, _ = auth_client
    shirt.base_price = Decimal("-1")
    shirt.save(update_fields=["base_price"])

    response = api.post(
        "/api/orders/",
        {
            "client": client_obj.id,
            "items": [{"catalog_item": shirt.id, "quantity": "1"}],
        },
        format="json",
    )

    assert response.status_code == 400
    assert Order.objects.count() == 0
    assert OrderItem.objects.count() == 0


def test_create_accepts_total_at_storage_limit(auth_client, client_obj, shirt):
    api, _ = auth_client
    response = api.post(
        "/api/orders/",
        {
            "client": client_obj.id,
            "items": [
                {
                    "catalog_item": shirt.id,
                    "quantity": "100",
                    "unit_price": "999999.99",
                },
                {"catalog_item": shirt.id, "quantity": "1", "unit_price": "0.99"},
            ],
        },
        format="json",
    )

    assert response.status_code == 201
    order = Order.objects.get(pk=response.data["id"])
    assert order.total_amount == Decimal("99999999.99")
    assert order.total_amount == sum(order.items.values_list("subtotal", flat=True))


@pytest.mark.parametrize("was_dry_cleaning", [True, False])
def test_update_synchronizes_dry_cleaning_status_with_catalog(
    auth_client, client_obj, shirt, suit, was_dry_cleaning
):
    api, user = auth_client
    original, replacement = (suit, shirt) if was_dry_cleaning else (shirt, suit)
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": original, "quantity": Decimal("1"), "unit_price": None}],
    )
    item = order.items.get()

    response = api.patch(
        f"/api/orders/{order.id}/",
        {"items": [{"id": item.id, "catalog_item": replacement.id, "quantity": "2"}]},
        format="json",
    )

    assert response.status_code == 200
    item.refresh_from_db()
    assert item.dry_cleaning_status == (
        None if was_dry_cleaning else DryCleaningStatus.RECEIVED
    )
    assert item.unit_price == Decimal(replacement.base_price)


def test_update_preserves_existing_dry_cleaning_tracking(auth_client, client_obj, suit):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": suit, "quantity": Decimal("1"), "unit_price": None}],
    )
    item = order.items.get()
    item.dry_cleaning_status = DryCleaningStatus.SENT
    item.save(update_fields=["dry_cleaning_status"])

    response = api.patch(
        f"/api/orders/{order.id}/",
        {"items": [{"id": item.id, "catalog_item": suit.id, "quantity": "2"}]},
        format="json",
    )

    assert response.status_code == 200
    item.refresh_from_db()
    assert item.dry_cleaning_status == DryCleaningStatus.SENT


def test_update_rolls_back_when_later_catalog_item_is_inactive(
    auth_client, client_obj, shirt, inactive_item
):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    before = order_snapshot(order)

    response = api.patch(
        f"/api/orders/{order.id}/",
        {
            "notes": "Rejected notes",
            "items": [
                {"id": order.items.get().id, "catalog_item": shirt.id, "quantity": "2"},
                {"catalog_item": inactive_item.id, "quantity": "1"},
            ],
        },
        format="json",
    )

    assert response.status_code == 400
    assert order_snapshot(order) == before


@pytest.mark.parametrize("closed_field", ["delivered_at", "cancelled_at"])
def test_stale_order_update_cannot_reopen_closed_order(
    auth_client, client_obj, shirt, closed_field
):
    _, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    Order.objects.filter(pk=order.pk).update(**{closed_field: timezone.now()})
    before = order_snapshot(Order.objects.get(pk=order.pk))

    with pytest.raises(APIException) as error:
        services.update_order(order, notes="Rejected notes")

    assert error.value.status_code == 409
    assert order_snapshot(order) == before


@pytest.mark.parametrize("identifier", ["abc", "0", "-1", "missing"])
def test_detail_rejects_invalid_or_missing_path_id(auth_client, identifier):
    api, _ = auth_client
    identifier = "999999" if identifier == "missing" else identifier
    response = api.get(f"/api/orders/{identifier}/")
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("client", "abc"),
        ("client", "1.5"),
        ("client", "0"),
        ("date_from", "abc"),
        ("date_from", "2026-02-30"),
        ("date_to", "abc"),
    ],
)
def test_list_rejects_invalid_filter_values(auth_client, field, value):
    api, _ = auth_client
    response = api.get("/api/orders/", {field: value})
    assert response.status_code == 400


def test_list_accepts_valid_date_range(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [{"catalog_item": shirt, "quantity": Decimal("1"), "unit_price": None}],
    )
    today = timezone.localdate().isoformat()

    response = api.get("/api/orders/", {"date_from": today, "date_to": today})

    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [order.id]


def test_dry_cleaning_item_cannot_be_accessed_through_another_order(
    auth_client, client_obj, suit
):
    api, user = auth_client
    items = [{"catalog_item": suit, "quantity": Decimal("1"), "unit_price": None}]
    order = make_order(client_obj, user, items)
    other_order = make_order(client_obj, user, items)
    before = order_snapshot(other_order)

    response = api.patch(
        f"/api/orders/{order.id}/items/{other_order.items.get().id}/dry-cleaning/",
        {"dry_cleaning_status": DryCleaningStatus.SENT},
        format="json",
    )

    assert response.status_code == 404
    assert order_snapshot(other_order) == before


@pytest.mark.parametrize("body", [[], None, "SENT", 123, True])
def test_dry_cleaning_rejects_non_object_json(auth_client, client_obj, suit, body):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": suit, "quantity": Decimal("1")}]
    )
    item = order.items.get()

    response = api.generic(
        "PATCH",
        f"/api/orders/{order.id}/items/{item.id}/dry-cleaning/",
        data=json.dumps(body),
        content_type="application/json",
    )

    assert response.status_code == 400
    item.refresh_from_db()
    assert item.dry_cleaning_status == DryCleaningStatus.RECEIVED


def order_admin_data(order):
    item = order.items.get()
    return {
        "order_number": order.order_number,
        "client": order.client_id,
        "notes": "Corrected notes",
        "total_amount": str(order.total_amount),
        "created_by": order.created_by_id,
        "delivered_at_0": "",
        "delivered_at_1": "",
        "cancelled_at_0": "",
        "cancelled_at_1": "",
        "items-TOTAL_FORMS": "1",
        "items-INITIAL_FORMS": "1",
        "items-MIN_NUM_FORMS": "0",
        "items-MAX_NUM_FORMS": "1000",
        "items-0-id": item.pk,
        "items-0-order": order.pk,
        "items-0-catalog_item": item.catalog_item_id,
        "items-0-quantity": str(item.quantity),
        "items-0-unit_price": str(item.unit_price),
        "items-0-subtotal": str(item.subtotal),
        "items-0-dry_cleaning_status": item.dry_cleaning_status or "",
        "_save": "Save",
    }


@pytest.mark.parametrize("tampering", ["amounts", "add_item", "delete_item"])
def test_admin_cannot_bypass_order_calculations(
    admin_client, auth_client, client_obj, shirt, suit, tampering
):
    _, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1")}]
    )
    original_number = order.order_number
    data = order_admin_data(order)
    if tampering == "amounts":
        data.update(
            {
                "order_number": "ORD-HACK",
                "total_amount": "999.00",
                "items-0-catalog_item": suit.pk,
                "items-0-quantity": "2.00",
                "items-0-unit_price": "100.00",
                "items-0-subtotal": "200.00",
            }
        )
    elif tampering == "add_item":
        data.update(
            {
                "items-TOTAL_FORMS": "2",
                "items-1-id": "",
                "items-1-order": order.pk,
                "items-1-catalog_item": suit.pk,
                "items-1-quantity": "1.00",
                "items-1-unit_price": "20.00",
                "items-1-subtotal": "20.00",
                "items-1-dry_cleaning_status": DryCleaningStatus.RECEIVED,
            }
        )
    else:
        data["items-0-DELETE"] = "on"

    response = admin_client.post(
        reverse("admin:orders_order_change", args=[order.pk]), data
    )

    assert response.status_code == 302
    order.refresh_from_db()
    item = order.items.get()
    assert order.notes == "Corrected notes"
    assert order.order_number == original_number
    assert item.catalog_item_id == shirt.pk
    assert item.quantity == Decimal("1.00")
    assert item.unit_price == Decimal("5.00")
    assert item.subtotal == item.quantity * item.unit_price
    assert order.total_amount == item.subtotal


@pytest.mark.parametrize("method", ["get", "post"])
def test_admin_cannot_create_order_outside_service(admin_client, method):
    response = getattr(admin_client, method)(reverse("admin:orders_order_add"))
    assert response.status_code == 403
    assert Order.objects.count() == 0


def test_admin_can_correct_order_state_and_dry_cleaning_tracking(
    admin_client, auth_client, client_obj, suit
):
    _, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": suit, "quantity": Decimal("1")}]
    )
    data = order_admin_data(order)
    data.update(
        {
            "delivered_at_0": "2026-10-02",
            "delivered_at_1": "10:00:00",
            "items-0-dry_cleaning_status": DryCleaningStatus.SENT,
        }
    )

    response = admin_client.post(
        reverse("admin:orders_order_change", args=[order.pk]), data
    )

    assert response.status_code == 302
    order.refresh_from_db()
    assert order.delivered_at is not None
    assert order.notes == "Corrected notes"
    assert order.items.get().dry_cleaning_status == DryCleaningStatus.SENT
    assert order.total_amount == Decimal("20.00")
