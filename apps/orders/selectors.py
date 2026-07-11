from django.utils.dateparse import parse_date

from .models import Order


def get_orders(filters):
    queryset = Order.objects.select_related("client")

    delivered = filters.get("delivered")
    if delivered is not None and delivered.lower() == "false":
        queryset = queryset.filter(delivered_at__isnull=True, cancelled_at__isnull=True)

    client_id = filters.get("client")
    if client_id:
        queryset = queryset.filter(client_id=client_id)

    date_from = filters.get("date_from")
    if date_from:
        queryset = queryset.filter(created_at__date__gte=parse_date(date_from))

    date_to = filters.get("date_to")
    if date_to:
        queryset = queryset.filter(created_at__date__lte=parse_date(date_to))

    return queryset
