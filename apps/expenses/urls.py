from django.urls import path

from .views import ExpenseDetailView, ExpenseListCreateView

urlpatterns = [
    path("", ExpenseListCreateView.as_view(), name="expense-list"),
    path("<int:pk>/", ExpenseDetailView.as_view(), name="expense-detail"),
]
