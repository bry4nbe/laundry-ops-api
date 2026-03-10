from decimal import Decimal

from django.conf import settings
from django.db import models


class PaymentMethod(models.TextChoices):
    CASH = "CASH", "Cash"
    CREDIT_CARD = "YAPE/PLIN", "Yape/Plin"
    OTHER = "OTHER", "Other"


class PaymentType(models.TextChoices):
    ADVANCE = "ADVANCE", "Advance"
    PARTIAL = "PARTIAL", "Partial"
    FINAL = "FINAL", "Final"


class Payment(models.Model):
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_payments",
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=(Decimal("0.00"))
    )
    payment_method = models.CharField(max_length=50)
    reference_code = models.CharField(max_length=100, blank=True, default="")
    payment_type = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment for Order {self.order.order_number}"
