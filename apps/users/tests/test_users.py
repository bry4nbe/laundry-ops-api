import pytest
from django.urls import reverse

from apps.users.models import User

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("manager_method", ["create_user", "create_superuser"])
@pytest.mark.parametrize("email_kwargs", [{}, {"email": ""}, {"email": None}])
def test_multiple_users_can_be_created_without_email(manager_method, email_kwargs):
    create = getattr(User.objects, manager_method)
    users = []
    for username in ("no_email_one", "no_email_two"):
        user = create(username=username, name=username, password=None, **email_kwargs)
        user.refresh_from_db()
        users.append(user)
    for user in users:
        assert user.email is None
        assert user.is_superuser == (manager_method == "create_superuser")
    assert User.objects.count() == 2


def test_user_creation_preserves_nonempty_email():
    user = User.objects.create_user(
        username="with_email", name="Test User", email="staff@example.com"
    )
    user.refresh_from_db()
    assert user.email == "staff@example.com"


def test_blank_email_validation_does_not_conflict_with_legacy_empty_email():
    legacy_user = User.objects.create_user(username="legacy", name="Legacy User")
    User.objects.filter(pk=legacy_user.pk).update(email="")
    user = User.objects.create_user(username="new_user", name="New User")

    user.full_clean()

    assert user.email is None
    legacy_user.refresh_from_db()
    assert legacy_user.email == ""


@pytest.mark.parametrize("query", ["searchable", "Lucia", "staff@example.com"])
def test_admin_can_search_custom_user_fields(admin_client, query):
    user = User.objects.create_user(
        username="searchable", name="Lucia Torres", email="staff@example.com"
    )

    response = admin_client.get(reverse("admin:users_user_changelist"), {"q": query})

    assert response.status_code == 200
    assert list(response.context["cl"].result_list) == [user]


@pytest.mark.parametrize("delete_mode", ["single", "bulk"])
def test_admin_cannot_delete_users_without_business_records(admin_client, delete_mode):
    user = User.objects.create_user(
        username="must_remain", name="Test User", email="staff@example.com"
    )
    if delete_mode == "single":
        response = admin_client.post(
            reverse("admin:users_user_delete", args=[user.pk]), {"post": "yes"}
        )
        assert response.status_code == 403
    else:
        admin_client.post(
            reverse("admin:users_user_changelist"),
            {"action": "delete_selected", "_selected_action": [user.pk], "post": "yes"},
        )
    assert User.objects.filter(pk=user.pk).exists()


def test_admin_can_deactivate_user(admin_client):
    user = User.objects.create_user(
        username="deactivate", name="Test User", email="staff@example.com"
    )
    response = admin_client.post(
        reverse("admin:users_user_change", args=[user.pk]),
        {
            "username": user.username,
            "name": user.name,
            "email": "",
            "role": user.role,
            "date_joined_0": "2026-10-02",
            "date_joined_1": "10:00:00",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    user.refresh_from_db()
    assert not user.is_active
