from django.urls import path

from .views import CatalogItemListView

urlpatterns = [
    path("items/", CatalogItemListView.as_view(), name="catalog-item-list"),
]
