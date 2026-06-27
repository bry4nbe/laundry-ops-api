from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ["username", "name", "email", "role", "is_active"]
    fieldsets = UserAdmin.fieldsets + (("Rol", {"fields": ["name", "role"]}),)
