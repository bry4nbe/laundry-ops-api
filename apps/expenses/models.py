from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class ExpenseCategory(models.TextChoices):
    SUPPLIES = "SUPPLIES", "Supplies"
    SERVICES = "SERVICES", "Services"
    OUTSOURCING = "OUTSOURCING", "Outsourcing"
    MAINTENANCE = "MAINTENANCE", "Maintenance"
    OTHER = "OTHER", "Other"


class Expense(models.Model):
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    concept = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=ExpenseCategory.choices)
    date = models.DateField()
    receipt_number = models.CharField(max_length=100, blank=True, null=True)  # noqa: DJ001
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_expenses",
    )

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="expense_amount_positive"
            )
        ]

    def __str__(self):
        return self.concept
