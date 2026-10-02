from rest_framework.permissions import BasePermission

from apps.users.models import UserRole


class IsBusinessAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == UserRole.ADMIN
