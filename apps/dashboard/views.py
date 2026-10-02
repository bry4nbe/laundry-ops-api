from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import selectors
from .permissions import IsBusinessAdmin
from .serializers import DashboardRangeSerializer, DashboardSummarySerializer


class DashboardView(APIView):
    permission_classes = [IsAuthenticated, IsBusinessAdmin]

    def get(self, request):
        today = timezone.localdate(timezone=timezone.get_default_timezone())
        filters = DashboardRangeSerializer(
            data=request.query_params, context={"today": today}
        )
        filters.is_valid(raise_exception=True)
        summary = selectors.get_dashboard_summary(**filters.validated_data, today=today)
        return Response(DashboardSummarySerializer(summary).data)
