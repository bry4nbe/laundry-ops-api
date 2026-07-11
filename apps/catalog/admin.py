from django.contrib import admin

from .models import CatalogItem


@admin.register(CatalogItem)
class CatalogItemAdmin(admin.ModelAdmin):
    list_display = ["name", "service_type", "base_price", "is_dry_cleaning", "is_active"]
    list_filter = ["service_type", "is_active", "is_dry_cleaning"]
    search_fields = ["name"]
