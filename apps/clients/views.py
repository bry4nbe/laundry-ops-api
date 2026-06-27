from django.db.models import Q
from rest_framework import viewsets

from .models import Client
from .serializers import ClientSerializer


class ClientViewSet(viewsets.ModelViewSet):
    serializer_class = ClientSerializer
    http_method_names = ["get", "post", "patch"]

    def get_queryset(self):
        queryset = Client.objects.all()
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(phone_number__icontains=search)
            )
        return queryset
