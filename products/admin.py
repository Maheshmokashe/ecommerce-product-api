from django.contrib import admin
from .models import Product, Category, Retailer

@admin.register(Retailer)
class RetailerAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'slug', 'website', 'is_active']

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'slug']
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['id', 'sku', 'name', 'category', 'retailer', 'price', 'stock', 'is_active']
    list_filter = ['category', 'retailer', 'is_active']
    search_fields = ['sku', 'name']