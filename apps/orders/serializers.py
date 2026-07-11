from decimal import Decimal

from rest_framework import serializers

from apps.catalog.models import CatalogItem
from apps.clients.models import Client

from . import services
from .models import Order, OrderItem


class ClientMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ["id", "name", "phone_number"]


class OrderItemDetailSerializer(serializers.ModelSerializer):
    catalog_item_name = serializers.CharField(source="catalog_item.name", read_only=True)

    class Meta:
        model = OrderItem
        fields = [
            "id",
            "catalog_item",
            "catalog_item_name",
            "quantity",
            "unit_price",
            "subtotal",
            "dry_cleaning_status",
        ]


class OrderItemInputSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False)
    catalog_item = serializers.PrimaryKeyRelatedField(queryset=CatalogItem.objects.all())
    quantity = serializers.DecimalField(max_digits=6, decimal_places=2)
    unit_price = serializers.DecimalField(
        max_digits=8, decimal_places=2, required=False, allow_null=True
    )


class OrderDetailSerializer(serializers.ModelSerializer):
    client = ClientMiniSerializer(read_only=True)
    status = serializers.CharField(read_only=True)
    items = OrderItemDetailSerializer(many=True, read_only=True)
    paid_amount = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "client",
            "status",
            "notes",
            "items",
            "total_amount",
            "paid_amount",
            "balance",
            "created_at",
            "delivered_at",
            "cancelled_at",
        ]

    def get_paid_amount(self, obj):
        # Hardcoded until apps.payments exists; will sum related Payment records.
        return Decimal("0")

    def get_balance(self, obj):
        return obj.total_amount - self.get_paid_amount(obj)


class OrderCreateSerializer(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Client.objects.all())
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    items = OrderItemInputSerializer(many=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("La orden debe tener al menos un ítem.")
        return value

    def create(self, validated_data):
        return services.create_order(
            client=validated_data["client"],
            items_data=validated_data["items"],
            notes=validated_data.get("notes", ""),
            created_by=validated_data["created_by"],
        )


class OrderUpdateSerializer(serializers.Serializer):
    client = serializers.PrimaryKeyRelatedField(
        queryset=Client.objects.all(), required=False
    )
    notes = serializers.CharField(required=False, allow_blank=True)
    items = OrderItemInputSerializer(many=True, required=False)

    def update(self, instance, validated_data):
        return services.update_order(
            instance,
            client=validated_data.get("client"),
            notes=validated_data.get("notes"),
            items_data=validated_data.get("items"),
        )
