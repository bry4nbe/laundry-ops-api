from decimal import Decimal

from rest_framework import serializers

from apps.users.models import User

from .models import Payment, PaymentMethod


class PaymentInputSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01")
    )
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices)
    reference_code = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )


class InitialPaymentSerializer(PaymentInputSerializer):
    amount = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0")
    )
    payment_method = serializers.ChoiceField(
        choices=PaymentMethod.choices, required=False
    )

    def validate(self, attrs):
        if attrs["amount"] > 0 and "payment_method" not in attrs:
            raise serializers.ValidationError(
                {"payment_method": "El método es obligatorio para un pago positivo."}
            )
        return attrs


class PaymentActorSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name"]


class PaymentDetailSerializer(serializers.ModelSerializer):
    created_by = PaymentActorSerializer(read_only=True)
    voided_by = PaymentActorSerializer(read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "order",
            "amount",
            "payment_method",
            "reference_code",
            "payment_type",
            "created_at",
            "created_by",
            "voided_at",
            "voided_by",
            "void_reason",
        ]
        read_only_fields = fields
