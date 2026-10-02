import hashlib
import json
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError

from apps.orders.models import Order

from .models import Payment, PaymentType


class PaymentConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "La operación entra en conflicto con el estado actual."
    default_code = "payment_conflict"


def request_identity(request):
    try:
        key = UUID(request.headers.get("Idempotency-Key", ""))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(
            {"Idempotency-Key": "Se requiere una clave UUID válida."}
        ) from exc
    try:
        canonical_request = json.dumps(
            {
                "user": request.user.pk,
                "method": request.method,
                "path": request.path,
                "body": request.data,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "El cuerpo debe ser JSON válido con texto UTF-8 y números finitos."
        ) from exc
    return key, hashlib.sha256(canonical_request).hexdigest()


def get_replay(idempotency_key, request_fingerprint):
    payment = (
        Payment.objects.select_related("created_by", "voided_by")
        .filter(idempotency_key=idempotency_key)
        .first()
    )
    if payment is not None and payment.request_fingerprint != request_fingerprint:
        raise PaymentConflict(
            "La clave de idempotencia ya se utilizó para otra petición."
        )
    return payment


def create_payment_for_locked_order(
    order,
    *,
    amount,
    payment_method,
    created_by,
    idempotency_key,
    request_fingerprint,
    reference_code="",
    initial=False,
):
    paid_amount = order.payments.filter(voided_at__isnull=True).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")
    balance = order.total_amount - paid_amount
    if order.cancelled_at is not None:
        raise PaymentConflict("No se pueden registrar pagos en una orden cancelada.")
    if amount <= 0 or amount > balance:
        raise ValidationError(
            {"amount": "El importe debe ser positivo y no superar el saldo vigente."}
        )
    payment_type = (
        PaymentType.FINAL
        if amount == balance
        else PaymentType.ADVANCE
        if initial
        else PaymentType.PARTIAL
    )
    return Payment.objects.create(
        order=order,
        amount=amount,
        payment_method=payment_method,
        reference_code=reference_code,
        payment_type=payment_type,
        created_by=created_by,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    )


def register_payment(
    order_id,
    *,
    amount,
    payment_method,
    created_by,
    idempotency_key,
    request_fingerprint,
    reference_code="",
):
    replay = get_replay(idempotency_key, request_fingerprint)
    if replay is not None:
        return replay, False
    try:
        with transaction.atomic():
            order = get_object_or_404(Order.objects.select_for_update(), pk=order_id)
            replay = get_replay(idempotency_key, request_fingerprint)
            if replay is not None:
                return replay, False
            payment = create_payment_for_locked_order(
                order,
                amount=amount,
                payment_method=payment_method,
                reference_code=reference_code,
                created_by=created_by,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
    except IntegrityError:
        replay = get_replay(idempotency_key, request_fingerprint)
        if replay is None:
            raise
        return replay, False
    return payment, True


@transaction.atomic
def void_payment(payment_id, *, voided_by, reason):
    reason = reason.strip()
    if not reason or len(reason) > 255:
        raise ValidationError(
            {"reason": "Indique un motivo de entre 1 y 255 caracteres."}
        )
    order_id = get_object_or_404(
        Payment.objects.only("order_id"), pk=payment_id
    ).order_id
    Order.objects.select_for_update().get(pk=order_id)
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    if payment.voided_at is not None:
        raise PaymentConflict("El pago ya está anulado.")
    payment.voided_at = timezone.now()
    payment.voided_by = voided_by
    payment.void_reason = reason
    payment.save(update_fields=["voided_at", "voided_by", "void_reason"])
    return payment
