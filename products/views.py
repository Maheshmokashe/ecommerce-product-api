from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from .models import Product, Category, Retailer
from .serializers import ProductSerializer, CategorySerializer, RetailerSerializer
import xml.etree.ElementTree as ET
import re

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.filter(is_active=True).select_related('category', 'retailer')
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

class RetailerViewSet(viewsets.ModelViewSet):
    queryset = Retailer.objects.filter(is_active=True)
    serializer_class = RetailerSerializer
    permission_classes = [IsAuthenticated]

def parse_price(price_str):
    if not price_str:
        return 0.0
    cleaned = re.sub(r'[^\d.]', '', price_str)
    try:
        return float(cleaned)
    except:
        return 0.0

def parse_product(product, retailer_obj):
    # SKU — from Variant first
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
        return None

    # Name
    name = product.findtext('n') or product.findtext('Name') or 'Unknown'
    brand = product.findtext('Brand') or ''
    if brand:
        name = f"{name} by {brand}"

    # Category — use second Part (most specific)
    category_name = 'Uncategorized'
    first_category = product.find('Category')
    if first_category is not None:
        parts = [p.text for p in first_category.findall('Part') if p.text]
        if len(parts) >= 2:
            category_name = parts[1]
        elif parts:
            category_name = parts[0]

    # Price — from Variant first, fallback to product level
    price_str = ''
    if variant is not None:
        price_str = variant.findtext('Price') or ''
    if not price_str:
        price_str = product.findtext('Price') or '0'

    # Stock — use StockIndicator directly
    stock_indicator = product.findtext('StockIndicator') or 'false'
    stock = 1 if stock_indicator.lower() == 'true' else 0

    # Description — strip HTML
    desc_raw = product.findtext('Description') or ''
    desc_clean = re.sub(r'<[^>]+>', ' ', desc_raw).strip()

    return {
        'sku': sku,
        'name': name[:255],
        'description': desc_clean,
        'category_name': category_name,
        'price': parse_price(price_str),
        'stock': stock,
        'source_url': product.findtext('ProductURL') or '',
        'image_url': product.findtext('PrimaryImageURL') or '',
        'retailer': retailer_obj,
    }

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

    if not products:
        return Response({'error': 'No products found in XML'}, status=400)

    # Auto-detect retailer from first product's <Retailer> tag
    first = products[0]
    retailer_name = first.findtext('Retailer') or 'Unknown Retailer'
    slug = re.sub(r'[^a-z0-9]+', '-', retailer_name.lower()).strip('-')
    retailer_obj, created = Retailer.objects.get_or_create(
        name=retailer_name,
        defaults={'slug': slug, 'is_active': True}
    )

    loaded = 0
    skipped = 0
    errors = []

    for product in products:
        try:
            data = parse_product(product, retailer_obj)

            if not data:
                skipped += 1
                continue

            if Product.objects.filter(sku=data['sku']).exists():
                skipped += 1
                continue

            cat_slug = data['category_name'].lower().replace(' ', '-').replace('/', '-').replace('&', 'and')
            category, _ = Category.objects.get_or_create(
                name=data['category_name'],
                defaults={'slug': cat_slug}
            )

            Product.objects.create(
                sku=data['sku'],
                name=data['name'],
                description=data['description'],
                category=category,
                retailer=data['retailer'],
                price=data['price'],
                stock=data['stock'],
                source_url=data['source_url'],
                image_url=data['image_url'],
                is_active=True
            )
            loaded += 1

        except Exception as e:
            errors.append(str(e))
            skipped += 1

    return Response({
        'message': f'Done! Retailer: {retailer_name} | Loaded: {loaded} | Skipped: {skipped}',
        'retailer': retailer_name,
        'retailer_created': created,
        'loaded': loaded,
        'skipped': skipped,
        'errors': errors[:5]
    })