from django.contrib import admin
from .models import Product, Category, Retailer

# The RetailerAdmin class is registered with the Django admin site to manage the Retailer model. It specifies which fields to display in the list view, including id, name, slug, website, and is_active status.
@admin.register(Retailer)
class RetailerAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'slug', 'website', 'is_active']

# The CategoryAdmin and ProductAdmin classes are registered with the Django admin site to manage the Category and Product models. The list_display attribute specifies which fields to display in the admin list view, while prepopulated_fields automatically generates the slug field based on the name field for categories. The ProductAdmin class also includes list_filter and search_fields for easier navigation and searching within the admin interface.
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'slug']
    prepopulated_fields = {'slug': ('name',)}

# The ProductAdmin class is registered with the Django admin site to manage the Product model. It specifies which fields to display in the list view, allows filtering by category, retailer, and active status, and enables searching by SKU and name.
@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['id', 'sku', 'name', 'category', 'retailer', 'price', 'stock', 'is_active']
    list_filter = ['category', 'retailer', 'is_active']
    search_fields = ['sku', 'name']