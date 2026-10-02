from django.urls import path

from .views import OrderPaymentView

urlpatterns = [
    path(
        "orders/<int:order_id>/payments/",
        OrderPaymentView.as_view(),
        name="order-payments",
    ),
]
