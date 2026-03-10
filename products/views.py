from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from .models import Product, Category, Retailer
from .serializers import ProductSerializer, CategorySerializer, RetailerSerializer
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import re
import html

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.filter(is_active=True).select_related('category', 'retailer')
    serializer_class = ProductSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'sku', 'category__name', 'brand']
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

def detect_currency(retailer_name, price_str):
    """Detect currency from last word of retailer name (region code)"""
    region_currency_map = {
        # Asia
        'IN':  '₹',
        'KR':  '₩',
        'JP':  '¥',
        'CN':  '¥',
        'HK':  'HK$',
        'SG':  'S$',
        'TH':  '฿',
        'MY':  'RM',
        # Europe
        'UK':  '£',
        'GB':  '£',
        'DE':  '€',
        'FR':  '€',
        'IT':  '€',
        'ES':  '€',
        'NL':  '€',
        'SE':  'kr',
        'NO':  'kr',
        'DK':  'kr',
        'PL':  'zł',
        'CH':  'CHF',
        # Americas
        'US':  '$',
        'CA':  'CA$',
        'MX':  'MX$',
        'BR':  'R$',
        # Middle East & Africa
        'AE':  'AED',
        'SA':  'SAR',
        'ZA':  'R',
        # Oceania
        'AU':  'A$',
        'NZ':  'NZ$',
    }

    # Extract last word as region code
    parts = retailer_name.strip().split()
    if parts:
        region = parts[-1].upper()
        if region in region_currency_map:
            return region_currency_map[region]

    # Fallback — check price string symbol
    if '£' in price_str: return '£'
    if '€' in price_str: return '€'
    if '$' in price_str: return '$'
    if '₹' in price_str: return '₹'

    return '₹'  # final default

def parse_price(price_str):
    if not price_str:
        return 0.0
    # Remove currency symbols and spaces, keep digits, dots, commas
    cleaned = re.sub(r'[^\d.,]', '', price_str).strip()
    if not cleaned:
        return 0.0
    # European format: 1.299,00 → remove dots, replace comma with dot
    if ',' in cleaned and '.' in cleaned:
        cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        # Format like 499,00 — comma is decimal separator
        cleaned = cleaned.replace(',', '.')
    # else normal format like 499.00 or 2499
    try:
        return float(cleaned)
    except:
        return 0.0

def parse_product(product, retailer_obj):
    # SKU
    sku = None
    variant = product.find('Variant')
    if variant is not None:
        sku = variant.findtext('SKU')
    if not sku:
        desc = product.findtext('Description') or ''
        sku_match = re.search(r'SKU\s+(\S+)', desc)
        if sku_match:
            sku = sku_match.group(1)
    if not sku:
        sku = product.findtext('ProductId')
    if not sku:
        return None

    # Name & Brand
    name = product.findtext('n') or product.findtext('Name') or 'Unknown'
    brand = product.findtext('Brand') or ''

    # Category
    category_name = 'Uncategorized'
    first_category = product.find('Category')
    if first_category is not None:
        parts = [p.text for p in first_category.findall('Part') if p.text]
        if len(parts) >= 2:
            category_name = parts[1]
        elif parts:
            category_name = parts[0]

    # Price — check variant first, then product level
    price_str = ''
    sale_price_str = ''
    if variant is not None:
        price_str = variant.findtext('Price') or ''
        sale_price_str = variant.findtext('SalePrice') or variant.findtext('Sale_Price') or ''
    if not price_str:
        price_str = product.findtext('Price') or '0'
    if not sale_price_str:
        sale_price_str = product.findtext('SalePrice') or product.findtext('Sale_Price') or ''

    # Currency
    currency = detect_currency(retailer_obj.name, price_str)

    # Parse prices
    regular_price = parse_price(price_str)
    sale_price = parse_price(sale_price_str) if sale_price_str else None

    # If sale price >= regular price, ignore it
    if sale_price and sale_price >= regular_price:
        sale_price = None

    # Stock
    stock_indicator = product.findtext('StockIndicator') or 'false'
    stock = 1 if stock_indicator.lower() == 'true' else 0

    # Description — strip HTML tags then decode HTML entities
    desc_raw = product.findtext('Description') or ''
    desc_clean = re.sub(r'<[^>]+>', ' ', desc_raw).strip()
    desc_clean = html.unescape(desc_clean)

    # Additional images
    additional_images = []
    color_elem = product.find('Color')
    if color_elem is not None:
        for img in color_elem.findall('AdditionalImageURL'):
            if img.text:
                additional_images.append(img.text)

    # Colors
    colors = []
    for color in product.findall('Color'):
        color_name = color.findtext('n') or color.findtext('Name') or ''
        if color_name and color_name not in colors:
            colors.append(color_name)

    # Sizes
    sizes = [s.text for s in product.findall('Size') if s.text]

    return {
        'sku': sku,
        'name': name[:255],
        'description': desc_clean,
        'category_name': category_name,
        'price': regular_price,
        'sale_price': sale_price,
        'currency': currency,
        'stock': stock,
        'source_url': product.findtext('ProductURL') or '',
        'image_url': product.findtext('PrimaryImageURL') or '',
        'additional_images': ','.join(additional_images[:6]),
        'colors': ','.join(colors),
        'sizes': ','.join(sizes),
        'brand': brand,
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

    products = [root] if root.tag == 'Product' else root.findall('Product')
    if not products:
        return Response({'error': 'No products found in XML'}, status=400)

    first = products[0]
    retailer_name = first.findtext('Retailer') or 'Unknown Retailer'
    slug = re.sub(r'[^a-z0-9]+', '-', retailer_name.lower()).strip('-')

    website = ''
    product_url = first.findtext('ProductURL') or ''
    if product_url:
        parsed = urlparse(product_url)
        website = f"{parsed.scheme}://{parsed.netloc}"

    retailer_obj, created = Retailer.objects.get_or_create(
        name=retailer_name,
        defaults={'slug': slug, 'is_active': True, 'website': website}
    )
    if not retailer_obj.website and website:
        retailer_obj.website = website
        retailer_obj.save()

    loaded = skipped = 0
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
                brand=data['brand'],
                price=data['price'],
                sale_price=data['sale_price'],
                currency=data['currency'],
                stock=data['stock'],
                source_url=data['source_url'],
                image_url=data['image_url'],
                additional_images=data['additional_images'],
                colors=data['colors'],
                sizes=data['sizes'],
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