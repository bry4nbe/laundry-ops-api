from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import DryCleaningStatus, Order, OrderItem


def _compute_subtotal(quantity, unit_price):
    return (quantity * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _resolve_item_fields(catalog_item, quantity, unit_price):
    if not catalog_item.is_active:
        raise ValidationError(f"El producto '{catalog_item.name}' no está activo.")
    if unit_price is None:
        unit_price = Decimal(catalog_item.base_price)
    subtotal = _compute_subtotal(quantity, unit_price)
    return unit_price, subtotal


@transaction.atomic
def create_order(client, items_data, notes, created_by):
    order = Order.objects.create(client=client, notes=notes or "", created_by=created_by)

    total = Decimal("0")
    items = []
    for item_data in items_data:
        catalog_item = item_data["catalog_item"]
        unit_price, subtotal = _resolve_item_fields(
            catalog_item, item_data["quantity"], item_data.get("unit_price")
        )
        dry_cleaning_status = (
            DryCleaningStatus.RECEIVED if catalog_item.is_dry_cleaning else None
        )
        items.append(
            OrderItem(
                order=order,
                catalog_item=catalog_item,
                quantity=item_data["quantity"],
                unit_price=unit_price,
                subtotal=subtotal,
                dry_cleaning_status=dry_cleaning_status,
            )
        )
        total += subtotal

    OrderItem.objects.bulk_create(items)
    order.total_amount = total
    order.order_number = f"ORD-{order.id:05d}"
    order.save(update_fields=["total_amount", "order_number"])
    return order


@transaction.atomic
def update_order(order, client=None, notes=None, items_data=None):
    if client is not None:
        order.client = client
    if notes is not None:
        order.notes = notes

    if items_data is not None:
        incoming_ids = {item["id"] for item in items_data if "id" in item}
        order.items.exclude(id__in=incoming_ids).delete()

        total = Decimal("0")
        for item_data in items_data:
            catalog_item = item_data["catalog_item"]
            unit_price, subtotal = _resolve_item_fields(
                catalog_item, item_data["quantity"], item_data.get("unit_price")
            )
            item_id = item_data.get("id")
            if item_id:
                try:
                    order_item = order.items.get(id=item_id)
                except OrderItem.DoesNotExist as exc:
                    raise ValidationError(
                        f"El ítem {item_id} no pertenece a esta orden."
                    ) from exc
                order_item.catalog_item = catalog_item
                order_item.quantity = item_data["quantity"]
                order_item.unit_price = unit_price
                order_item.subtotal = subtotal
                order_item.save()
            else:
                dry_cleaning_status = (
                    DryCleaningStatus.RECEIVED if catalog_item.is_dry_cleaning else None
                )
                order.items.create(
                    catalog_item=catalog_item,
                    quantity=item_data["quantity"],
                    unit_price=unit_price,
                    subtotal=subtotal,
                    dry_cleaning_status=dry_cleaning_status,
                )
            total += subtotal

        order.total_amount = total

    order.save()
    return order
