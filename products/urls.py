from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ProductViewSet, CategoryViewSet, RetailerViewSet, UploadLogViewSet,
    upload_xml, bulk_delete_products, category_stats,
    update_feed_url, refresh_feed, analytics,
    qa_data_quality, qa_validate_feed,
    qa_fix_suggestions, qa_advanced_rules,
    qa_retailer_comparison, qa_upload_flags,
    qa_price_changes,
)

router = DefaultRouter()
router.register(r'products',    ProductViewSet)
router.register(r'categories',  CategoryViewSet)
router.register(r'retailers',   RetailerViewSet)
router.register(r'upload-logs', UploadLogViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('upload-xml/',                               upload_xml),
    path('bulk-delete/',                              bulk_delete_products),
    path('category-stats/',                           category_stats),
    path('analytics/',                                analytics),
    path('retailers/<int:retailer_id>/update-feed/',  update_feed_url),
    path('retailers/<int:retailer_id>/refresh-feed/', refresh_feed),

    # QA endpoints
    path('qa/data-quality/',        qa_data_quality),
    path('qa/validate-feed/',       qa_validate_feed),
    path('qa/fix-suggestions/',     qa_fix_suggestions),
    path('qa/advanced-rules/',      qa_advanced_rules),
    path('qa/retailer-comparison/', qa_retailer_comparison),
    path('qa/upload-flags/',        qa_upload_flags),
    path('qa/price-changes/',       qa_price_changes),
]