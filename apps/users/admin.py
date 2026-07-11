from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ["username", "name", "email", "role", "is_active"]
    fieldsets = [
        (None, {"fields": ["username", "password"]}),
        ("Personal info", {"fields": ["name", "email"]}),
        ("Rol", {"fields": ["role"]}),
        (
            "Permissions",
            {
                "fields": [
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ]
            },
        ),
        ("Important dates", {"fields": ["last_login", "date_joined"]}),
    ]
    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": ["username", "name", "role", "password1", "password2"],
            },
        )
    ]
