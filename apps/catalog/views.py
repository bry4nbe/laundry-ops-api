from rest_framework.generics import ListAPIView

from .models import CatalogItem
from .serializers import CatalogItemSerializer


class CatalogItemListView(ListAPIView):
    serializer_class = CatalogItemSerializer
    pagination_class = None

    def get_queryset(self):
        queryset = CatalogItem.objects.all()
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == "true")
        return queryset
