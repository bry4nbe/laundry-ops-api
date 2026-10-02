from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.payments import services as payment_services
from apps.payments.serializers import InitialPaymentSerializer

from . import selectors, services
from .models import Order, OrderItem
from .serializers import (
    OrderCreateSerializer,
    OrderDetailSerializer,
    OrderItemDetailSerializer,
    OrderItemDryCleaningSerializer,
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
        if isinstance(request.data, dict) and request.data.get("payment") is not None:
            payment_serializer = InitialPaymentSerializer(data=request.data["payment"])
            payment_serializer.is_valid(raise_exception=True)
            if payment_serializer.validated_data["amount"] > 0:
                key, fingerprint = payment_services.request_identity(request)
                replay = payment_services.get_replay(key, fingerprint)
                if replay is not None:
                    order = selectors.order_queryset().get(pk=replay.order_id)
                    return Response(OrderDetailSerializer(order).data)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save(created_by=request.user)
        order = selectors.order_queryset().get(pk=order.pk)
        return Response(
            OrderDetailSerializer(order).data,
            status=status.HTTP_201_CREATED
            if serializer.was_created
            else status.HTTP_200_OK,
        )


class OrderDetailView(generics.RetrieveAPIView):
    serializer_class = OrderDetailSerializer

    def get_queryset(self):
        return selectors.order_queryset()

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
        order = selectors.order_queryset().get(pk=order.pk)
        return Response(OrderDetailSerializer(order).data)


class OrderDeliverView(APIView):
    @transaction.atomic
    def post(self, request, pk):
        order = get_object_or_404(Order.objects.select_for_update(), pk=pk)
        if order.delivered_at is not None or order.cancelled_at is not None:
            raise services.OrderConflict("La orden ya fue entregada o cancelada.")
        order.delivered_at = timezone.now()
        order.save(update_fields=["delivered_at"])
        order = selectors.order_queryset().get(pk=order.pk)
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
        serializer = OrderItemDryCleaningSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item.dry_cleaning_status = serializer.validated_data["dry_cleaning_status"]
        item.save(update_fields=["dry_cleaning_status"])
        return Response(OrderItemDetailSerializer(item).data)
