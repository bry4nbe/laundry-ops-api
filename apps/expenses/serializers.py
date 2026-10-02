from rest_framework import serializers

from apps.users.models import User

from .models import Expense


class ExpenseActorSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name"]


class ExpenseSerializer(serializers.ModelSerializer):
    created_by = ExpenseActorSerializer(read_only=True)
    receipt_number = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        allow_null=True,
        default=None,
    )

    def validate_receipt_number(self, value):
        return value or None

    class Meta:
        model = Expense
        fields = [
            "id",
            "amount",
            "concept",
            "category",
            "date",
            "receipt_number",
            "created_by",
        ]
        read_only_fields = ["id", "created_by"]
