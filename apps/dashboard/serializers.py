from datetime import date

from rest_framework import serializers


class DashboardRangeSerializer(serializers.Serializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        if ("date_from" in attrs) != ("date_to" in attrs):
            raise serializers.ValidationError("Indique date_from y date_to juntos.")
        if not attrs:
            return {
                "date_from": self.context["today"],
                "date_to": self.context["today"],
            }
        for field in ("date_from", "date_to"):
            if self.initial_data[field] != attrs[field].isoformat():
                raise serializers.ValidationError({field: "Use el formato YYYY-MM-DD."})
        if attrs["date_from"] > attrs["date_to"]:
            raise serializers.ValidationError(
                "date_from no puede ser posterior a date_to."
            )
        if attrs["date_to"] == date.max:
            raise serializers.ValidationError(
                {"date_to": "La fecha no permite calcular el límite final del rango."}
            )
        return attrs


class CollectedByMethodSerializer(serializers.Serializer):
    CASH = serializers.DecimalField(
        max_digits=None, decimal_places=2, coerce_to_string=True
    )
    YAPE_PLIN = serializers.DecimalField(
        max_digits=None, decimal_places=2, coerce_to_string=True
    )


class CurrentMonthMonitorSerializer(serializers.Serializer):
    month = serializers.CharField()
    collected_amount = serializers.DecimalField(
        max_digits=None, decimal_places=2, coerce_to_string=True
    )
    above_5000 = serializers.BooleanField()
    above_8000 = serializers.BooleanField()
    indicative_only = serializers.BooleanField()


class DashboardSummarySerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    orders_created = serializers.IntegerField()
    pending_delivery_count = serializers.IntegerField()
    collected_amount = serializers.DecimalField(
        max_digits=None, decimal_places=2, coerce_to_string=True
    )
    collected_by_method = CollectedByMethodSerializer()
    outstanding_amount = serializers.DecimalField(
        max_digits=None, decimal_places=2, coerce_to_string=True
    )
    current_month_monitor = CurrentMonthMonitorSerializer()
