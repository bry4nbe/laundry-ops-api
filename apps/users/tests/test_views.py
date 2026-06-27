from typing import cast

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.users.models import User

pytestmark = pytest.mark.django_db


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


def test_login_failure_wrong_credentials():
    client = APIClient()
    url = reverse("users:login")

    data = {"username": "fantasma", "password": "hacker123"}

    response = cast(Response, client.post(url, data))
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
