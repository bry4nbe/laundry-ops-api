from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client as DjangoClient
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.expenses.models import Expense, ExpenseCategory
from apps.users.models import User, UserRole

pytestmark = pytest.mark.django_db


@pytest.fixture
def actor():
    return User.objects.create_user(
        username="expense-admin", name="Expense Admin", role=UserRole.ADMIN
    )


def jwt_client(user):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return client


@pytest.fixture
def api(actor):
    return jwt_client(actor)


@pytest.fixture
def expense(actor):
    return Expense.objects.create(
        amount=Decimal("25.40"),
        concept="Detergent",
        category=ExpenseCategory.SUPPLIES,
        date=date(2026, 10, 2),
        receipt_number="B001-123",
        created_by=actor,
    )


def expense_body(**changes):
    return {
        "amount": "25.40",
        "concept": "Detergent",
        "category": ExpenseCategory.SUPPLIES,
        "date": "2026-10-02",
        **changes,
    }


def detail_url(expense):
    return reverse("expense-detail", args=[expense.pk])


def management_client(*, role=UserRole.ADMIN, permission=None, superuser=False):
    user = User.objects.create_user(
        username="expense-manager",
        name="Expense Manager",
        role=role,
        is_staff=True,
        is_superuser=superuser,
    )
    if permission:
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="expenses", codename=permission
            )
        )
    client = DjangoClient()
    client.force_login(user)
    return user, client


def test_login_jwt_can_create_list_and_retrieve_expenses(actor):
    actor.set_password("testpass123")
    actor.save(update_fields=["password"])
    client = APIClient()
    login = client.post(
        reverse("users:login"),
        {"username": actor.username, "password": "testpass123"},
        format="json",
    )
    assert login.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
    created = client.post(reverse("expense-list"), expense_body(), format="json")
    assert created.status_code == 201
    assert created.data == {
        "id": created.data["id"],
        "amount": "25.40",
        "concept": "Detergent",
        "category": "SUPPLIES",
        "date": "2026-10-02",
        "receipt_number": None,
        "created_by": {"id": actor.pk, "name": actor.name},
    }
    listing = client.get(reverse("expense-list"))
    assert listing.status_code == 200
    assert listing.data["count"] == 1
    assert listing.data["results"] == [created.data]
    detail = client.get(reverse("expense-detail", args=[created.data["id"]]))
    assert detail.status_code == 200
    assert detail.data == created.data


@pytest.mark.parametrize("superuser", [False, True])
def test_operator_cannot_access_expenses_even_with_django_admin_flags(
    expense, superuser
):
    operator = User.objects.create_user(
        username="expense-operator",
        role=UserRole.OPERATOR,
        is_staff=superuser,
        is_superuser=superuser,
    )
    client = jwt_client(operator)
    assert client.get(reverse("expense-list")).status_code == 403
    assert client.get(detail_url(expense)).status_code == 403
    assert (
        client.post(reverse("expense-list"), expense_body(), format="json").status_code
        == 403
    )
    assert Expense.objects.count() == 1


def test_missing_invalid_expired_and_inactive_jwt_are_rejected(actor, expense):
    client = APIClient()
    assert client.get(reverse("expense-list")).status_code == 401
    assert client.get(detail_url(expense)).status_code == 401
    assert (
        client.post(reverse("expense-list"), expense_body(), format="json").status_code
        == 401
    )
    client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")
    assert client.get(reverse("expense-list")).status_code == 401
    token = AccessToken.for_user(actor)
    token.set_exp(lifetime=-timedelta(seconds=1))
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert client.get(reverse("expense-list")).status_code == 401
    client = jwt_client(actor)
    actor.is_active = False
    actor.save(update_fields=["is_active"])
    assert client.get(reverse("expense-list")).status_code == 401
    assert Expense.objects.count() == 1


@pytest.mark.parametrize("category", ExpenseCategory.values)
def test_all_expense_categories_are_supported(api, category):
    response = api.post(
        reverse("expense-list"), expense_body(category=category), format="json"
    )
    assert response.status_code == 201
    assert response.data["category"] == category


@pytest.mark.parametrize("amount", ["0.01", "25.40", "99999999.99"])
def test_positive_amounts_are_exact_and_return_two_decimals(api, amount):
    response = api.post(
        reverse("expense-list"), expense_body(amount=amount), format="json"
    )
    assert response.status_code == 201
    assert response.data["amount"] == amount
    assert Expense.objects.get(pk=response.data["id"]).amount == Decimal(amount)


@pytest.mark.parametrize(
    "amount",
    [
        "0",
        "-1",
        "0.001",
        "1.000",
        "100000000",
        "bad",
        "",
        "NaN",
        "Infinity",
        True,
        None,
        [],
    ],
)
def test_invalid_amounts_do_not_create_expenses(api, amount):
    response = api.post(
        reverse("expense-list"), expense_body(amount=amount), format="json"
    )
    assert response.status_code == 400
    assert "amount" in response.data
    assert not Expense.objects.exists()


@pytest.mark.parametrize("field", ["amount", "concept", "category", "date"])
def test_expense_fields_are_required(api, field):
    body = expense_body()
    body.pop(field)
    response = api.post(reverse("expense-list"), body, format="json")
    assert response.status_code == 400
    assert field in response.data
    assert not Expense.objects.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("concept", "   "),
        ("concept", "x" * 256),
        ("category", "INVALID"),
        ("date", "2026-02-30"),
        ("date", "not-a-date"),
        ("receipt_number", "x" * 101),
    ],
)
def test_invalid_expense_metadata_is_rejected(api, field, value):
    response = api.post(
        reverse("expense-list"), expense_body(**{field: value}), format="json"
    )
    assert response.status_code == 400
    assert field in response.data
    assert not Expense.objects.exists()


@pytest.mark.parametrize("receipt", [None, "", "   ", " B001-123 "])
def test_optional_receipt_is_normalized(api, receipt):
    response = api.post(
        reverse("expense-list"), expense_body(receipt_number=receipt), format="json"
    )
    assert response.status_code == 201
    expected = receipt.strip() or None if receipt is not None else None
    assert response.data["receipt_number"] == expected
    assert Expense.objects.get(pk=response.data["id"]).receipt_number == expected


def test_request_cannot_forge_id_or_creator(api, actor):
    other = User.objects.create_user(username="another-admin", role=UserRole.ADMIN)
    response = api.post(
        reverse("expense-list"),
        expense_body(id=99999, created_by={"id": other.pk, "name": other.name}),
        format="json",
    )
    assert response.status_code == 201
    assert response.data["id"] != 99999
    assert response.data["created_by"] == {"id": actor.pk, "name": actor.name}
    assert Expense.objects.get(pk=response.data["id"]).created_by_id == actor.pk


def test_list_pagination_order_and_queries_are_constant(
    api, actor, expense, django_assert_num_queries
):
    with django_assert_num_queries(3):
        first = api.get(reverse("expense-list"))
    Expense.objects.bulk_create(
        [
            Expense(
                amount=Decimal("1.00"),
                concept=f"Expense {index}",
                category=ExpenseCategory.OTHER,
                date=date(2026, 10, 3),
                created_by=actor,
            )
            for index in range(20)
        ]
    )
    with django_assert_num_queries(3):
        first = api.get(reverse("expense-list"))
    assert first.status_code == 200
    assert first.data["count"] == 21
    assert len(first.data["results"]) == 20
    ids = [item["id"] for item in first.data["results"]]
    assert ids == sorted(ids, reverse=True)
    assert expense.pk not in ids
    assert first.data["next"] is not None
    second = api.get(reverse("expense-list"), {"page": 2})
    assert [item["id"] for item in second.data["results"]] == [expense.pk]


def test_empty_list_and_missing_detail(api):
    response = api.get(reverse("expense-list"))
    assert response.status_code == 200
    assert response.data == {"count": 0, "next": None, "previous": None, "results": []}
    assert api.get(reverse("expense-detail", args=[99999])).status_code == 404


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_api_disallows_corrections_and_deletion(api, expense, method):
    for url in [reverse("expense-list"), detail_url(expense)]:
        assert (
            getattr(api, method)(
                url, expense_body(amount="1.00"), format="json"
            ).status_code
            == 405
        )
    assert (
        api.post(detail_url(expense), expense_body(), format="json").status_code == 405
    )
    expense.refresh_from_db()
    assert expense.amount == Decimal("25.40")
    assert Expense.objects.count() == 1


def test_admin_correction_preserves_creator_and_records_history(actor, expense):
    manager, client = management_client(permission="change_expense")
    response = client.post(
        reverse("admin:expenses_expense_change", args=[expense.pk]),
        expense_body(
            amount="30.00",
            concept="Corrected detergent",
            receipt_number="B001-124",
            created_by=manager.pk,
        ),
    )
    assert response.status_code == 302
    expense.refresh_from_db()
    assert expense.amount == Decimal("30.00")
    assert expense.concept == "Corrected detergent"
    assert expense.receipt_number == "B001-124"
    assert expense.created_by_id == actor.pk
    entry = LogEntry.objects.get(
        user=manager,
        object_id=str(expense.pk),
        action_flag=CHANGE,
        content_type__app_label="expenses",
    )
    assert "amount" in entry.change_message.lower()
    assert (
        client.get(
            reverse("admin:expenses_expense_history", args=[expense.pk])
        ).status_code
        == 200
    )
    assert Expense.objects.count() == 1


def test_admin_view_permission_does_not_allow_corrections(expense):
    _, client = management_client(permission="view_expense")
    url = reverse("admin:expenses_expense_change", args=[expense.pk])
    assert client.get(reverse("admin:expenses_expense_changelist")).status_code == 200
    assert client.get(url).status_code == 200
    assert client.post(url, expense_body(amount="1.00")).status_code == 403
    expense.refresh_from_db()
    assert expense.amount == Decimal("25.40")


def test_admin_rejects_operator_even_when_superuser(expense):
    _, client = management_client(role=UserRole.OPERATOR, superuser=True)
    assert client.get(reverse("admin:expenses_expense_changelist")).status_code == 403
    url = reverse("admin:expenses_expense_change", args=[expense.pk])
    assert client.get(url).status_code == 403
    assert client.post(url, expense_body(amount="1.00")).status_code == 403
    expense.refresh_from_db()
    assert expense.amount == Decimal("25.40")


def test_admin_requires_native_permissions_in_addition_to_business_role(expense):
    _, client = management_client()
    assert client.get(reverse("admin:expenses_expense_changelist")).status_code == 403
    assert (
        client.post(
            reverse("admin:expenses_expense_change", args=[expense.pk]), expense_body()
        ).status_code
        == 403
    )


@pytest.mark.parametrize("operation", ["add", "delete"])
def test_admin_disallows_creation_and_individual_deletion(expense, operation):
    _, client = management_client(superuser=True)
    url = reverse(
        f"admin:expenses_expense_{operation}",
        args=[] if operation == "add" else [expense.pk],
    )
    assert client.get(url).status_code == 403
    assert client.post(url, {"post": "yes", **expense_body()}).status_code == 403
    assert Expense.objects.count() == 1


def test_admin_does_not_expose_or_execute_bulk_deletion(expense):
    _, client = management_client(superuser=True)
    url = reverse("admin:expenses_expense_changelist")
    listing = client.get(url)
    assert listing.status_code == 200
    assert listing.context["action_form"] is None
    client.post(
        url,
        {"action": "delete_selected", "_selected_action": [expense.pk], "post": "yes"},
    )
    assert Expense.objects.filter(pk=expense.pk).exists()


@pytest.mark.parametrize("amount", ["0", "-1", "1.001"])
def test_admin_validates_amount_before_saving(expense, amount):
    _, client = management_client(permission="change_expense")
    response = client.post(
        reverse("admin:expenses_expense_change", args=[expense.pk]),
        expense_body(amount=amount),
    )
    assert response.status_code == 200
    assert "amount" in response.context["adminform"].form.errors
    expense.refresh_from_db()
    assert expense.amount == Decimal("25.40")
    assert not LogEntry.objects.filter(content_type__app_label="expenses").exists()


@pytest.mark.parametrize("amount", [Decimal("0.00"), Decimal("-0.01")])
def test_database_rejects_nonpositive_amounts(actor, amount):
    with pytest.raises(IntegrityError), transaction.atomic():
        Expense.objects.create(
            amount=amount,
            concept="Invalid expense",
            category=ExpenseCategory.OTHER,
            date=date(2026, 10, 2),
            created_by=actor,
        )
    assert not Expense.objects.exists()


def test_expense_creator_cannot_be_deleted(actor, expense):
    with pytest.raises(ProtectedError):
        actor.delete()
    assert User.objects.filter(pk=actor.pk).exists()
    assert Expense.objects.filter(pk=expense.pk).exists()
