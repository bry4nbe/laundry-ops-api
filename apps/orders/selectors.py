from .models import Order
from .serializers import OrderFilterSerializer


def get_orders(filters):
    serializer = OrderFilterSerializer(data=filters)
    serializer.is_valid(raise_exception=True)
    validated_filters = serializer.validated_data
    queryset = Order.objects.select_related("client")

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
