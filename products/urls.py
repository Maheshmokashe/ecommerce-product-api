from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProductViewSet, CategoryViewSet, RetailerViewSet, UploadLogViewSet, upload_xml

router = DefaultRouter()
router.register(r'products', ProductViewSet)
router.register(r'categories', CategoryViewSet)
router.register(r'retailers', RetailerViewSet)
router.register(r'upload-logs', UploadLogViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('upload-xml/', upload_xml),
]