from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orders.models import Order

from . import services
from .models import Payment
from .serializers import PaymentDetailSerializer, PaymentInputSerializer


class OrderPaymentView(APIView):
    parser_classes = [JSONParser]

    def get(self, request, order_id):
        get_object_or_404(Order, pk=order_id)
        payments = (
            Payment.objects.filter(order_id=order_id)
            .select_related("created_by", "voided_by")
            .order_by("created_at", "id")
        )
        return Response(PaymentDetailSerializer(payments, many=True).data)

    def post(self, request, order_id):
        key, fingerprint = services.request_identity(request)
        replay = services.get_replay(key, fingerprint)
        if replay is not None:
            return Response(PaymentDetailSerializer(replay).data)
        serializer = PaymentInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment, created = services.register_payment(
            order_id,
            **serializer.validated_data,
            created_by=request.user,
            idempotency_key=key,
            request_fingerprint=fingerprint,
        )
        payment = Payment.objects.select_related("created_by", "voided_by").get(
            pk=payment.pk
        )
        return Response(
            PaymentDetailSerializer(payment).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
