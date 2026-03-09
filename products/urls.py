from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ProductViewSet, CategoryViewSet, RetailerViewSet, upload_xml

router = DefaultRouter()
router.register(r'products', ProductViewSet)
router.register(r'categories', CategoryViewSet)
router.register(r'retailers', RetailerViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('upload-xml/', upload_xml),
]