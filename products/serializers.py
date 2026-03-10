from rest_framework import serializers
from .models import Product, Category, Retailer, UploadLog

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug']

class RetailerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Retailer
        fields = ['id', 'name', 'slug', 'website', 'is_active']

class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    retailer_name = serializers.CharField(source='retailer.name', read_only=True)

    class Meta:
        model = Product
        fields = ['id', 'sku', 'name', 'description', 'category', 'category_name',
                  'retailer', 'retailer_name', 'brand', 'price', 'sale_price',
                  'currency', 'stock', 'source_url', 'image_url',
                  'additional_images', 'colors', 'sizes', 'is_active']
class UploadLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = UploadLog
        fields = ['id', 'retailer_name', 'filename', 'loaded', 'skipped',
                  'total_found', 'status', 'error_message', 'uploaded_by', 'created_at']