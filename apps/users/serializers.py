from typing import Any

from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.users.models import User


class UserMeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "name", "email", "role"]
        read_only_fields = fields


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        original_data = super().validate(attrs)

        data: dict[str, Any] = dict(original_data)

        data["user"] = UserMeSerializer(self.user).data

        return data
