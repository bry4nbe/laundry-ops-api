from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.catalog.models import ServiceType


class OrderStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    READY = "READY", "Ready"
    DELIVERED = "DELIVERED", "Delivered"
    CANCELLED = "CANCELLED", "Cancelled"


class DryCleaningStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Received"
    SENT_TO_THIRD_PARTY = "SENT_TO_THIRD_PARTY", "Sent to Third Party"
    RETURNED = "RETURNED", "Returned"


class Order(models.Model):
    order_number = models.CharField(max_length=100, unique=True)
    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="orders"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_orders",
    )
    status = models.CharField(
        max_length=20, choices=OrderStatus.choices, default=OrderStatus.RECEIVED
    )
    total_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    notes = models.TextField(blank=True, default="")
    ticket_photo_url = models.URLField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.order_number


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "catalog.CatalogItem", on_delete=models.PROTECT, related_name="order_items"
    )
    service_type = models.CharField(max_length=15, choices=ServiceType.choices)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    item_notes = models.TextField(blank=True, default="")
    is_dry_cleaning = models.BooleanField(default=False)
    third_party_cost = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    dry_cleaning_status = models.CharField(  # noqa: DJ001
        max_length=30, choices=DryCleaningStatus.choices, null=True, blank=True
    )
    sent_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product.name} - {self.quantity} {self.service_type}"
