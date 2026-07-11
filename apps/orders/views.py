from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from . import selectors
from .models import DryCleaningStatus, Order, OrderItem
from .serializers import (
    OrderCreateSerializer,
    OrderDetailSerializer,
    OrderItemDetailSerializer,
    OrderUpdateSerializer,
)


class OrderPagination(PageNumberPagination):
    page_size = 20


class OrderListCreateView(generics.ListCreateAPIView):
    pagination_class = OrderPagination

    def get_queryset(self):
        return selectors.get_orders(self.request.query_params)

    def get_serializer_class(self):
        if self.request.method == "POST":
            return OrderCreateSerializer
        return OrderDetailSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save(created_by=request.user)
        return Response(
            OrderDetailSerializer(order).data, status=status.HTTP_201_CREATED
        )


class OrderDetailView(generics.RetrieveAPIView):
    queryset = Order.objects.select_related("client")
    serializer_class = OrderDetailSerializer

    def patch(self, request, *args, **kwargs):
        order = self.get_object()
        if order.delivered_at is not None or order.cancelled_at is not None:
            return Response(
                {"detail": "Solo se pueden editar órdenes activas."},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = OrderUpdateSerializer(order, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return Response(OrderDetailSerializer(order).data)


class OrderDeliverView(APIView):
    def post(self, request, pk):
        order = get_object_or_404(Order, pk=pk)
        if order.delivered_at is not None or order.cancelled_at is not None:
            return Response(
                {"detail": "La orden ya fue entregada o cancelada."},
                status=status.HTTP_409_CONFLICT,
            )
        order.delivered_at = timezone.now()
        order.save(update_fields=["delivered_at"])
        return Response(OrderDetailSerializer(order).data)


class OrderItemDryCleaningView(APIView):
    def patch(self, request, pk, item_id):
        order = get_object_or_404(Order, pk=pk)
        if order.cancelled_at is not None:
            return Response(
                {"detail": "No se puede modificar una orden cancelada."},
                status=status.HTTP_409_CONFLICT,
            )
        item = get_object_or_404(OrderItem, pk=item_id, order=order)
        if item.dry_cleaning_status is None:
            return Response(
                {"detail": "El ítem no es de lavado al seco."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        new_status = request.data.get("dry_cleaning_status")
        if new_status not in DryCleaningStatus.values:
            return Response(
                {"detail": "Estado de lavado al seco inválido."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        item.dry_cleaning_status = new_status
        item.save(update_fields=["dry_cleaning_status"])
        return Response(OrderItemDetailSerializer(item).data)
