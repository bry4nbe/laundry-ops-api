from django.conf import settings
from django.db import models


class PaymentMethod(models.TextChoices):
    CASH = "CASH", "Cash"
    YAPE_PLIN = "YAPE_PLIN", "Yape/Plin"


class PaymentType(models.TextChoices):
    ADVANCE = "ADVANCE", "Advance"
    PARTIAL = "PARTIAL", "Partial"
    FINAL = "FINAL", "Final"


class Payment(models.Model):
    order = models.ForeignKey(
        "orders.Order", on_delete=models.PROTECT, related_name="payments"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_payments",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=50, choices=PaymentMethod.choices)
    reference_code = models.CharField(max_length=100, blank=True, default="")
    payment_type = models.CharField(max_length=50, choices=PaymentType.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    idempotency_key = models.UUIDField(unique=True, null=True, blank=True)
    request_fingerprint = models.CharField(max_length=64, blank=True, default="")
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="voided_payments",
        null=True,
        blank=True,
    )
    void_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="payment_amount_positive"
            )
        ]

    def __str__(self):
        return f"Payment for Order {self.order.order_number}"
