from decimal import Decimal

from django.db import models


class Order(models.Model):
    order_number = models.CharField(max_length=100, unique=True)
    client_id = models.ForeignKey
    created_by = models.ForeignKey
    status = models.TextChoices
    total_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    notes = models.TextField(blank=True, default="")
    ticket_photo_url = models.URLField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.order_number
