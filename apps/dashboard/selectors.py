from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum
from django.utils import timezone

from apps.orders import selectors as order_selectors
from apps.orders.models import Order
from apps.payments.models import Payment, PaymentMethod


def _midnight(day):
    return timezone.make_aware(
        datetime.combine(day, time.min), timezone.get_default_timezone()
    )


def get_dashboard_summary(*, date_from, date_to, today):
    period = Q(
        created_at__gte=_midnight(date_from),
        created_at__lt=_midnight(date_to + timedelta(days=1)),
    )
    month_start = today.replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    current_month = Q(
        created_at__gte=_midnight(month_start),
        created_at__lt=_midnight(next_month),
    )
    money_field = DecimalField(max_digits=None, decimal_places=2)
    zero = Decimal("0.00")
    collections = Payment.objects.filter(voided_at__isnull=True).aggregate(
        collected_amount=Sum(
            "amount", filter=period, default=zero, output_field=money_field
        ),
        cash=Sum(
            "amount",
            filter=period & Q(payment_method=PaymentMethod.CASH),
            default=zero,
            output_field=money_field,
        ),
        yape_plin=Sum(
            "amount",
            filter=period & Q(payment_method=PaymentMethod.YAPE_PLIN),
            default=zero,
            output_field=money_field,
        ),
        month_collected=Sum(
            "amount", filter=current_month, default=zero, output_field=money_field
        ),
    )
    counts = Order.objects.aggregate(
        orders_created=Count("pk", filter=period),
        pending_delivery_count=Count(
            "pk", filter=Q(delivered_at__isnull=True, cancelled_at__isnull=True)
        ),
    )
    outstanding = (
        order_selectors.order_queryset()
        .filter(period, cancelled_at__isnull=True)
        .aggregate(amount=Sum("balance", default=zero, output_field=money_field))[
            "amount"
        ]
    )
    return {
        "date_from": date_from,
        "date_to": date_to,
        **counts,
        "collected_amount": collections["collected_amount"],
        "collected_by_method": {
            PaymentMethod.CASH: collections["cash"],
            PaymentMethod.YAPE_PLIN: collections["yape_plin"],
        },
        "outstanding_amount": outstanding,
        "current_month_monitor": {
            "month": month_start.strftime("%Y-%m"),
            "collected_amount": collections["month_collected"],
            "above_5000": collections["month_collected"] > Decimal("5000.00"),
            "above_8000": collections["month_collected"] > Decimal("8000.00"),
            "indicative_only": True,
        },
    }
