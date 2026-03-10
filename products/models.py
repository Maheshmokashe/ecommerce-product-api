from django.db import models

class Retailer(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    website = models.URLField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    def __str__(self):
        return self.name

class Product(models.Model):
    sku = models.CharField(max_length=100, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, db_index=True)
    retailer = models.ForeignKey(Retailer, on_delete=models.CASCADE, null=True, blank=True)
    brand = models.CharField(max_length=255, blank=True, default='')
    price = models.DecimalField(max_digits=10, decimal_places=2, db_index=True)
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, blank=True, default='₹')
    stock = models.IntegerField(default=0)
    source_url = models.URLField(max_length=500, blank=True, default='')
    image_url = models.URLField(max_length=500, blank=True, default='')
    additional_images = models.TextField(blank=True, default='')
    colors = models.CharField(max_length=500, blank=True, default='')
    sizes = models.CharField(max_length=500, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['sku']),
            models.Index(fields=['category', 'price']),
        ]

    def __str__(self):
        return self.name


class UploadLog(models.Model):
    retailer_name = models.CharField(max_length=100)
    filename = models.CharField(max_length=255, blank=True, default='')
    loaded = models.IntegerField(default=0)
    skipped = models.IntegerField(default=0)
    total_found = models.IntegerField(default=0)
    status = models.CharField(max_length=20, default='success')  # success / failed
    error_message = models.TextField(blank=True, default='')
    uploaded_by = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.retailer_name} - {self.created_at}"