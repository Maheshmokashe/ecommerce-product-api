from django.db import models


class Retailer(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    website = models.URLField(blank=True, default='')
    feed_url = models.URLField(blank=True, default='', max_length=500)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children')
    level = models.IntegerField(default=0)

    class Meta:
        unique_together = [['name', 'parent']]

    def __str__(self):
        return self.name


class Product(models.Model):
    sku = models.CharField(max_length=100, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, db_index=True, related_name='primary_products')
    categories = models.ManyToManyField(Category, blank=True, related_name='all_products')
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

    def __str__(self):
        return f'{self.sku} — {self.name}'


class UploadLog(models.Model):
    retailer_name = models.CharField(max_length=100)
    filename = models.CharField(max_length=255, blank=True, default='')
    loaded = models.IntegerField(default=0)
    skipped = models.IntegerField(default=0)
    total_found = models.IntegerField(default=0)
    status = models.CharField(max_length=20, default='success')
    error_message = models.TextField(blank=True, default='')
    uploaded_by      = models.CharField(max_length=100, blank=True, default='')
    duration_seconds = models.FloatField(null=True, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.retailer_name} — {self.created_at}'


# ─────────────────────────────────────────────
# Price History — snapshot on every upload/refresh
# ─────────────────────────────────────────────

class PriceHistory(models.Model):
    CHANGE_TYPES = [
        ('price_up',        'Price Increased'),
        ('price_down',      'Price Decreased'),
        ('sale_added',      'Sale Price Added'),
        ('sale_removed',    'Sale Price Removed'),
        ('sale_changed',    'Sale Price Changed'),
        ('new_product',     'New Product'),
    ]

    sku           = models.CharField(max_length=100, db_index=True)
    product_name  = models.CharField(max_length=255, blank=True, default='')
    retailer_name = models.CharField(max_length=100, db_index=True)
    source_url    = models.URLField(max_length=500, blank=True, default='')

    old_price     = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    new_price     = models.DecimalField(max_digits=10, decimal_places=2)
    old_sale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    new_sale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    change_type   = models.CharField(max_length=20, choices=CHANGE_TYPES, db_index=True)
    change_pct    = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    currency      = models.CharField(max_length=10, blank=True, default='₹')

    detected_at   = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-detected_at']
        indexes = [
            models.Index(fields=['retailer_name', 'detected_at']),
            models.Index(fields=['sku', 'detected_at']),
        ]

    def __str__(self):
        return f'{self.sku} {self.change_type} @ {self.detected_at}'