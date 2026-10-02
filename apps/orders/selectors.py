from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce

from .models import Order
from .serializers import OrderFilterSerializer


def order_queryset():
    money_field = DecimalField(max_digits=10, decimal_places=2)
    return (
        Order.objects.select_related("client")
        .annotate(
            paid_amount=Coalesce(
                Sum("payments__amount", filter=Q(payments__voided_at__isnull=True)),
                Value(Decimal("0.00")),
                output_field=money_field,
            )
        )
        .annotate(
            balance=ExpressionWrapper(
                F("total_amount") - F("paid_amount"), output_field=money_field
            )
        )
        .prefetch_related("items__catalog_item")
        .order_by("-created_at", "-id")
    )


def get_orders(filters):
    serializer = OrderFilterSerializer(data=filters)
    serializer.is_valid(raise_exception=True)
    validated_filters = serializer.validated_data
    queryset = order_queryset()

    delivered = filters.get("delivered")
    if delivered is not None and delivered.lower() == "false":
        queryset = queryset.filter(delivered_at__isnull=True, cancelled_at__isnull=True)

    client_id = validated_filters.get("client")
    if client_id:
        queryset = queryset.filter(client_id=client_id)

    date_from = validated_filters.get("date_from")
    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)

    date_to = validated_filters.get("date_to")
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)

    return queryset
