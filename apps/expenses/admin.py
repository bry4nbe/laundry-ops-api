from django.contrib import admin

from apps.users.models import UserRole

from .models import Expense


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = [
        "date",
        "concept",
        "category",
        "amount",
        "receipt_number",
        "created_by",
    ]
    list_filter = ["category", "date"]
    search_fields = ["concept", "receipt_number"]
    readonly_fields = ["created_by"]
    list_select_related = ["created_by"]

    def has_module_permission(self, request):
        return request.user.role == UserRole.ADMIN and super().has_module_permission(
            request
        )

    def has_view_permission(self, request, obj=None):
        return request.user.role == UserRole.ADMIN and super().has_view_permission(
            request, obj
        )

    def has_change_permission(self, request, obj=None):
        return request.user.role == UserRole.ADMIN and super().has_change_permission(
            request, obj
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
