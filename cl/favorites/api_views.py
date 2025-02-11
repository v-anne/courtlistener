from django.db.models import Q
from rest_framework import permissions
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from cl.api.api_permissions import V3APIPermission
from cl.api.pagination import MediumAdjustablePagination
from cl.api.utils import LoggingMixin
from cl.favorites.api_permissions import IsTagOwner
from cl.favorites.api_serializers import (
    DocketTagSerializer,
    PrayerSerializer,
    UserTagSerializer,
)
from cl.favorites.filters import DocketTagFilter, PrayerFilter, UserTagFilter
from cl.favorites.models import DocketTag, Prayer, UserTag


class UserTagViewSet(ModelViewSet):
    permission_classes = [
        permissions.IsAuthenticatedOrReadOnly,
        V3APIPermission,
    ]
    serializer_class = UserTagSerializer
    pagination_class = MediumAdjustablePagination
    filterset_class = UserTagFilter
    ordering_fields = (
        "date_created",
        "date_modified",
        "name",
        "view_count",
    )
    # Default cursor ordering key
    ordering = "-id"
    # Additional cursor ordering fields
    cursor_ordering_fields = [
        "id",
        "date_created",
        "date_modified",
    ]

    def get_queryset(self):
        q = Q(published=True)
        if self.request.user.is_authenticated:
            q |= Q(user=self.request.user)
        return UserTag.objects.filter(q).order_by("-id")


class DocketTagViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, IsTagOwner, V3APIPermission]
    serializer_class = DocketTagSerializer
    filterset_class = DocketTagFilter
    pagination_class = MediumAdjustablePagination
    # Default cursor ordering key
    ordering = "-id"
    # Additional cursor ordering fields
    cursor_ordering_fields = ["id"]

    def get_queryset(self):
        return DocketTag.objects.filter(
            Q(tag__user=self.request.user) | Q(tag__published=True)
        )


class PrayerViewSet(LoggingMixin, ModelViewSet):
    """A ModelViewset to handle CRUD operations for Prayer."""

    permission_classes = [IsAuthenticated, V3APIPermission]
    serializer_class = PrayerSerializer
    pagination_class = MediumAdjustablePagination
    filterset_class = PrayerFilter
    ordering_fields = ("date_created",)
    # Default cursor ordering key
    ordering = "-date_created"
    # Additional cursor ordering fields
    cursor_ordering_fields = ["date_created"]
    # Only allow these methods. Restricting PUT and PATCH.
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        """
        Return a list of all the open prayers
        for the currently authenticated user.
        """
        user = self.request.user
        return Prayer.objects.filter(
            user=user, status=Prayer.WAITING
        ).order_by("-date_created")
