from rest_framework import serializers

from .models import CatalogItem


class CatalogItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = CatalogItem
        fields = [
            "id",
            "name",
            "service_type",
            "base_price",
            "is_dry_cleaning",
            "is_active",
        ]
