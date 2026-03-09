from django.db import models

class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name


class Product(models.Model):
    sku         = models.CharField(max_length=100, unique=True, db_index=True)
    name        = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category    = models.ForeignKey(Category, on_delete=models.SET_NULL,
                                    null=True, related_name='products', db_index=True)
    price       = models.DecimalField(max_digits=10, decimal_places=2, db_index=True)
    stock       = models.PositiveIntegerField(default=0)
    source_url  = models.URLField(blank=True)
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    image_url = models.URLField(max_length=500, blank=True, default='')


    class Meta:
        indexes = [
            models.Index(fields=['sku']),
            models.Index(fields=['category', 'price']),
        ]

    def __str__(self):
        return f"{self.sku} - {self.name}"