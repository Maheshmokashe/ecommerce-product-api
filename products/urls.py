from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ProductViewSet, CategoryViewSet, RetailerViewSet,
    UploadLogViewSet, upload_xml, bulk_delete_products,
    category_stats, update_feed_url, refresh_feed, analytics,
    qa_data_quality, qa_validate_feed
)

router = DefaultRouter()
router.register(r'products', ProductViewSet)
router.register(r'categories', CategoryViewSet)
router.register(r'retailers', RetailerViewSet)
router.register(r'upload-logs', UploadLogViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('upload-xml/', upload_xml),
    path('bulk-delete/', bulk_delete_products),
    path('category-stats/', category_stats),
    path('analytics/', analytics),
    path('retailers/<int:retailer_id>/update-feed/', update_feed_url),
    path('retailers/<int:retailer_id>/refresh-feed/', refresh_feed),
    path('qa/data-quality/', qa_data_quality),
    path('qa/validate-feed/', qa_validate_feed),
]