import json
from datetime import timedelta
from typing import cast

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_session():
    user = User.objects.create_user(
        username="operator",
        name="Test Operator",
        password="testpass123",
        email=None,
    )
    api = APIClient()
    response = api.post(
        reverse("users:login"),
        {"username": user.username, "password": "testpass123"},
        format="json",
    )
    assert response.status_code == 200
    return api, user, response.data


def test_login_success():
    client = APIClient()
    User.objects.create_user(
        username="cajero1",
        name="Juan Cajero",
        password="password123",
        role="OPERATOR",
    )
    url = reverse("users:login")

    data = {"username": "cajero1", "password": "password123"}

    response = cast(Response, client.post(url, data))

    assert response.status_code == status.HTTP_200_OK
    assert response.data is not None
    assert response.data["user"]["username"] == "cajero1"
    assert response.data["access"]
    assert response.data["refresh"]
    assert "password" not in response.data["user"]


def test_login_failure_wrong_credentials():
    client = APIClient()
    url = reverse("users:login")

    data = {"username": "fantasma", "password": "hacker123"}

    response = cast(Response, client.post(url, data))
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_login_rejects_wrong_password_for_existing_user(auth_session):
    api, user, _ = auth_session
    response = api.post(
        reverse("users:login"),
        {"username": user.username, "password": "wrong-password"},
        format="json",
    )
    assert response.status_code == 401


def test_me_returns_current_user_with_real_access_token(auth_session):
    api, user, tokens = auth_session
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    user.role = "ADMIN"
    user.save(update_fields=["role"])

    response = api.get(reverse("users:me"))

    assert response.status_code == 200
    assert response.data["id"] == user.id
    assert response.data["role"] == "ADMIN"
    assert "password" not in response.data


@pytest.mark.parametrize(
    "token_kind", ["missing", "malformed", "tampered", "expired", "refresh"]
)
def test_me_rejects_invalid_access_tokens(auth_session, token_kind):
    api, _, tokens = auth_session
    if token_kind == "missing":
        token = None
    elif token_kind == "malformed":
        token = "not-a-jwt"
    elif token_kind == "tampered":
        payload, signature = tokens["access"].rsplit(".", 1)
        replacement = "A" if signature[0] != "A" else "B"
        token = f"{payload}.{replacement}{signature[1:]}"
    elif token_kind == "expired":
        token = AccessToken(tokens["access"])
        token.set_exp(lifetime=timedelta(seconds=-1))
    else:
        token = tokens["refresh"]
    if token is not None:
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = api.get(reverse("users:me"))

    assert response.status_code == 401


def test_refresh_rotates_tokens_and_rejects_previous_refresh(auth_session):
    api, user, tokens = auth_session
    response = api.post(
        reverse("users:token_refresh"), {"refresh": tokens["refresh"]}, format="json"
    )
    assert response.status_code == 200
    assert response.data["access"] != tokens["access"]
    assert response.data["refresh"] != tokens["refresh"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    me = api.get(reverse("users:me"))
    assert me.status_code == 200
    assert me.data["id"] == user.id

    replay = api.post(
        reverse("users:token_refresh"), {"refresh": tokens["refresh"]}, format="json"
    )
    assert replay.status_code == 401


def test_logout_revokes_refresh_but_does_not_revoke_unexpired_access(auth_session):
    api, _, tokens = auth_session
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    response = api.post(
        reverse("users:logout"), {"refresh": tokens["refresh"]}, format="json"
    )

    assert response.status_code == 204
    refresh = api.post(
        reverse("users:token_refresh"), {"refresh": tokens["refresh"]}, format="json"
    )
    assert refresh.status_code == 401
    assert api.get(reverse("users:me")).status_code == 200


@pytest.mark.parametrize(
    ("access_present", "refresh_value", "expected_status"),
    [(False, "valid", 401), (True, None, 400), (True, "invalid", 400)],
)
def test_logout_requires_valid_access_and_refresh(
    auth_session, access_present, refresh_value, expected_status
):
    api, _, tokens = auth_session
    if access_present:
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    data = {}
    if refresh_value is not None:
        data["refresh"] = tokens["refresh"] if refresh_value == "valid" else "not-a-jwt"

    response = api.post(reverse("users:logout"), data, format="json")

    assert response.status_code == expected_status


def test_inactive_user_cannot_login_use_access_or_refresh(auth_session):
    api, user, tokens = auth_session
    user.is_active = False
    user.save(update_fields=["is_active"])
    login = api.post(
        reverse("users:login"),
        {"username": user.username, "password": "testpass123"},
        format="json",
    )
    assert login.status_code == 401
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert api.get(reverse("users:me")).status_code == 401
    refresh = api.post(
        reverse("users:token_refresh"), {"refresh": tokens["refresh"]}, format="json"
    )
    assert refresh.status_code == 401


@pytest.mark.parametrize("body", [[], None, "invalid", 123, True])
def test_logout_rejects_non_object_json_without_revoking_refresh(auth_session, body):
    api, _, tokens = auth_session
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    response = api.generic(
        "POST",
        reverse("users:logout"),
        data=json.dumps(body),
        content_type="application/json",
    )

    assert response.status_code == 400
    refresh = api.post(
        reverse("users:token_refresh"), {"refresh": tokens["refresh"]}, format="json"
    )
    assert refresh.status_code == 200


def test_refresh_rejects_token_for_deleted_user(auth_session):
    api, user, tokens = auth_session
    user.delete()

    response = api.post(
        reverse("users:token_refresh"), {"refresh": tokens["refresh"]}, format="json"
    )

    assert response.status_code == 401
