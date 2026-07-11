from django.urls import path

from .views import (
    OrderDeliverView,
    OrderDetailView,
    OrderItemDryCleaningView,
    OrderListCreateView,
)

urlpatterns = [
    path("", OrderListCreateView.as_view(), name="order-list-create"),
    path("<int:pk>/", OrderDetailView.as_view(), name="order-detail"),
    path("<int:pk>/deliver/", OrderDeliverView.as_view(), name="order-deliver"),
    path(
        "<int:pk>/items/<int:item_id>/dry-cleaning/",
        OrderItemDryCleaningView.as_view(),
        name="order-item-dry-cleaning",
    ),
]
