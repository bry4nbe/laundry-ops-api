import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from django.contrib import admin
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import Permission
from django.db import (
    IntegrityError,
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import Client as DjangoClient
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.catalog.models import CatalogItem, ServiceType
from apps.clients.models import Client
from apps.orders import selectors
from apps.orders import services as order_services
from apps.orders.models import Order, OrderItem
from apps.payments import services
from apps.payments.models import Payment, PaymentMethod, PaymentType
from apps.users.models import User, UserRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def actor():
    return User.objects.create_user(username="cashier", name="Test Cashier")


@pytest.fixture
def api(actor):
    client = APIClient()
    client.force_authenticate(user=actor)
    return client


@pytest.fixture
def customer():
    return Client.objects.create(name="Test Customer")


@pytest.fixture
def catalog():
    return CatalogItem.objects.create(
        name="Shirt", service_type=ServiceType.PER_GARMENT, base_price=Decimal("10.00")
    )


@pytest.fixture
def order(actor, customer, catalog):
    return order_services.create_order(
        customer, [{"catalog_item": catalog, "quantity": Decimal("2.00")}], "", actor
    )


@pytest.fixture
def management_user():
    return User.objects.create_user(
        username="auditor",
        name="Test Auditor",
        is_staff=True,
        is_superuser=True,
        role=UserRole.ADMIN,
    )


@pytest.fixture
def management_client(management_user):
    client = DjangoClient()
    client.force_login(management_user)
    return client


def payment_url(order):
    return reverse("order-payments", args=[order.pk])


def post_payment(
    api, target_order, amount="5.00", method=PaymentMethod.CASH, key=None, **extra
):
    return api.post(
        payment_url(target_order),
        {"amount": amount, "payment_method": method, **extra},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(key or uuid4()),
    )


@pytest.fixture
def payment(api, order):
    response = post_payment(api, order)
    assert response.status_code == 201
    return Payment.objects.get(pk=response.data["id"])


def initial_order_body(customer, catalog, amount="5.00"):
    return {
        "client": customer.pk,
        "items": [{"catalog_item": catalog.pk, "quantity": "2.00"}],
        "payment": {"amount": amount, "payment_method": PaymentMethod.CASH},
    }


def void_action(client, payments, **extra):
    return client.post(
        reverse("admin:payments_payment_changelist"),
        {
            "action": "void_selected_payment",
            "_selected_action": [payment.pk for payment in payments],
            **extra,
        },
    )


@pytest.mark.parametrize("method", PaymentMethod.values)
def test_partial_and_final_payments_have_real_balances(api, actor, order, method):
    partial = post_payment(api, order, "7.50", method)
    final = post_payment(api, order, "12.50", method, reference_code="REFERENCE-123")
    assert partial.status_code == final.status_code == 201
    assert partial.data["payment_type"] == PaymentType.PARTIAL
    assert final.data["payment_type"] == PaymentType.FINAL
    assert partial.data["amount"] == "7.50"
    assert partial.data["reference_code"] == ""
    assert final.data["reference_code"] == "REFERENCE-123"
    assert final.data["created_by"] == {"id": actor.pk, "name": actor.name}
    assert final.data["voided_by"] is None
    assert final.data["voided_at"] is None
    assert final.data["void_reason"] == ""
    detail = api.get(reverse("order-detail", args=[order.pk]))
    assert detail.data["paid_amount"] == "20.00"
    assert detail.data["balance"] == "0.00"
    assert post_payment(api, order, "0.01").status_code == 400
    assert order.payments.count() == 2


@pytest.mark.parametrize(
    "amount",
    [
        "0",
        "-1",
        "20.01",
        "100000000",
        "0.001",
        "1.000",
        "bad",
        "",
        "NaN",
        "Infinity",
        None,
        True,
        {},
        [],
    ],
)
def test_invalid_amount_does_not_record_or_reserve_key(api, order, amount):
    key = uuid4()
    response = post_payment(api, order, amount, key=key)
    assert response.status_code == 400
    assert not Payment.objects.exists()
    assert post_payment(api, order, "5.00", key=key).status_code == 201


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"amount": "5.00"},
        {"payment_method": "CASH"},
        {"amount": "5.00", "payment_method": "OTHER"},
        {"amount": "5.00", "payment_method": None},
        {"amount": "5.00", "payment_method": "CASH", "reference_code": "x" * 101},
        {"amount": "5.00", "payment_method": "CASH", "reference_code": None},
    ],
)
def test_invalid_payment_fields(api, order, body):
    response = api.post(
        payment_url(order), body, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid4())
    )
    assert response.status_code == 400
    assert not Payment.objects.exists()


@pytest.mark.parametrize("body", [None, [], "payment", True, 1])
def test_payment_body_must_be_a_json_object(api, order, body):
    response = api.post(
        payment_url(order),
        json.dumps(body),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert response.status_code == 400
    assert not Payment.objects.exists()


@pytest.mark.parametrize(
    "body",
    [
        '{"amount":1e999,"payment_method":"CASH"}',
        '{"amount":-1e999,"payment_method":"CASH"}',
        '{"amount":"5.00","payment_method":"CASH","extra":1e999}',
        '{"amount":"5.00","payment_method":"CASH","reference_code":"\\ud800"}',
        '{"amount":"5.00","payment_method":"CASH","extra":"\\ud800"}',
    ],
)
def test_invalid_json_values_are_400_without_reserving_key(api, order, body):
    key = str(uuid4())
    response = api.post(
        payment_url(order),
        body,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert response.status_code == 400
    assert not Payment.objects.exists()
    assert post_payment(api, order, key=key).status_code == 201


def test_reference_accepts_maximum_length(api, order):
    response = post_payment(api, order, reference_code="x" * 100)
    assert response.status_code == 201
    assert response.data["reference_code"] == "x" * 100


def test_cors_allows_idempotency_header_for_separate_frontend(order, settings):
    settings.CORS_ALLOWED_ORIGINS = ["http://localhost:5173"]
    response = APIClient().options(
        payment_url(order),
        HTTP_ORIGIN="http://localhost:5173",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,content-type,idempotency-key",
    )
    assert response.status_code == 200
    assert response["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert "idempotency-key" in response["Access-Control-Allow-Headers"].lower()


@pytest.mark.parametrize("key", [None, "", "not-a-uuid"])
def test_later_payments_require_uuid_key(api, order, key):
    headers = {} if key is None else {"HTTP_IDEMPOTENCY_KEY": key}
    response = api.post(
        payment_url(order),
        {"amount": "5.00", "payment_method": "CASH"},
        format="json",
        **headers,
    )
    assert response.status_code == 400
    assert not Payment.objects.exists()


@pytest.mark.parametrize("role", UserRole.values)
def test_real_jwt_payment_workflow_for_both_roles(customer, catalog, role):
    user = User.objects.create_user(
        username=f"jwt-{role}", name=f"Test {role}", password="testpass123", role=role
    )
    api = APIClient()
    login = api.post(
        reverse("users:login"),
        {"username": user.username, "password": "testpass123"},
        format="json",
    )
    assert login.status_code == 200
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    created = api.post(
        reverse("order-list-create"),
        initial_order_body(customer, catalog),
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert created.status_code == 201
    order = Order.objects.get(pk=created.data["id"])
    later = post_payment(api, order, "15.00", PaymentMethod.YAPE_PLIN)
    assert later.status_code == 201
    history = api.get(payment_url(order))
    assert history.status_code == 200
    assert isinstance(history.data, list)
    assert [row["payment_type"] for row in history.data] == [
        PaymentType.ADVANCE,
        PaymentType.FINAL,
    ]
    assert all(
        row["created_by"] == {"id": user.pk, "name": user.name} for row in history.data
    )


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("token", [None, "invalid", "expired"])
def test_payment_endpoints_require_valid_jwt(order, actor, method, token):
    api = APIClient()
    if token == "expired":
        access = AccessToken.for_user(actor)
        access["exp"] = 0
        token = str(access)
    if token is not None:
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = getattr(api, method)(payment_url(order))
    assert response.status_code == 401
    assert not Payment.objects.exists()


@pytest.mark.parametrize("method", ["get", "post"])
def test_missing_order_returns_404(api, method):
    response = getattr(api, method)(
        reverse("order-payments", args=[999999]),
        {"amount": "1.00", "payment_method": "CASH"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert response.status_code == 404


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_no_edit_delete_or_void_api(api, order, method):
    assert (
        getattr(api, method)(payment_url(order), {}, format="json").status_code == 405
    )


def test_payment_audit_cannot_be_supplied_by_request(
    api, actor, order, management_user
):
    other_order = Order.objects.create(client=order.client, created_by=management_user)
    response = post_payment(
        api,
        order,
        order=other_order.pk,
        payment_type=PaymentType.FINAL,
        created_by=management_user.pk,
        created_at="2000-01-01T00:00:00Z",
        voided_at="2000-01-01T00:00:00Z",
        voided_by=management_user.pk,
        void_reason="Forged",
        idempotency_key=str(uuid4()),
        request_fingerprint="forged",
    )
    assert response.status_code == 201
    recorded = Payment.objects.get(pk=response.data["id"])
    assert recorded.order_id == order.pk
    assert recorded.created_by_id == actor.pk
    assert recorded.created_at.year != 2000
    assert recorded.payment_type == PaymentType.PARTIAL
    assert recorded.voided_at is None
    assert recorded.voided_by_id is None
    assert recorded.void_reason == ""
    assert len(recorded.request_fingerprint) == 64


def test_delivery_allows_debt_and_later_collection(api, order, payment):
    delivered = api.post(reverse("order-deliver", args=[order.pk]))
    assert delivered.status_code == 200
    assert delivered.data["paid_amount"] == "5.00"
    assert delivered.data["balance"] == "15.00"
    final = post_payment(api, order, "15.00")
    assert final.status_code == 201
    assert final.data["payment_type"] == PaymentType.FINAL


def test_admin_cancellation_keeps_income_and_denies_new_payments(
    api, order, payment, management_client
):
    response = management_client.post(
        reverse("admin:orders_order_changelist"),
        {"action": "cancel_orders", "_selected_action": [order.pk]},
    )
    assert response.status_code == 302
    order.refresh_from_db()
    assert order.cancelled_at is not None
    assert post_payment(api, order).status_code == 409
    payment.refresh_from_db()
    assert payment.voided_at is None
    detail = api.get(reverse("order-detail", args=[order.pk]))
    assert detail.data["paid_amount"] == "5.00"
    assert detail.data["balance"] == "15.00"


def test_history_retains_voided_payments_and_orders_ties_by_id(
    api, order, payment, management_user
):
    final = post_payment(api, order, "15.00")
    assert final.status_code == 201
    services.void_payment(
        payment.pk, voided_by=management_user, reason="Duplicate entry"
    )
    Payment.objects.filter(order=order).update(created_at=timezone.now())
    history = api.get(payment_url(order))
    assert [row["id"] for row in history.data] == [payment.pk, final.data["id"]]
    assert history.data[0]["voided_by"] == {
        "id": management_user.pk,
        "name": management_user.name,
    }
    assert history.data[0]["void_reason"] == "Duplicate entry"
    assert history.data[0]["amount"] == "5.00"
    detail = api.get(reverse("order-detail", args=[order.pk]))
    assert detail.data["paid_amount"] == "15.00"
    assert detail.data["balance"] == "5.00"


def test_history_is_unpaginated_and_has_no_per_payment_queries(
    api, order, actor, django_assert_num_queries
):
    Payment.objects.bulk_create(
        [
            Payment(
                order=order,
                amount=Decimal("0.10"),
                payment_method=PaymentMethod.CASH,
                payment_type=PaymentType.PARTIAL,
                created_by=actor,
            )
            for _ in range(25)
        ]
    )
    with django_assert_num_queries(2):
        response = api.get(payment_url(order))
    assert response.status_code == 200
    assert len(response.data) == 25


def test_order_list_aggregates_payments_without_per_order_queries(
    api, actor, customer, catalog, management_user, django_assert_num_queries
):
    for _ in range(3):
        order = order_services.create_order(
            customer,
            [
                {"catalog_item": catalog, "quantity": Decimal("1.00")},
                {"catalog_item": catalog, "quantity": Decimal("1.00")},
            ],
            "",
            actor,
        )
        Payment.objects.create(
            order=order,
            amount=Decimal("5.00"),
            payment_method=PaymentMethod.CASH,
            payment_type=PaymentType.PARTIAL,
            created_by=actor,
        )
        Payment.objects.create(
            order=order,
            amount=Decimal("7.00"),
            payment_method=PaymentMethod.CASH,
            payment_type=PaymentType.PARTIAL,
            created_by=actor,
            voided_at=timezone.now(),
            voided_by=management_user,
            void_reason="Correction",
        )
    with django_assert_num_queries(4):
        response = api.get(reverse("order-list-create"))
    assert response.status_code == 200
    assert len(response.data["results"]) == 3
    assert all(
        row["paid_amount"] == "5.00" and row["balance"] == "15.00"
        for row in response.data["results"]
    )


def test_identical_retry_returns_existing_payment_even_after_cancellation(api, order):
    key = uuid4()
    first = post_payment(api, order, "20.00", key=key)
    order.cancelled_at = timezone.now()
    order.save(update_fields=["cancelled_at"])
    replay = post_payment(api, order, "20.00", key=key)
    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.data == first.data
    assert Payment.objects.count() == 1


def test_fingerprint_uses_canonical_json_key_order(api, order):
    key = str(uuid4())
    first = api.post(
        payment_url(order),
        '{"amount":"5.00","payment_method":"CASH","reference_code":"A"}',
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    replay = api.post(
        payment_url(order),
        '{ "reference_code": "A", "payment_method": "CASH", "amount": "5.00" }',
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.data["id"] == first.data["id"]
    assert Payment.objects.count() == 1


@pytest.mark.parametrize(
    "change", ["amount", "method", "reference", "user", "order", "operation"]
)
def test_key_reuse_with_other_content_or_operation_is_conflict(
    api, actor, order, customer, catalog, change
):
    key = uuid4()
    first = post_payment(api, order, key=key)
    assert first.status_code == 201
    if change == "amount":
        response = post_payment(api, order, "6.00", key=key)
    elif change == "method":
        response = post_payment(api, order, method=PaymentMethod.YAPE_PLIN, key=key)
    elif change == "reference":
        response = post_payment(api, order, key=key, reference_code="different")
    elif change == "user":
        other_user = User.objects.create_user(username="other-cashier")
        api.force_authenticate(user=other_user)
        response = post_payment(api, order, key=key)
    elif change == "order":
        other_order = order_services.create_order(
            customer,
            [{"catalog_item": catalog, "quantity": Decimal("2.00")}],
            "",
            actor,
        )
        response = post_payment(api, other_order, key=key)
    else:
        response = api.post(
            reverse("order-list-create"),
            initial_order_body(customer, catalog),
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(key),
        )
    assert response.status_code == 409
    assert Payment.objects.count() == 1


def test_retry_of_voided_payment_returns_voided_record(api, order, management_user):
    key = uuid4()
    first = post_payment(api, order, key=key)
    services.void_payment(
        first.data["id"], voided_by=management_user, reason="Correction"
    )
    replay = post_payment(api, order, key=key)
    assert replay.status_code == 200
    assert replay.data["id"] == first.data["id"]
    assert replay.data["voided_at"] is not None
    assert replay.data["void_reason"] == "Correction"
    assert Payment.objects.count() == 1
    assert selectors.order_queryset().get(pk=order.pk).paid_amount == Decimal("0.00")


def test_initial_order_retry_does_not_create_another_order(
    api, customer, catalog, management_user
):
    key = uuid4()
    body = initial_order_body(customer, catalog)
    first = api.post(
        reverse("order-list-create"), body, format="json", HTTP_IDEMPOTENCY_KEY=str(key)
    )
    assert first.status_code == 201
    payment = Payment.objects.get()
    services.void_payment(payment.pk, voided_by=management_user, reason="Correction")
    order = payment.order
    replacement = CatalogItem.objects.create(
        name="Replacement",
        service_type=ServiceType.PER_GARMENT,
        base_price=Decimal("10.00"),
    )
    order_services.update_order(
        order, items_data=[{"catalog_item": replacement, "quantity": Decimal("3.00")}]
    )
    catalog.delete()
    replay = api.post(
        reverse("order-list-create"), body, format="json", HTTP_IDEMPOTENCY_KEY=str(key)
    )
    assert replay.status_code == 200
    assert replay.data["id"] == first.data["id"]
    assert replay.data["paid_amount"] == "0.00"
    assert replay.data["balance"] == "30.00"
    assert Order.objects.count() == Payment.objects.count() == 1
    assert Payment.objects.get().voided_at is not None


def test_orders_without_advance_are_not_idempotent(api, customer, catalog):
    key = str(uuid4())
    body = initial_order_body(customer, catalog)
    body["payment"] = None
    first = api.post(
        reverse("order-list-create"), body, format="json", HTTP_IDEMPOTENCY_KEY=key
    )
    second = api.post(
        reverse("order-list-create"), body, format="json", HTTP_IDEMPOTENCY_KEY=key
    )
    assert first.status_code == second.status_code == 201
    assert first.data["id"] != second.data["id"]
    assert Order.objects.count() == 2
    assert not Payment.objects.exists()


@pytest.mark.parametrize("change", ["amount", "notes", "items", "user", "operation"])
def test_initial_order_key_cannot_be_reused(api, customer, catalog, change):
    key = uuid4()
    body = initial_order_body(customer, catalog)
    first = api.post(
        reverse("order-list-create"), body, format="json", HTTP_IDEMPOTENCY_KEY=str(key)
    )
    assert first.status_code == 201
    if change == "amount":
        body["payment"]["amount"] = "6.00"
    elif change == "notes":
        body["notes"] = "Changed notes"
    elif change == "items":
        body["items"][0]["quantity"] = "3.00"
    elif change == "user":
        api.force_authenticate(user=User.objects.create_user(username="another"))
    else:
        response = post_payment(api, Order.objects.get(pk=first.data["id"]), key=key)
        assert response.status_code == 409
        return
    response = api.post(
        reverse("order-list-create"), body, format="json", HTTP_IDEMPOTENCY_KEY=str(key)
    )
    assert response.status_code == 409
    assert Order.objects.count() == Payment.objects.count() == 1


def test_void_action_requires_confirmation_and_preserves_original_data(
    management_client, management_user, payment, api, order
):
    original = (
        payment.amount,
        payment.payment_method,
        payment.payment_type,
        payment.created_by_id,
        payment.created_at,
        payment.reference_code,
        payment.idempotency_key,
        payment.request_fingerprint,
    )
    confirmation = void_action(management_client, [payment])
    assert confirmation.status_code == 200
    assert "Confirmar anulación" in confirmation.content.decode()
    payment.refresh_from_db()
    assert payment.voided_at is None
    done = void_action(
        management_client,
        [payment],
        confirm_void="Confirm",
        reason="  Mistaken entry  ",
    )
    assert done.status_code == 302
    payment.refresh_from_db()
    assert payment.voided_by_id == management_user.pk
    assert payment.voided_at is not None
    assert payment.void_reason == "Mistaken entry"
    assert original == (
        payment.amount,
        payment.payment_method,
        payment.payment_type,
        payment.created_by_id,
        payment.created_at,
        payment.reference_code,
        payment.idempotency_key,
        payment.request_fingerprint,
    )
    assert LogEntry.objects.filter(
        user=management_user, object_id=str(payment.pk), action_flag=CHANGE
    ).exists()
    assert api.get(payment_url(order)).data[0]["voided_at"] is not None


@pytest.mark.parametrize("reason", ["", "   ", "x" * 256])
def test_void_action_requires_valid_reason(management_client, payment, reason):
    response = void_action(
        management_client, [payment], confirm_void="Confirm", reason=reason
    )
    assert response.status_code == 200
    assert response.context["form"].errors
    payment.refresh_from_db()
    assert payment.voided_at is None


def test_void_action_does_not_accept_multiple_payments(
    management_client, payment, api, order
):
    second = Payment.objects.get(pk=post_payment(api, order).data["id"])
    response = void_action(
        management_client,
        [payment, second],
        confirm_void="Confirm",
        reason="Correction",
    )
    assert response.status_code == 302
    assert not Payment.objects.filter(voided_at__isnull=False).exists()


def test_void_action_cannot_repeat_or_reactivate_payment(management_client, payment):
    first = void_action(
        management_client, [payment], confirm_void="Confirm", reason="Original reason"
    )
    assert first.status_code == 302
    payment.refresh_from_db()
    timestamp = payment.voided_at
    repeated = void_action(
        management_client, [payment], confirm_void="Confirm", reason="Changed reason"
    )
    assert repeated.status_code == 302
    payment.refresh_from_db()
    assert payment.voided_at == timestamp
    assert payment.void_reason == "Original reason"
    assert Payment.objects.count() == 1


@pytest.mark.parametrize("can_void", [False, True])
def test_void_uses_native_change_permission_without_generic_editing(payment, can_void):
    staff = User.objects.create_user(username="payment-viewer", is_staff=True)
    permission = Permission.objects.get(
        content_type__app_label="payments",
        codename="change_payment" if can_void else "view_payment",
    )
    staff.user_permissions.add(permission)
    client = DjangoClient()
    client.force_login(staff)
    listing = client.get(reverse("admin:payments_payment_changelist"))
    assert listing.status_code == 200
    if can_void:
        assert "void_selected_payment" in dict(
            listing.context["action_form"].fields["action"].choices
        )
    else:
        assert listing.context["action_form"] is None
    response = void_action(
        client, [payment], confirm_void="Confirm", reason="Correction"
    )
    assert response.status_code == (302 if can_void else 200)
    payment.refresh_from_db()
    assert (payment.voided_at is not None) == can_void
    assert (
        client.post(
            reverse("admin:payments_payment_change", args=[payment.pk]),
            {"amount": "1.00"},
        ).status_code
        == 403
    )
    assert (
        client.get(
            reverse("admin:payments_payment_change", args=[payment.pk])
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "operation,method",
    [
        ("add", "get"),
        ("add", "post"),
        ("change", "post"),
        ("delete", "get"),
        ("delete", "post"),
    ],
)
def test_payment_admin_rejects_add_edit_delete(
    management_client, payment, operation, method
):
    args = [] if operation == "add" else [payment.pk]
    response = getattr(management_client, method)(
        reverse(f"admin:payments_payment_{operation}", args=args),
        {"amount": "1.00", "voided_at": ""},
    )
    assert response.status_code == 403
    payment.refresh_from_db()
    assert payment.amount == Decimal("5.00")
    assert Payment.objects.count() == 1
    assert "delete_selected" not in admin.site._registry[Payment].actions


@pytest.mark.parametrize("voided", [False, True])
def test_deleting_order_cannot_remove_payment_history(
    order, payment, management_user, voided
):
    if voided:
        services.void_payment(
            payment.pk, voided_by=management_user, reason="Correction"
        )
    with pytest.raises(ProtectedError):
        order.delete()
    assert Order.objects.filter(pk=order.pk).exists()
    assert Payment.objects.filter(pk=payment.pk).exists()


def test_payment_actors_are_protected(order, management_user):
    creator = User.objects.create_user(username="payment-only-creator")
    payment = Payment.objects.create(
        order=order,
        created_by=creator,
        amount=Decimal("1.00"),
        payment_method=PaymentMethod.CASH,
        payment_type=PaymentType.PARTIAL,
    )
    services.void_payment(payment.pk, voided_by=management_user, reason="Correction")
    for user in [creator, management_user]:
        with pytest.raises(ProtectedError):
            user.delete()


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-0.01")])
def test_database_enforces_positive_payment_amount(order, actor, amount):
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(
            order=order,
            created_by=actor,
            amount=amount,
            payment_method=PaymentMethod.CASH,
            payment_type=PaymentType.PARTIAL,
        )
    assert not Payment.objects.exists()


def concurrent_requests(actor, calls):
    start = Barrier(len(calls))

    def worker(call):
        close_old_connections()
        try:
            api = APIClient()
            api.force_authenticate(user=actor)
            start.wait(timeout=15)
            response = call(api)
            return response.status_code, response.data
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        futures = [executor.submit(worker, call) for call in calls]
        return [future.result(timeout=30) for future in futures]


@pytest.mark.django_db(transaction=True)
def test_concurrent_payments_cannot_overpay(actor, order):
    results = concurrent_requests(
        actor,
        [
            lambda api: post_payment(api, order, "15.00"),
            lambda api: post_payment(api, order, "15.00"),
        ],
    )
    assert sorted(code for code, _ in results) == [201, 400]
    assert Payment.objects.count() == 1
    refreshed = selectors.order_queryset().get(pk=order.pk)
    assert refreshed.paid_amount == Decimal("15.00")
    assert refreshed.balance == Decimal("5.00")


@pytest.mark.django_db(transaction=True)
def test_concurrent_identical_later_payments_have_one_record(actor, order):
    key = uuid4()
    results = concurrent_requests(
        actor,
        [
            lambda api: post_payment(api, order, "20.00", key=key),
            lambda api: post_payment(api, order, "20.00", key=key),
        ],
    )
    assert sorted(code for code, _ in results) == [200, 201]
    assert results[0][1]["id"] == results[1][1]["id"]
    assert Payment.objects.count() == 1
    assert selectors.order_queryset().get(pk=order.pk).balance == Decimal("0.00")


@pytest.mark.django_db(transaction=True)
def test_concurrent_initial_retry_rolls_back_duplicate_order(
    actor, customer, catalog, monkeypatch
):
    key = str(uuid4())
    body = initial_order_body(customer, catalog)
    created_orders = Barrier(2)
    original_create = order_services.create_order

    def simultaneous_create(*args, **kwargs):
        order = original_create(*args, **kwargs)
        created_orders.wait(timeout=15)
        return order

    monkeypatch.setattr(order_services, "create_order", simultaneous_create)
    results = concurrent_requests(
        actor,
        [
            lambda api: api.post(
                reverse("order-list-create"),
                body,
                format="json",
                HTTP_IDEMPOTENCY_KEY=key,
            ),
            lambda api: api.post(
                reverse("order-list-create"),
                body,
                format="json",
                HTTP_IDEMPOTENCY_KEY=key,
            ),
        ],
    )
    assert sorted(code for code, _ in results) == [200, 201]
    assert results[0][1]["id"] == results[1][1]["id"]
    assert (
        Order.objects.count()
        == Payment.objects.count()
        == OrderItem.objects.count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_reuse_on_other_order_returns_conflict(
    actor, order, customer, catalog, monkeypatch
):
    other_order = order_services.create_order(
        customer, [{"catalog_item": catalog, "quantity": Decimal("2.00")}], "", actor
    )
    key = uuid4()
    insertions = Barrier(2)
    original_create = services.create_payment_for_locked_order

    def simultaneous_create(*args, **kwargs):
        insertions.wait(timeout=15)
        return original_create(*args, **kwargs)

    monkeypatch.setattr(
        services, "create_payment_for_locked_order", simultaneous_create
    )
    results = concurrent_requests(
        actor,
        [
            lambda api: post_payment(api, order, key=key),
            lambda api: post_payment(api, other_order, key=key),
        ],
    )
    assert sorted(code for code, _ in results) == [201, 409]
    assert Payment.objects.count() == 1
    assert Order.objects.count() == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_economic_edit_and_payment_keep_consistent_balance(
    actor, order, catalog
):
    results = concurrent_requests(
        actor,
        [
            lambda api: post_payment(api, order, "15.00"),
            lambda api: api.patch(
                reverse("order-detail", args=[order.pk]),
                {"items": [{"catalog_item": catalog.pk, "quantity": "1.00"}]},
                format="json",
            ),
        ],
    )
    refreshed = selectors.order_queryset().get(pk=order.pk)
    if results[0][0] == 201:
        assert results[1][0] == 409
        assert refreshed.total_amount == Decimal("20.00")
        assert refreshed.paid_amount == Decimal("15.00")
    else:
        assert results[0][0] == 400
        assert results[1][0] == 200
        assert refreshed.total_amount == Decimal("10.00")
        assert refreshed.paid_amount == Decimal("0.00")
    assert refreshed.balance >= 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "amount,method,valid",
    [
        ("5.00", "CASH", True),
        ("0.00", "CASH", False),
        ("-1.00", "CASH", False),
        ("5.00", "OTHER", False),
    ],
)
def test_incremental_migration_preserves_valid_data_and_stops_on_invalid_rows(
    actor, order, amount, method, valid
):
    old_target = [("payments", "0001_initial")]
    new_target = [("payments", "0002_payment_integrity_and_voiding")]
    executor = MigrationExecutor(connection)
    executor.migrate(old_target)
    old_payment = executor.loader.project_state(old_target).apps.get_model(
        "payments", "Payment"
    )
    record = old_payment.objects.create(
        order_id=order.pk,
        created_by_id=actor.pk,
        amount=Decimal(amount),
        payment_method=method,
        payment_type="PARTIAL",
    )
    try:
        if valid:
            MigrationExecutor(connection).migrate(new_target)
            migrated = Payment.objects.get(pk=record.pk)
            assert migrated.amount == Decimal(amount)
            assert migrated.idempotency_key is None
            assert migrated.request_fingerprint == ""
            assert migrated.voided_at is None
            assert migrated.voided_by_id is None
            assert migrated.void_reason == ""
        else:
            with pytest.raises(RuntimeError, match="Payments migration stopped"):
                MigrationExecutor(connection).migrate(new_target)
            assert old_payment.objects.get(pk=record.pk).payment_method == method
            assert old_payment.objects.count() == 1
            assert Order.objects.filter(pk=order.pk).exists()
    finally:
        if not valid:
            old_payment.objects.filter(pk=record.pk).delete()
        MigrationExecutor(connection).migrate(new_target)
