from django.conf import settings
from django.db import models


class OrderStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Activa"
    DELIVERED = "DELIVERED", "Entregada"
    CANCELLED = "CANCELLED", "Cancelada"


class DryCleaningStatus(models.TextChoices):
    RECEIVED = "RECEIVED", "Recibido"
    SENT = "SENT", "Enviado al tercero"
    RETURNED = "RETURNED", "Devuelto"


class Order(models.Model):
    order_number = models.CharField(  # noqa: DJ001
        max_length=10, unique=True, null=True, blank=True
    )
    client = models.ForeignKey(
        "clients.Client", on_delete=models.PROTECT, related_name="orders"
    )
    notes = models.TextField(blank=True, default="")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.order_number or f"Order #{self.pk}"

    @property
    def status(self):
        if self.cancelled_at is not None:
            return OrderStatus.CANCELLED
        if self.delivered_at is not None:
            return OrderStatus.DELIVERED
        return OrderStatus.ACTIVE


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    catalog_item = models.ForeignKey(
        "catalog.CatalogItem", on_delete=models.PROTECT, related_name="order_items"
    )
    quantity = models.DecimalField(max_digits=6, decimal_places=2)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    dry_cleaning_status = models.CharField(  # noqa: DJ001
        max_length=10, choices=DryCleaningStatus.choices, null=True, blank=True
    )

    def __str__(self):
        return f"{self.catalog_item.name} x{self.quantity}"
