from rest_framework import serializers
from .models import Product, Category, Retailer, UploadLog


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'parent', 'level']


class RetailerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Retailer
        fields = ['id', 'name', 'slug', 'website', 'feed_url', 'last_fetched_at', 'is_active', 'created_at']


class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.SerializerMethodField()
    retailer_name = serializers.SerializerMethodField()

    def get_category_name(self, obj):
        if obj.category:
            return obj.category.name
        return ''

    def get_retailer_name(self, obj):
        if obj.retailer:
            return obj.retailer.name
        return ''

    class Meta:
        model = Product
        fields = [
            'id', 'sku', 'name', 'description', 'category', 'category_name',
            'retailer', 'retailer_name', 'brand', 'price', 'sale_price',
            'currency', 'stock', 'source_url', 'image_url', 'additional_images',
            'colors', 'sizes', 'is_active', 'created_at'
        ]


class UploadLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = UploadLog
        fields = [
            'id', 'retailer_name', 'filename', 'loaded', 'skipped',
            'total_found', 'status', 'error_message', 'uploaded_by', 'created_at'
        ]