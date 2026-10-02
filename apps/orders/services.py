from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError

from apps.payments import services as payment_services

from .models import DryCleaningStatus, Order, OrderItem

MAX_AMOUNT = Decimal("99999999.99")


class OrderConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Solo se pueden editar órdenes activas."
    default_code = "order_conflict"


def _compute_subtotal(quantity, unit_price):
    subtotal = (quantity * unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if subtotal > MAX_AMOUNT:
        raise ValidationError("El subtotal supera el importe máximo permitido.")
    return subtotal


def _resolve_item_fields(catalog_item, quantity, unit_price):
    if not catalog_item.is_active:
        raise ValidationError(f"El producto '{catalog_item.name}' no está activo.")
    if unit_price is None:
        unit_price = Decimal(catalog_item.base_price)
    if unit_price < 0:
        raise ValidationError("El precio unitario no puede ser negativo.")
    subtotal = _compute_subtotal(quantity, unit_price)
    return unit_price, subtotal


@transaction.atomic
def create_order(client, items_data, notes, created_by):
    order = Order.objects.create(
        client=client, notes=notes or "", created_by=created_by
    )

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

    if total > MAX_AMOUNT:
        raise ValidationError("El total supera el importe máximo permitido.")
    OrderItem.objects.bulk_create(items)
    order.total_amount = total
    order.order_number = f"ORD-{order.id:05d}"
    order.save(update_fields=["total_amount", "order_number"])
    return order


def create_order_with_advance(
    *,
    client,
    items_data,
    notes,
    created_by,
    payment_data,
    idempotency_key,
    request_fingerprint,
):
    replay = payment_services.get_replay(idempotency_key, request_fingerprint)
    if replay is not None:
        return Order.objects.get(pk=replay.order_id), False
    try:
        with transaction.atomic():
            order = create_order(client, items_data, notes, created_by)
            order = Order.objects.select_for_update().get(pk=order.pk)
            payment_services.create_payment_for_locked_order(
                order,
                **payment_data,
                created_by=created_by,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                initial=True,
            )
    except IntegrityError:
        replay = payment_services.get_replay(idempotency_key, request_fingerprint)
        if replay is None:
            raise
        return Order.objects.get(pk=replay.order_id), False
    return order, True


@transaction.atomic
def update_order(order, client=None, notes=None, items_data=None):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.delivered_at is not None or order.cancelled_at is not None:
        raise OrderConflict()
    if (
        items_data is not None
        and order.payments.filter(voided_at__isnull=True).exists()
    ):
        raise OrderConflict(
            "No se pueden cambiar los ítems ni sus importes mientras existan pagos vigentes."
        )
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
            item_id = item_data.get("id")
            order_item = None
            if item_id is not None:
                try:
                    order_item = order.items.get(id=item_id)
                except OrderItem.DoesNotExist as exc:
                    raise ValidationError(
                        f"El ítem {item_id} no pertenece a esta orden."
                    ) from exc
            unit_price = item_data.get("unit_price")
            if (
                order_item is not None
                and order_item.catalog_item_id == catalog_item.pk
                and "unit_price" not in item_data
            ):
                unit_price = order_item.unit_price
            unit_price, subtotal = _resolve_item_fields(
                catalog_item, item_data["quantity"], unit_price
            )
            if order_item is not None:
                order_item.catalog_item = catalog_item
                order_item.quantity = item_data["quantity"]
                order_item.unit_price = unit_price
                order_item.subtotal = subtotal
                if not catalog_item.is_dry_cleaning:
                    order_item.dry_cleaning_status = None
                elif order_item.dry_cleaning_status is None:
                    order_item.dry_cleaning_status = DryCleaningStatus.RECEIVED
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

        if total > MAX_AMOUNT:
            raise ValidationError("El total supera el importe máximo permitido.")
        order.total_amount = total

    order.save()
    return order
