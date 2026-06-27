from django.contrib.auth.models import AbstractUser
from django.db import models


class UserRole(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    OPERATOR = "OPERATOR", "Operator"


class User(AbstractUser):
    first_name = None
    last_name = None
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True, blank=True, null=True)
    role = models.CharField(
        max_length=10, choices=UserRole.choices, default=UserRole.OPERATOR
    )
    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["name"]

    def __str__(self):
        return self.username
