from django.contrib import admin, messages
from django.utils import timezone

from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = [
        "catalog_item",
        "quantity",
        "unit_price",
        "subtotal",
        "dry_cleaning_status",
    ]
    readonly_fields = ["catalog_item", "quantity", "unit_price", "subtotal"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["order_number", "client", "total_amount", "status", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["order_number", "client__name"]
    readonly_fields = ["order_number", "total_amount"]
    inlines = [OrderItemInline]
    actions = ["cancel_orders"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Cancelar órdenes seleccionadas")
    def cancel_orders(self, request, queryset):
        active_orders = queryset.filter(
            delivered_at__isnull=True, cancelled_at__isnull=True
        )
        count = active_orders.update(cancelled_at=timezone.now())
        skipped = queryset.count() - count
        self.message_user(request, f"{count} orden(es) cancelada(s).")
        if skipped:
            self.message_user(
                request,
                f"{skipped} orden(es) omitida(s) por ya estar entregadas o canceladas.",
                level=messages.WARNING,
            )
