from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.catalog.models import CatalogItem, ServiceType
from apps.clients.models import Client
from apps.orders import services as order_services
from apps.orders.models import Order
from apps.payments import services as payment_services
from apps.payments.models import Payment, PaymentMethod, PaymentType
from apps.users.models import User, UserRole

pytestmark = pytest.mark.django_db
LIMA = ZoneInfo("America/Lima")
TODAY = date(2026, 10, 2)
NOON = datetime.combine(TODAY, time(12), LIMA)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda **kwargs: TODAY)


@pytest.fixture
def actor():
    return User.objects.create_user(username="dashboard-admin", role=UserRole.ADMIN)


@pytest.fixture
def api(actor):
    client = APIClient()
    client.force_authenticate(user=actor)
    return client


@pytest.fixture
def customer():
    return Client.objects.create(name="Marco Alca")


@pytest.fixture
def catalog():
    return CatalogItem.objects.create(
        name="Shirt", service_type=ServiceType.PER_GARMENT, base_price=Decimal("10.00")
    )


@pytest.fixture
def make_order(actor, customer, catalog):
    def create(*, when=NOON, quantity="2.00", unit_price="10.00", items=None, **state):
        order = order_services.create_order(
            customer,
            items
            or [
                {
                    "catalog_item": catalog,
                    "quantity": Decimal(quantity),
                    "unit_price": Decimal(unit_price),
                }
            ],
            "",
            actor,
        )
        Order.objects.filter(pk=order.pk).update(created_at=when, **state)
        order.refresh_from_db()
        return order

    return create


@pytest.fixture
def make_payment(actor):
    def create(
        order,
        amount,
        *,
        method=PaymentMethod.CASH,
        payment_type=PaymentType.PARTIAL,
        when=NOON,
        voided=False,
    ):
        payment = Payment.objects.create(
            order=order,
            created_by=actor,
            amount=Decimal(amount),
            payment_method=method,
            payment_type=payment_type,
        )
        Payment.objects.filter(pk=payment.pk).update(created_at=when)
        if voided:
            payment_services.void_payment(
                payment.pk, voided_by=actor, reason="Correction"
            )
        return payment

    return create


@pytest.fixture
def login():
    def authenticate(*, role=UserRole.ADMIN, **flags):
        user = User.objects.create_user(
            username="dashboard-login",
            name="Dashboard User",
            password="testpass123",
            role=role,
            **flags,
        )
        client = APIClient()
        response = client.post(
            reverse("users:login"),
            {"username": user.username, "password": "testpass123"},
            format="json",
        )
        assert response.status_code == 200
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        return client, user, response.data["access"]

    return authenticate


def test_empty_summary_returns_complete_zero_values(api):
    response = api.get(reverse("dashboard-summary"))
    assert response.status_code == 200
    assert response.json() == {
        "date_from": "2026-10-02",
        "date_to": "2026-10-02",
        "orders_created": 0,
        "pending_delivery_count": 0,
        "collected_amount": "0.00",
        "collected_by_method": {"CASH": "0.00", "YAPE_PLIN": "0.00"},
        "outstanding_amount": "0.00",
        "current_month_monitor": {
            "month": "2026-10",
            "collected_amount": "0.00",
            "above_5000": False,
            "above_8000": False,
            "indicative_only": True,
        },
    }


@pytest.mark.parametrize(
    ("role", "flags", "expected"),
    [
        (UserRole.ADMIN, {}, 200),
        (UserRole.OPERATOR, {}, 403),
        (UserRole.OPERATOR, {"is_staff": True}, 403),
        (UserRole.OPERATOR, {"is_staff": True, "is_superuser": True}, 403),
    ],
)
def test_real_jwt_enforces_business_role_and_keeps_pending_orders_accessible(
    login, make_order, role, flags, expected
):
    client, _, _ = login(role=role, **flags)
    order = make_order()
    assert client.get(reverse("dashboard-summary")).status_code == expected
    pending = client.get(
        reverse("order-list-create"), {"delivered": "false", "search": "marco"}
    )
    assert pending.status_code == 200
    assert [row["id"] for row in pending.data["results"]] == [order.pk]


def test_dashboard_requires_jwt():
    client = APIClient()
    assert client.get(reverse("dashboard-summary")).status_code == 401
    client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
    assert client.get(reverse("dashboard-summary")).status_code == 401


def test_expired_jwt_is_rejected(login):
    client, _, access = login()
    token = AccessToken(access)
    token.set_exp(lifetime=timedelta(seconds=-1))
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert client.get(reverse("dashboard-summary")).status_code == 401


def test_deactivated_admin_is_rejected_with_previously_issued_jwt(login):
    client, user, _ = login()
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert client.get(reverse("dashboard-summary")).status_code == 401


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_dashboard_rejects_writes_without_changing_business_data(
    api, make_order, method
):
    make_order()
    before = (Order.objects.count(), Payment.objects.count(), User.objects.count())
    response = getattr(api, method)(reverse("dashboard-summary"), {}, format="json")
    assert response.status_code == 405
    assert (
        Order.objects.count(),
        Payment.objects.count(),
        User.objects.count(),
    ) == before


@pytest.mark.parametrize(
    "filters",
    [
        {"date_from": "2026-10-02"},
        {"date_to": "2026-10-02"},
        {"date_from": "", "date_to": "2026-10-02"},
        {"date_from": "abc", "date_to": "2026-10-02"},
        {"date_from": "2026-02-30", "date_to": "2026-10-02"},
        {"date_from": "2026-10-02", "date_to": "abc"},
        {"date_from": "2026-10-03", "date_to": "2026-10-02"},
        {"date_from": "20261002", "date_to": "2026-10-02"},
        {"date_from": "2026-10-02", "date_to": "9999-12-31"},
    ],
)
def test_invalid_ranges_return_400(api, filters):
    assert api.get(reverse("dashboard-summary"), filters).status_code == 400


def test_summary_counts_orders_without_multiplying_payments_or_items(
    api, make_order, make_payment, catalog
):
    items = [
        {"catalog_item": catalog, "quantity": Decimal("1.00")},
        {"catalog_item": catalog, "quantity": Decimal("1.00")},
    ]
    active = make_order(items=items)
    delivered = make_order(delivered_at=NOON)
    cancelled = make_order(cancelled_at=NOON)
    older = make_order(when=NOON - timedelta(days=2))
    make_payment(active, "5.00", payment_type=PaymentType.ADVANCE)
    make_payment(active, "3.00", method=PaymentMethod.YAPE_PLIN)
    make_payment(active, "4.00", voided=True)
    make_payment(delivered, "10.00", method=PaymentMethod.YAPE_PLIN)
    make_payment(cancelled, "6.00")
    make_payment(older, "7.00")

    data = api.get(reverse("dashboard-summary")).json()
    assert data["orders_created"] == 3
    assert data["pending_delivery_count"] == 2
    assert data["collected_amount"] == "31.00"
    assert data["collected_by_method"] == {"CASH": "18.00", "YAPE_PLIN": "13.00"}
    assert data["outstanding_amount"] == "22.00"
    assert data["current_month_monitor"]["collected_amount"] == "31.00"


def test_period_collections_follow_payment_date_but_debt_uses_all_payments(
    api, make_order, make_payment
):
    historical = NOON - timedelta(days=2)
    order = make_order(when=historical)
    older = make_order(when=historical - timedelta(days=1))
    make_payment(order, "3.00", when=historical)
    make_payment(order, "5.00", when=NOON - timedelta(days=1))
    make_payment(order, "7.00")
    make_payment(order, "2.00", when=historical, voided=True)
    make_payment(older, "4.00", when=historical)

    data = api.get(
        reverse("dashboard-summary"),
        {"date_from": "2026-09-30", "date_to": "2026-09-30"},
    ).json()
    assert data["date_from"] == data["date_to"] == "2026-09-30"
    assert data["orders_created"] == 1
    assert data["pending_delivery_count"] == 2
    assert data["collected_amount"] == "7.00"
    assert data["outstanding_amount"] == "5.00"
    assert data["current_month_monitor"]["month"] == "2026-10"
    assert data["current_month_monitor"]["collected_amount"] == "12.00"


def test_voiding_recalculates_historical_collections_and_current_debt(
    api, actor, make_order, make_payment
):
    historical = NOON - timedelta(days=20)
    order = make_order(when=historical)
    payment = make_payment(order, "12.00", when=historical)
    filters = {"date_from": "2026-09-12", "date_to": "2026-09-12"}
    before = api.get(reverse("dashboard-summary"), filters).json()
    assert before["collected_amount"] == "12.00"
    assert before["outstanding_amount"] == "8.00"

    payment_services.void_payment(payment.pk, voided_by=actor, reason="Correction")
    after = api.get(reverse("dashboard-summary"), filters).json()
    assert after["collected_amount"] == "0.00"
    assert after["outstanding_amount"] == "20.00"
    assert Payment.objects.count() == 1


@pytest.mark.parametrize("day", [TODAY, date(2026, 1, 1), date(2024, 3, 1)])
def test_lima_day_boundaries_include_start_and_exclude_next_midnight(
    api, make_order, make_payment, monkeypatch, day
):
    monkeypatch.setattr(timezone, "localdate", lambda **kwargs: day)
    start = datetime.combine(day, time.min, LIMA)
    next_day = start + timedelta(days=1)
    instants = [
        start - timedelta(microseconds=1),
        start,
        next_day - timedelta(microseconds=1),
        next_day,
    ]
    for amount, instant in enumerate(instants, start=1):
        utc_instant = instant.astimezone(datetime_timezone.utc)
        order = make_order(when=utc_instant)
        make_payment(order, str(amount), when=utc_instant)

    data = api.get(reverse("dashboard-summary")).json()
    assert data["date_from"] == data["date_to"] == day.isoformat()
    assert data["orders_created"] == 2
    assert data["pending_delivery_count"] == 4
    assert data["collected_amount"] == "5.00"
    assert data["outstanding_amount"] == "35.00"


def test_monitor_includes_only_current_month_even_with_empty_custom_period(
    api, make_order, make_payment
):
    start = datetime(2026, 10, 1, tzinfo=LIMA)
    end = datetime(2026, 11, 1, tzinfo=LIMA)
    for amount, instant in enumerate(
        [
            start - timedelta(microseconds=1),
            start,
            end - timedelta(microseconds=1),
            end,
        ],
        start=1,
    ):
        order = make_order()
        make_payment(order, str(amount), when=instant)

    data = api.get(
        reverse("dashboard-summary"),
        {"date_from": "2026-01-01", "date_to": "2026-01-31"},
    ).json()
    assert data["orders_created"] == 0
    assert data["pending_delivery_count"] == 4
    assert data["collected_amount"] == "0.00"
    assert data["outstanding_amount"] == "0.00"
    assert data["current_month_monitor"]["collected_amount"] == "5.00"


@pytest.mark.parametrize(
    ("amount", "above_5000", "above_8000"),
    [
        ("4999.99", False, False),
        ("5000.00", False, False),
        ("5000.01", True, False),
        ("8000.00", True, False),
        ("8000.01", True, True),
    ],
)
def test_monitor_thresholds_are_strictly_greater_than_limits(
    api, make_order, make_payment, amount, above_5000, above_8000
):
    order = make_order(quantity="1.00", unit_price="10000.00")
    make_payment(order, amount)
    monitor = api.get(reverse("dashboard-summary")).json()["current_month_monitor"]
    assert monitor["collected_amount"] == amount
    assert monitor["above_5000"] is above_5000
    assert monitor["above_8000"] is above_8000
    assert monitor["indicative_only"] is True


def test_aggregates_can_exceed_individual_order_and_payment_limits(
    api, make_order, make_payment
):
    for _ in range(3):
        order = make_order(quantity="100.00", unit_price="900000.00")
        make_payment(order, "40000000.00")
    data = api.get(reverse("dashboard-summary")).json()
    assert data["collected_amount"] == "120000000.00"
    assert data["collected_by_method"]["CASH"] == "120000000.00"
    assert data["outstanding_amount"] == "150000000.00"
    assert data["current_month_monitor"]["collected_amount"] == "120000000.00"


@pytest.mark.parametrize("order_count", [0, 1, 25])
def test_summary_query_count_is_constant(
    api, make_order, make_payment, django_assert_num_queries, order_count
):
    for _ in range(order_count):
        order = make_order()
        make_payment(order, "2.00")
        make_payment(order, "3.00", method=PaymentMethod.YAPE_PLIN)
    with django_assert_num_queries(3):
        response = api.get(reverse("dashboard-summary"))
    assert response.status_code == 200
    assert response.data["orders_created"] == order_count
    assert Decimal(response.data["collected_amount"]) == Decimal("5.00") * order_count


def test_real_jwt_order_advance_later_payments_and_delivery_update_dashboard(
    login, customer, catalog
):
    client, _, _ = login()
    created = client.post(
        reverse("order-list-create"),
        {
            "client": customer.pk,
            "items": [{"catalog_item": catalog.pk, "quantity": "3.00"}],
            "payment": {"amount": "12.00", "payment_method": "CASH"},
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert created.status_code == 201
    order_id = created.data["id"]
    Order.objects.filter(pk=order_id).update(created_at=NOON)
    Payment.objects.filter(order_id=order_id).update(created_at=NOON)
    first = client.get(reverse("dashboard-summary")).json()
    assert first["collected_amount"] == "12.00"
    assert first["outstanding_amount"] == "18.00"

    for amount in ("5.00", "13.00"):
        paid = client.post(
            reverse("order-payments", args=[order_id]),
            {"amount": amount, "payment_method": "YAPE_PLIN"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        assert paid.status_code == 201
    assert client.post(reverse("order-deliver", args=[order_id])).status_code == 200
    Payment.objects.filter(order_id=order_id).update(created_at=NOON)
    final = client.get(reverse("dashboard-summary")).json()
    assert final["orders_created"] == 1
    assert final["pending_delivery_count"] == 0
    assert final["collected_amount"] == "30.00"
    assert final["collected_by_method"] == {"CASH": "12.00", "YAPE_PLIN": "18.00"}
    assert final["outstanding_amount"] == "0.00"
    assert list(
        Payment.objects.order_by("pk").values_list("payment_type", flat=True)
    ) == [
        PaymentType.ADVANCE,
        PaymentType.PARTIAL,
        PaymentType.FINAL,
    ]
