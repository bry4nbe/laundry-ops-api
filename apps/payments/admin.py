from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from rest_framework.exceptions import APIException

from . import services
from .models import Payment


class VoidPaymentForm(forms.Form):
    reason = forms.CharField(
        label="Motivo de anulación",
        max_length=255,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "order",
        "amount",
        "payment_method",
        "payment_type",
        "created_at",
        "created_by",
        "voided_at",
    ]
    list_filter = ["payment_method", "payment_type", "created_at", "voided_at"]
    search_fields = ["order__order_number", "reference_code", "created_by__name"]
    readonly_fields = [field.name for field in Payment._meta.fields]
    actions = ["void_selected_payment"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("order", "created_by", "voided_by")
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_void_permission(self, request):
        return request.user.has_perm("payments.change_payment")

    @admin.action(description="Anular un pago seleccionado", permissions=["void"])
    def void_selected_payment(self, request, queryset):
        if not self.has_void_permission(request):
            raise PermissionDenied
        if queryset.count() != 1:
            self.message_user(
                request,
                "Seleccione exactamente un pago para anular.",
                level=messages.ERROR,
            )
            return None
        payment = queryset.get()
        if payment.voided_at is not None:
            self.message_user(request, "El pago ya está anulado.", level=messages.ERROR)
            return None
        form = VoidPaymentForm(request.POST if "confirm_void" in request.POST else None)
        if "confirm_void" in request.POST and form.is_valid():
            try:
                payment = services.void_payment(
                    payment.pk,
                    voided_by=request.user,
                    reason=form.cleaned_data["reason"],
                )
            except APIException as exc:
                self.message_user(request, str(exc.detail), level=messages.ERROR)
            else:
                self.log_change(
                    request, payment, f"Payment voided: {payment.void_reason}"
                )
                self.message_user(
                    request,
                    "Pago anulado. El historial se conserva; esta acción no devuelve dinero.",
                )
            return None
        context = {
            **self.admin_site.each_context(request),
            "title": "Confirmar anulación de pago",
            "opts": self.model._meta,
            "payment": payment,
            "form": form,
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
        }
        return TemplateResponse(
            request, "admin/payments/payment/void_confirmation.html", context
        )
