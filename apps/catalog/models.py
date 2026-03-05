from django.db import models


class ServiceType(models.TextChoices):
    PER_GARMENT = "PER_GARMENT", "Per Garment"
    PER_KG = "PER_KG", "Per Kilogram"


class Catalog(models.Model):
    name = models.CharField(max_length=255)
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    service_type = models.CharField(
        max_length=15, choices=ServiceType.choices, default=ServiceType.PER_GARMENT
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
