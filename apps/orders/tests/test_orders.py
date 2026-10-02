import json
from decimal import Decimal
from uuid import uuid4

import pytest
from django.contrib import admin
from django.db import transaction
from django.forms import inlineformset_factory
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import APIException
from rest_framework.test import APIClient

from apps.catalog.models import CatalogItem, ServiceType
from apps.clients.models import Client
from apps.orders import services
from apps.orders.admin import OrderAdmin
from apps.orders.models import DryCleaningStatus, Order, OrderItem
from apps.payments import services as payment_services
from apps.payments.models import Payment, PaymentMethod, PaymentType
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


@pytest.mark.parametrize("search_by", ["client_name", "order_number"])
def test_list_search_matches_client_name_or_order_number(
    auth_client, client_obj, shirt, search_by
):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1")}]
    )
    other_client = Client.objects.create(name="Other Customer")
    make_order(other_client, user, [{"catalog_item": shirt, "quantity": Decimal("1")}])
    term = "  aRCO aLc  " if search_by == "client_name" else order.order_number.lower()
    response = api.get("/api/orders/", {"search": term})
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert [row["id"] for row in response.data["results"]] == [order.pk]


@pytest.mark.parametrize("term", ["", " \t "])
def test_blank_search_does_not_filter_orders(auth_client, client_obj, shirt, term):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1")}]
    )
    response = api.get("/api/orders/", {"search": term})
    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [order.pk]


def test_search_without_matches_returns_empty_page(auth_client, client_obj, shirt):
    api, user = auth_client
    make_order(client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("1")}])
    response = api.get("/api/orders/", {"search": "not-a-matching-customer"})
    assert response.status_code == 200
    assert response.data["count"] == 0
    assert response.data["results"] == []


def test_search_pending_orders_excludes_closed_orders_and_keeps_real_balances(
    auth_client, client_obj, shirt
):
    api, user = auth_client
    orders = [
        make_order(
            client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("2")}]
        )
        for _ in range(3)
    ]
    Order.objects.filter(pk=orders[1].pk).update(delivered_at=timezone.now())
    Order.objects.filter(pk=orders[2].pk).update(cancelled_at=timezone.now())
    Payment.objects.create(
        order=orders[0],
        created_by=user,
        amount=Decimal("2.00"),
        payment_method=PaymentMethod.CASH,
        payment_type=PaymentType.PARTIAL,
    )
    response = api.get("/api/orders/", {"search": "marco", "delivered": "false"})
    assert response.status_code == 200
    assert response.data["count"] == 1
    row = response.data["results"][0]
    assert row["id"] == orders[0].pk
    assert row["status"] == "ACTIVE"
    assert row["total_amount"] == "10.00"
    assert row["paid_amount"] == "2.00"
    assert row["balance"] == "8.00"


def test_search_combines_with_existing_client_and_date_filters(
    auth_client, client_obj, shirt
):
    api, user = auth_client
    items = [{"catalog_item": shirt, "quantity": Decimal("1")}]
    today = timezone.localdate().isoformat()
    wanted = make_order(client_obj, user, items)
    old = make_order(client_obj, user, items)
    Order.objects.filter(pk=old.pk).update(created_at="2000-01-01T12:00:00Z")
    other = Client.objects.create(name="Marco Other")
    make_order(other, user, items)
    response = api.get(
        "/api/orders/",
        {
            "search": "marco",
            "delivered": "false",
            "client": client_obj.pk,
            "date_from": today,
            "date_to": today,
        },
    )
    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [wanted.pk]


def test_search_keeps_pagination_ordering_and_constant_queries(
    auth_client, client_obj, shirt, django_assert_num_queries
):
    api, user = auth_client
    identifiers = []
    created_at = timezone.now()
    for _ in range(21):
        order = make_order(
            client_obj,
            user,
            [
                {"catalog_item": shirt, "quantity": Decimal("1")},
                {"catalog_item": shirt, "quantity": Decimal("1")},
            ],
        )
        identifiers.append(order.pk)
        Order.objects.filter(pk=order.pk).update(created_at=created_at)
        Payment.objects.create(
            order=order,
            created_by=user,
            amount=Decimal("2.00"),
            payment_method=PaymentMethod.CASH,
            payment_type=PaymentType.PARTIAL,
        )
    with django_assert_num_queries(4):
        first = api.get("/api/orders/", {"search": "marco", "delivered": "false"})
    assert first.status_code == 200
    assert first.data["count"] == 21
    assert [row["id"] for row in first.data["results"]] == list(reversed(identifiers))[
        :20
    ]
    assert all(row["balance"] == "8.00" for row in first.data["results"])
    with django_assert_num_queries(4):
        second = api.get(first.data["next"])
    assert second.status_code == 200
    assert [row["id"] for row in second.data["results"]] == [identifiers[0]]


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


@pytest.mark.parametrize(
    "payment_data",
    ["absent", None, {"amount": "0.00"}, {"amount": "0", "payment_method": "CASH"}],
)
def test_create_order_without_positive_advance_has_no_payment(
    auth_client, client_obj, shirt, payment_data
):
    api, _ = auth_client
    body = {
        "client": client_obj.pk,
        "items": [{"catalog_item": shirt.pk, "quantity": "2.00"}],
    }
    if payment_data != "absent":
        body["payment"] = payment_data
    response = api.post(
        reverse("order-list-create"),
        body,
        format="json",
        HTTP_IDEMPOTENCY_KEY="ignored-without-advance",
    )
    assert response.status_code == 201
    assert response.data["paid_amount"] == "0.00"
    assert response.data["balance"] == "10.00"
    assert Payment.objects.count() == 0


@pytest.mark.parametrize("method", PaymentMethod.values)
@pytest.mark.parametrize(
    "amount,payment_type,balance",
    [("4.00", PaymentType.ADVANCE, "6.00"), ("10.00", PaymentType.FINAL, "0.00")],
)
def test_create_order_with_advance_or_full_payment(
    auth_client, client_obj, shirt, method, amount, payment_type, balance
):
    api, user = auth_client
    response = api.post(
        reverse("order-list-create"),
        {
            "client": client_obj.pk,
            "items": [{"catalog_item": shirt.pk, "quantity": "2.00"}],
            "payment": {
                "amount": amount,
                "payment_method": method,
                "reference_code": "INITIAL-REF",
            },
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert response.status_code == 201
    assert response.data["paid_amount"] == amount
    assert response.data["balance"] == balance
    payment = Payment.objects.get()
    assert payment.order_id == response.data["id"]
    assert payment.created_by_id == user.pk
    assert payment.payment_type == payment_type
    assert payment.reference_code == "INITIAL-REF"


@pytest.mark.parametrize(
    "payment_data",
    [
        {"amount": "10.01", "payment_method": "CASH"},
        {"amount": "-1.00", "payment_method": "CASH"},
        {"amount": "0.001", "payment_method": "CASH"},
        {"amount": "5.00"},
        {"amount": "5.00", "payment_method": "OTHER"},
        {"amount": "bad", "payment_method": "CASH"},
    ],
)
def test_invalid_advance_rolls_back_entire_order(
    auth_client, client_obj, shirt, payment_data
):
    api, _ = auth_client
    key = str(uuid4())
    response = api.post(
        reverse("order-list-create"),
        {
            "client": client_obj.pk,
            "items": [{"catalog_item": shirt.pk, "quantity": "2.00"}],
            "payment": payment_data,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert response.status_code == 400
    assert (
        Order.objects.count()
        == OrderItem.objects.count()
        == Payment.objects.count()
        == 0
    )
    retry = api.post(
        reverse("order-list-create"),
        {
            "client": client_obj.pk,
            "items": [{"catalog_item": shirt.pk, "quantity": "2.00"}],
            "payment": {"amount": "5.00", "payment_method": "CASH"},
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert retry.status_code == 201


def test_invalid_order_with_advance_does_not_record_payment(
    auth_client, client_obj, inactive_item
):
    api, _ = auth_client
    response = api.post(
        reverse("order-list-create"),
        {
            "client": client_obj.pk,
            "items": [{"catalog_item": inactive_item.pk, "quantity": "2.00"}],
            "payment": {"amount": "1.00", "payment_method": "CASH"},
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert response.status_code == 400
    assert (
        Order.objects.count()
        == OrderItem.objects.count()
        == Payment.objects.count()
        == 0
    )


@pytest.mark.parametrize("key", [None, "not-a-uuid"])
def test_positive_advance_requires_uuid(auth_client, client_obj, shirt, key):
    api, _ = auth_client
    headers = {} if key is None else {"HTTP_IDEMPOTENCY_KEY": key}
    response = api.post(
        reverse("order-list-create"),
        {
            "client": client_obj.pk,
            "items": [{"catalog_item": shirt.pk, "quantity": "2.00"}],
            "payment": {"amount": "5.00", "payment_method": "CASH"},
        },
        format="json",
        **headers,
    )
    assert response.status_code == 400
    assert Order.objects.count() == Payment.objects.count() == 0


def record_order_payment(api, order, amount="5.00"):
    response = api.post(
        reverse("order-payments", args=[order.pk]),
        {"amount": amount, "payment_method": "CASH"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert response.status_code == 201
    return Payment.objects.get(pk=response.data["id"])


@pytest.mark.parametrize(
    "change", ["catalog", "quantity", "price", "remove", "identical"]
)
def test_active_payment_blocks_all_economic_item_changes(
    auth_client, client_obj, shirt, suit, change
):
    api, user = auth_client
    order = make_order(
        client_obj,
        user,
        [
            {"catalog_item": shirt, "quantity": Decimal("1.00")},
            {"catalog_item": suit, "quantity": Decimal("1.00")},
        ],
    )
    record_order_payment(api, order)
    items = [
        {
            "id": item.pk,
            "catalog_item": item.catalog_item_id,
            "quantity": str(item.quantity),
            "unit_price": str(item.unit_price),
        }
        for item in order.items.order_by("id")
    ]
    if change == "catalog":
        items[0]["catalog_item"] = suit.pk
    elif change == "quantity":
        items[0]["quantity"] = "2.00"
    elif change == "price":
        items[0]["unit_price"] = "1.00"
    elif change == "remove":
        items = items[:1]
    response = api.patch(
        reverse("order-detail", args=[order.pk]), {"items": items}, format="json"
    )
    assert response.status_code == 409
    order.refresh_from_db()
    assert order.total_amount == Decimal("25.00")
    assert order.items.count() == 2
    assert order.items.get(catalog_item=shirt).quantity == Decimal("1.00")


def test_active_payment_allows_client_and_notes_changes(auth_client, client_obj, shirt):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("2.00")}]
    )
    record_order_payment(api, order)
    new_client = Client.objects.create(name="Corrected customer")
    response = api.patch(
        reverse("order-detail", args=[order.pk]),
        {"client": new_client.pk, "notes": "Corrected notes"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["client"]["id"] == new_client.pk
    assert response.data["notes"] == "Corrected notes"
    assert response.data["paid_amount"] == "5.00"
    assert response.data["balance"] == "5.00"


def test_only_voiding_all_payments_unlocks_economic_editing(
    auth_client, client_obj, shirt, admin_user
):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("3.00")}]
    )
    first = record_order_payment(api, order)
    second = record_order_payment(api, order)
    body = {"items": [{"catalog_item": shirt.pk, "quantity": "1.00"}]}
    payment_services.void_payment(
        first.pk, voided_by=admin_user, reason="First correction"
    )
    assert (
        api.patch(
            reverse("order-detail", args=[order.pk]), body, format="json"
        ).status_code
        == 409
    )
    payment_services.void_payment(
        second.pk, voided_by=admin_user, reason="Second correction"
    )
    response = api.patch(reverse("order-detail", args=[order.pk]), body, format="json")
    assert response.status_code == 200
    assert response.data["total_amount"] == "5.00"
    assert response.data["paid_amount"] == "0.00"
    assert response.data["balance"] == "5.00"
    assert order.payments.count() == 2


@pytest.mark.parametrize("closed_field", ["delivered_at", "cancelled_at"])
def test_voiding_all_payments_does_not_unlock_closed_order(
    auth_client, client_obj, shirt, admin_user, closed_field
):
    api, user = auth_client
    order = make_order(
        client_obj, user, [{"catalog_item": shirt, "quantity": Decimal("2.00")}]
    )
    payment = record_order_payment(api, order)
    setattr(order, closed_field, timezone.now())
    order.save(update_fields=[closed_field])
    payment_services.void_payment(payment.pk, voided_by=admin_user, reason="Correction")
    response = api.patch(
        reverse("order-detail", args=[order.pk]),
        {"items": [{"catalog_item": shirt.pk, "quantity": "1.00"}]},
        format="json",
    )
    assert response.status_code == 409
    order.refresh_from_db()
    assert order.total_amount == Decimal("10.00")


def test_admin_saves_only_editable_fields_from_stale_order_and_inline(
    auth_client, client_obj, suit
):
    _, user = auth_client
    stale_order = make_order(
        client_obj, user, [{"catalog_item": suit, "quantity": Decimal("1.00")}]
    )
    item = stale_order.items.get()
    formset_class = inlineformset_factory(
        Order, OrderItem, fields=["dry_cleaning_status"], extra=0, can_delete=False
    )
    formset = formset_class(
        instance=stale_order,
        data={
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "1",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-id": item.pk,
            "items-0-order": stale_order.pk,
            "items-0-dry_cleaning_status": DryCleaningStatus.SENT,
        },
        prefix="items",
    )
    assert formset.is_valid(), formset.errors
    services.update_order(
        stale_order,
        items_data=[
            {
                "id": item.pk,
                "catalog_item": suit,
                "quantity": Decimal("2.00"),
                "unit_price": Decimal("30.00"),
            }
        ],
    )
    stale_order.notes = "Corrected notes"
    model_admin = OrderAdmin(Order, admin.site)
    with transaction.atomic():
        model_admin.save_model(None, stale_order, None, True)
        model_admin.save_formset(None, None, formset, True)
    stale_order.refresh_from_db()
    item.refresh_from_db()
    assert stale_order.notes == "Corrected notes"
    assert stale_order.total_amount == Decimal("60.00")
    assert item.quantity == Decimal("2.00")
    assert item.unit_price == Decimal("30.00")
    assert item.subtotal == Decimal("60.00")
    assert item.dry_cleaning_status == DryCleaningStatus.SENT
