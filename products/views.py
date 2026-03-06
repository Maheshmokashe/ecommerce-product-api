from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from .models import Product, Category
from .serializers import ProductSerializer, CategorySerializer
import xml.etree.ElementTree as ET
import re

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.filter(is_active=True).select_related('category')
    serializer_class = ProductSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'sku', 'category__name']
    ordering_fields = ['price', 'created_at']

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsAdminUser()]
        return [IsAuthenticated()]


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]


def parse_price(price_str):
    if not price_str:
        return 0.0
    cleaned = re.sub(r'[^\d.]', '', price_str)
    try:
        return float(cleaned)
    except:
        return 0.0


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def upload_xml(request):
    file = request.FILES.get('file')
    if not file:
        return Response({'error': 'No file uploaded'}, status=400)

    try:
        tree = ET.parse(file)
        root = tree.getroot()
    except ET.ParseError as e:
        return Response({'error': f'Invalid XML: {str(e)}'}, status=400)

    if root.tag == 'Product':
        products = [root]
    else:
        products = root.findall('Product')

    loaded = 0
    skipped = 0
    errors = []

    for product in products:
        try:
            # SKU from Variant first
            sku = None
            variant = product.find('Variant')
            if variant is not None:
                sku = variant.findtext('SKU')

            # Fallback to Description
            if not sku:
                desc = product.findtext('Description') or ''
                sku_match = re.search(r'SKU\s+(\S+)', desc)
                if sku_match:
                    sku = sku_match.group(1)

            # Fallback to ProductId
            if not sku:
                sku = product.findtext('ProductId')

            if not sku:
                skipped += 1
                continue

            # Deduplication
            if Product.objects.filter(sku=sku).exists():
                skipped += 1
                continue

            # Name — tag is <n> in Westside XML
            name = product.findtext('n') or ''
            if not name:
                name = product.findtext('Name') or 'Unknown'

            # Brand
            brand = product.findtext('Brand') or ''
            if brand:
                name = f"{name} by {brand}"

            # Category
            category_name = 'Uncategorized'
            first_category = product.find('Category')
            if first_category is not None:
                parts = [p.text for p in first_category.findall('Part') if p.text]
                if len(parts) >= 2:
                    category_name = parts[1]
                elif parts:
                    category_name = parts[0]

            slug = category_name.lower().replace(' ', '-').replace('/', '-').replace('&', 'and')
            category, _ = Category.objects.get_or_create(
                name=category_name,
                defaults={'slug': slug}
            )

            # Price
            price_str = ''
            if variant is not None:
                price_str = variant.findtext('Price') or ''
            if not price_str:
                price_str = product.findtext('Price') or '0'
            price = parse_price(price_str)

            # Stock
            available = ''
            if variant is not None:
                available = variant.findtext('Available') or ''
            if not available:
                available = product.findtext('StockIndicator') or 'false'
            stock = 100 if available.lower() == 'true' else 0

            # URLs
            source_url = product.findtext('ProductURL') or ''

            # Description — strip HTML
            desc_raw = product.findtext('Description') or ''
            desc_clean = re.sub(r'<[^>]+>', ' ', desc_raw).strip()

            Product.objects.create(
                sku=sku,
                name=name[:255],
                description=desc_clean,
                category=category,
                price=price,
                stock=stock,
                source_url=source_url,
                is_active=True
            )
            loaded += 1

        except Exception as e:
            errors.append(f"SKU {sku}: {str(e)}")
            skipped += 1

    return Response({
        'message': f'Done! Loaded: {loaded} | Skipped: {skipped}',
        'loaded': loaded,
        'skipped': skipped,
        'errors': errors[:5]
    })