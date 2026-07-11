from django.db import models


class ServiceType(models.TextChoices):
    PER_GARMENT = "PER_GARMENT", "Per Garment"
    PER_KG = "PER_KG", "Per Kilogram"


class CatalogItem(models.Model):
    name = models.CharField(max_length=100, unique=True)
    service_type = models.CharField(max_length=15, choices=ServiceType.choices)
    base_price = models.DecimalField(max_digits=8, decimal_places=2)
    is_dry_cleaning = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
