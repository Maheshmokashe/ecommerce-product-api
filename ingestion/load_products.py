import os
import sys
import django
import pandas as pd

# Setup Django environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from products.models import Product, Category

def load_from_csv(filepath: str):
    df = pd.read_csv(filepath)
    loaded = 0
    skipped = 0

    for _, row in df.iterrows():
        # Get or create category
        category, _ = Category.objects.get_or_create(
            name=row['category'],
            defaults={'slug': row['category'].lower().replace(' ', '-')}
        )

        # Deduplication — skip if SKU already exists
        if Product.objects.filter(sku=row['sku']).exists():
            print(f"Skipped (duplicate SKU): {row['sku']}")
            skipped += 1
            continue

        Product.objects.create(
            sku=row['sku'],
            name=row['name'],
            description=row.get('description', ''),
            category=category,
            price=row['price'],
            stock=row.get('stock', 0),
            source_url=row.get('source_url', '')
        )
        print(f"Loaded: {row['name']}")
        loaded += 1

    print(f"\n✅ Done! Loaded: {loaded} | Skipped (duplicates): {skipped}")

if __name__ == '__main__':
    load_from_csv('ingestion/sample_products.csv')