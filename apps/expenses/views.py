from rest_framework import generics
from rest_framework.pagination import PageNumberPagination

from .models import Expense
from .permissions import IsBusinessAdmin
from .serializers import ExpenseSerializer


class ExpensePagination(PageNumberPagination):
    page_size = 20


class ExpenseListCreateView(generics.ListCreateAPIView):
    queryset = Expense.objects.select_related("created_by")
    serializer_class = ExpenseSerializer
    permission_classes = [IsBusinessAdmin]
    pagination_class = ExpensePagination

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ExpenseDetailView(generics.RetrieveAPIView):
    queryset = Expense.objects.select_related("created_by")
    serializer_class = ExpenseSerializer
    permission_classes = [IsBusinessAdmin]
