from django.db import models


class Client(models.Model):
    name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20, unique=True, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
