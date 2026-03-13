from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from .models import Product, Category, Retailer, UploadLog, PriceHistory
from .serializers import ProductSerializer, CategorySerializer, RetailerSerializer, UploadLogSerializer
from urllib.parse import urlparse
from django.db import models
import xml.etree.ElementTree as ET
import re
import html


# ─────────────────────────────────────────────
# ViewSets
# ─────────────────────────────────────────────

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


class UploadLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = UploadLog.objects.all().order_by('-created_at')
    serializer_class = UploadLogSerializer
    permission_classes = [IsAuthenticated]


# ─────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────

def detect_currency(retailer_name, price_str):
    region_currency_map = {
        'IN': '₹', 'KR': '₩', 'JP': '¥', 'CN': '¥',
        'HK': 'HK$', 'SG': 'S$', 'TH': '฿', 'MY': 'RM',
        'UK': '£', 'GB': '£', 'DE': '€', 'FR': '€',
        'IT': '€', 'ES': '€', 'NL': '€', 'SE': 'kr',
        'NO': 'kr', 'DK': 'kr', 'PL': 'zł', 'CH': 'CHF',
        'US': '$', 'CA': 'CA$', 'MX': 'MX$', 'BR': 'R$',
        'AE': 'AED', 'SA': 'SAR', 'ZA': 'R',
        'AU': 'A$', 'NZ': 'NZ$',
    }
    parts = retailer_name.strip().split()
    if parts:
        region = parts[-1].upper()
        if region in region_currency_map:
            return region_currency_map[region]
    if '£' in price_str: return '£'
    if '€' in price_str: return '€'
    if '$' in price_str: return '$'
    if '₹' in price_str: return '₹'
    return '₹'


def parse_price(price_str):
    if not price_str:
        return 0.0
    cleaned = re.sub(r'[^\d.,]', '', price_str).strip()
    if not cleaned:
        return 0.0
    if ',' in cleaned and '.' in cleaned:
        if cleaned.index(',') > cleaned.index('.'):
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    elif ',' in cleaned:
        parts = cleaned.split(',')
        last_part = parts[-1]
        if len(last_part) == 2:
            cleaned = cleaned.replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    try:
        return float(cleaned)
    except:
        return 0.0


def get_or_create_category_tree(parts, cache):
    """
    Build category tree from parts list.
    Uses in-memory cache dict to avoid repeated DB hits.
    cache key = "Part1||Part2||Part3"
    """
    if not parts:
        key = '__uncategorized__'
        if key not in cache:
            cat, _ = Category.objects.get_or_create(
                name='Uncategorized',
                parent=None,
                defaults={'slug': 'uncategorized', 'level': 0}
            )
            cache[key] = cat
        return cache[key]

    parent = None
    category = None
    for level, part in enumerate(parts):
        # Build cache key up to this level e.g. "Women", "Women||Western Wear"
        path_key = '||'.join(parts[:level + 1])
        if path_key not in cache:
            slug = re.sub(r'[^a-z0-9]+', '-', part.lower()).strip('-')
            category, _ = Category.objects.get_or_create(
                name=part,
                parent=parent,
                defaults={'slug': slug, 'level': level}
            )
            cache[path_key] = category
        else:
            category = cache[path_key]
        parent = category

    return category


def get_ancestors(cat, ancestor_cache):
    """
    Return the category itself plus all ancestors.
    Uses ancestor_cache to avoid repeated parent traversals.
    """
    if cat.id in ancestor_cache:
        return ancestor_cache[cat.id]

    ancestors = []
    current = cat
    while current is not None:
        if current not in ancestors:
            ancestors.append(current)
        current = current.parent

    ancestor_cache[cat.id] = ancestors
    return ancestors


def build_all_categories_with_ancestors(all_category_parts, cat_cache, ancestor_cache):
    """
    Given all category paths for one product,
    return unique flat list of all categories + ancestors.
    """
    all_cats = []
    seen_ids = set()
    for parts in all_category_parts:
        leaf_cat = get_or_create_category_tree(parts, cat_cache)
        if leaf_cat:
            for ancestor in get_ancestors(leaf_cat, ancestor_cache):
                if ancestor.id not in seen_ids:
                    all_cats.append(ancestor)
                    seen_ids.add(ancestor.id)
    return all_cats


def parse_product(product, retailer_obj):
    # ── SKU ──────────────────────────────────
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

    # ── Basic fields ─────────────────────────
    name = product.findtext('n') or product.findtext('Name') or 'Unknown'
    brand = product.findtext('Brand') or ''

    # ── ALL category paths from ALL <Category> tags ──────────────
    all_category_parts = []
    for cat_elem in product.findall('Category'):
        parts = [p.text.strip() for p in cat_elem.findall('Part') if p.text and p.text.strip()]
        if parts:
            all_category_parts.append(parts)

    # Primary category = longest path (most specific)
    primary_parts = []
    if all_category_parts:
        primary_parts = max(all_category_parts, key=len)

    # ── Price ─────────────────────────────────
    # Strategy: scan ALL variants to find a valid price + sale price.
    # Some variants have no <Price> tag (e.g. size 30 and 40 above).
    # We pick the first variant that has a valid non-zero price.
    # If no variant has a price, fall back to product-level <Price>.
    # Product-level price may be a range like "₹1874.25 - ₹2399.0"
    # — in that case we take the first number before " - ".

    all_variants = product.findall('Variant')

    price_str = ''
    sale_price_str = ''

    # Pass 1: find first variant that has a <Price> tag
    for v in all_variants:
        v_price = v.findtext('Price') or ''
        if v_price.strip():
            price_str = v_price.strip()
            sale_price_str = v.findtext('SalePrice') or v.findtext('Sale_Price') or ''
            break

    # Pass 2: if still no price from variants, use product-level price
    if not price_str:
        raw = product.findtext('Price') or '0'
        # Handle range strings like "₹1874.25 - ₹2399.0" → take first part
        price_str = raw.split(' - ')[0].strip() if ' - ' in raw else raw

    # Fall back sale price from product level if variant didn't supply one
    if not sale_price_str:
        raw_sale = product.findtext('SalePrice') or product.findtext('Sale_Price') or ''
        # Handle range strings like "₹1439.0 - ₹1499.0" → take first part
        sale_price_str = raw_sale.split(' - ')[0].strip() if ' - ' in raw_sale else raw_sale

    currency = detect_currency(retailer_obj.name, price_str)
    regular_price = parse_price(price_str)
    sale_price = parse_price(sale_price_str) if sale_price_str else None
    if sale_price and sale_price >= regular_price:
        sale_price = None

    # ── Stock ─────────────────────────────────
    stock_indicator = product.findtext('StockIndicator') or 'false'
    stock = 1 if stock_indicator.lower() == 'true' else 0

    # ── Description ───────────────────────────
    desc_raw = product.findtext('Description') or ''
    desc_clean = re.sub(r'<[^>]+>', ' ', desc_raw).strip()
    desc_clean = html.unescape(desc_clean)

    # ── Images ────────────────────────────────
    additional_images = []
    color_elem = product.find('Color')
    if color_elem is not None:
        for img in color_elem.findall('AdditionalImageURL'):
            if img.text:
                additional_images.append(img.text)

    # ── Colors ────────────────────────────────
    colors = []
    for color in product.findall('Color'):
        color_name = color.findtext('n') or color.findtext('Name') or ''
        if color_name and color_name not in colors:
            colors.append(color_name)

    # ── Sizes ─────────────────────────────────
    sizes = [s.text for s in product.findall('Size') if s.text]

    return {
        'sku': sku,
        'name': name[:255],
        'description': desc_clean,
        'primary_category_parts': primary_parts,
        'all_category_parts': all_category_parts,
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


# ─────────────────────────────────────────────
# Upload XML  (optimized with bulk insert + cache)
# ─────────────────────────────────────────────

BULK_BATCH_SIZE = 500  # insert 500 products at a time

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def upload_xml(request):
    file = request.FILES.get('file')
    if not file:
        return Response({'error': 'No file uploaded'}, status=400)

    filename = file.name
    uploaded_by = request.user.username
    import time as _time
    _start = _time.time()

    try:
        tree = ET.parse(file)
        root = tree.getroot()
    except ET.ParseError as e:
        UploadLog.objects.create(
            retailer_name='Unknown', filename=filename,
            loaded=0, skipped=0, total_found=0,
            status='failed', error_message=str(e), uploaded_by=uploaded_by
        )
        return Response({'error': f'Invalid XML: {str(e)}'}, status=400)

    products = [root] if root.tag == 'Product' else root.findall('Product')
    if not products:
        return Response({'error': 'No products found in XML'}, status=400)

    # Auto-detect retailer
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

    # Pre-load existing SKUs to avoid per-product DB check
    existing_skus = set(
        Product.objects.filter(retailer=retailer_obj)
        .values_list('sku', flat=True)
    )

    loaded = skipped = 0
    errors = []
    total_found = len(products)

    # Shared caches for the entire upload — built once, reused for all products
    cat_cache = {}       # { "Women||Western Wear": <Category obj> }
    ancestor_cache = {}  # { category_id: [cat, parent, grandparent...] }

    # Batch buffers
    product_batch = []           # Product objects to bulk_create
    m2m_map = {}                 # { sku: [category_ids] } for M2M after bulk_create

    def flush_batch():
        """Insert current batch into DB and set M2M categories."""
        nonlocal loaded
        if not product_batch:
            return

        # Bulk insert — one query for entire batch!
        created_products = Product.objects.bulk_create(
            product_batch,
            ignore_conflicts=True  # skip if SKU already exists
        )

        # Set M2M categories for each created product
        # Re-fetch with SKUs to get actual DB IDs
        skus_in_batch = [p.sku for p in product_batch]
        db_products = {
            p.sku: p for p in Product.objects.filter(sku__in=skus_in_batch)
        }

        m2m_through = Product.categories.through  # the junction table
        through_objects = []
        for sku, cat_ids in m2m_map.items():
            prod = db_products.get(sku)
            if prod:
                for cat_id in cat_ids:
                    through_objects.append(
                        m2m_through(product_id=prod.id, category_id=cat_id)
                    )

        if through_objects:
            m2m_through.objects.bulk_create(through_objects, ignore_conflicts=True)

        loaded += len(created_products)
        product_batch.clear()
        m2m_map.clear()

    for product in products:
        try:
            data = parse_product(product, retailer_obj)
            if not data:
                skipped += 1
                continue

            if data['sku'] in existing_skus:
                skipped += 1
                continue

            # Mark as seen so duplicates within same XML are skipped
            existing_skus.add(data['sku'])

            # Resolve categories using cache
            primary_category = get_or_create_category_tree(
                data['primary_category_parts'], cat_cache
            )
            all_cats = build_all_categories_with_ancestors(
                data['all_category_parts'], cat_cache, ancestor_cache
            )

            # Add to batch (no DB write yet)
            product_batch.append(Product(
                sku=data['sku'],
                name=data['name'],
                description=data['description'],
                category=primary_category,
                retailer=retailer_obj,
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
            ))

            m2m_map[data['sku']] = [c.id for c in all_cats]

            # Flush every BULK_BATCH_SIZE products
            if len(product_batch) >= BULK_BATCH_SIZE:
                flush_batch()

        except Exception as e:
            errors.append(str(e))
            skipped += 1

    # Flush remaining products
    flush_batch()

    UploadLog.objects.create(
        retailer_name=retailer_name, filename=filename,
        loaded=loaded, skipped=skipped, total_found=total_found,
        status='success', error_message=', '.join(errors[:3]),
        uploaded_by=uploaded_by,
        duration_seconds=round(_time.time() - _start, 2)
    )

    return Response({
        'message': f'Done! Retailer: {retailer_name} | Loaded: {loaded} | Skipped: {skipped}',
        'retailer': retailer_name,
        'retailer_created': created,
        'loaded': loaded,
        'skipped': skipped,
        'total_found': total_found,
        'errors': errors[:5]
    })


# ─────────────────────────────────────────────
# Category Stats
# ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def category_stats(request):
    retailer_name = request.query_params.get('retailer', None)

    def build_tree(parent=None):
        cats = Category.objects.filter(parent=parent).order_by('name')
        result = []
        for c in cats:
            # Use M2M all_products — accurate because ancestors are stored too
            qs = c.all_products.all()
            if retailer_name:
                qs = qs.filter(retailer__name=retailer_name)
            total = qs.count()
            available = qs.filter(stock=1).count()
            if total == 0:
                continue
            result.append({
                'id': c.id,
                'name': c.name,
                'slug': c.slug,
                'level': c.level,
                'total': total,
                'available': available,
                'children': build_tree(parent=c)
            })
        return result

    tree = build_tree(parent=None)
    return Response(tree)


# ─────────────────────────────────────────────
# Bulk Delete
# ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bulk_delete_products(request):
    ids = request.data.get('ids', [])
    if not ids:
        return Response({'error': 'No product IDs provided'}, status=400)
    deleted_count, _ = Product.objects.filter(id__in=ids).delete()
    return Response({
        'message': f'Successfully deleted {deleted_count} products',
        'deleted': deleted_count
    })


# ─────────────────────────────────────────────
# Feed URL Management
# ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_feed_url(request, retailer_id):
    try:
        retailer = Retailer.objects.get(id=retailer_id)
    except Retailer.DoesNotExist:
        return Response({'error': 'Retailer not found'}, status=404)
    feed_url = request.data.get('feed_url', '').strip()
    if not feed_url:
        return Response({'error': 'feed_url is required'}, status=400)
    retailer.feed_url = feed_url
    retailer.save()
    return Response({'message': 'Feed URL updated', 'feed_url': feed_url})


# ─────────────────────────────────────────────
# Refresh Feed  (optimized with bulk insert + cache)
# ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def refresh_feed(request, retailer_id):
    import urllib.request
    import time as _time
    from django.utils import timezone

    _start = _time.time()

    try:
        retailer = Retailer.objects.get(id=retailer_id)
    except Retailer.DoesNotExist:
        return Response({'error': 'Retailer not found'}, status=404)

    if not retailer.feed_url:
        return Response({'error': 'No feed URL set for this retailer'}, status=400)

    try:
        with urllib.request.urlopen(retailer.feed_url, timeout=30) as response:
            xml_data = response.read()
    except Exception as e:
        return Response({'error': f'Failed to fetch feed: {str(e)}'}, status=400)

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        return Response({'error': f'Invalid XML: {str(e)}'}, status=400)

    products = [root] if root.tag == 'Product' else root.findall('Product')
    if not products:
        return Response({'error': 'No products found in feed'}, status=400)

    loaded = skipped = 0
    errors = []
    total_found = len(products)

    cat_cache = {}
    ancestor_cache = {}

    for product in products:
        try:
            data = parse_product(product, retailer)
            if not data:
                skipped += 1
                continue

            primary_category = get_or_create_category_tree(
                data['primary_category_parts'], cat_cache
            )
            all_cats = build_all_categories_with_ancestors(
                data['all_category_parts'], cat_cache, ancestor_cache
            )

            product_obj, was_created = Product.objects.update_or_create(
                sku=data['sku'],
                defaults={
                    'name': data['name'],
                    'description': data['description'],
                    'category': primary_category,
                    'retailer': retailer,
                    'brand': data['brand'],
                    'price': data['price'],
                    'sale_price': data['sale_price'],
                    'currency': data['currency'],
                    'stock': data['stock'],
                    'source_url': data['source_url'],
                    'image_url': data['image_url'],
                    'additional_images': data['additional_images'],
                    'colors': data['colors'],
                    'sizes': data['sizes'],
                    'is_active': True,
                }
            )

            # ── Price snapshot ───────────────────
            snapshot_price_change(product_obj, data, was_created)

            if all_cats:
                product_obj.categories.set(all_cats)

            loaded += 1

        except Exception as e:
            errors.append(str(e))
            skipped += 1

    retailer.last_fetched_at = timezone.now()
    retailer.save()

    UploadLog.objects.create(
        retailer_name=retailer.name, filename=retailer.feed_url,
        loaded=loaded, skipped=skipped, total_found=total_found,
        status='success', error_message=', '.join(errors[:3]),
        uploaded_by=request.user.username,
        duration_seconds=round(_time.time() - _start, 2)
    )

    return Response({
        'message': f'Feed refreshed! Loaded: {loaded} | Skipped: {skipped}',
        'loaded': loaded,
        'skipped': skipped,
        'total_found': total_found,
        'last_fetched_at': retailer.last_fetched_at,
        'errors': errors[:5]
    })


# ─────────────────────────────────────────────
# Analytics
# ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def analytics(request):
    from django.db.models import Count, Avg, Min, Max, Q
    from django.db.models.functions import TruncDate

    # ── Product Analytics ─────────────────────
    total_products = Product.objects.filter(is_active=True).count()
    in_stock = Product.objects.filter(is_active=True, stock=1).count()
    out_of_stock = Product.objects.filter(is_active=True, stock=0).count()
    with_sale = Product.objects.filter(is_active=True, sale_price__isnull=False).count()
    without_sale = total_products - with_sale

    products_per_retailer = list(
        Product.objects.filter(is_active=True)
        .values('retailer__name')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    top_brands = list(
        Product.objects.filter(is_active=True)
        .exclude(brand='')
        .values('brand')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    products_over_time = list(
        Product.objects.filter(is_active=True)
        .annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(count=Count('id'))
        .order_by('date')
    )
    for p in products_over_time:
        p['date'] = str(p['date'])

    # ── Price Analytics ───────────────────────
    avg_price_per_retailer = list(
        Product.objects.filter(is_active=True)
        .values('retailer__name', 'currency')
        .annotate(avg_price=Avg('price'), min_price=Min('price'), max_price=Max('price'))
        .order_by('-avg_price')
    )
    for r in avg_price_per_retailer:
        r['avg_price'] = round(float(r['avg_price'] or 0), 2)
        r['min_price'] = round(float(r['min_price'] or 0), 2)
        r['max_price'] = round(float(r['max_price'] or 0), 2)

    ranges = [
        ('Under 500',   Q(price__lt=500)),
        ('500-1000',    Q(price__gte=500,   price__lt=1000)),
        ('1000-2000',   Q(price__gte=1000,  price__lt=2000)),
        ('2000-5000',   Q(price__gte=2000,  price__lt=5000)),
        ('5000-10000',  Q(price__gte=5000,  price__lt=10000)),
        ('Above 10000', Q(price__gte=10000)),
    ]
    price_distribution = [
        {'range': label, 'count': Product.objects.filter(is_active=True).filter(q).count()}
        for label, q in ranges
    ]

    # ── Category Analytics ────────────────────
    top_categories = list(
        Product.objects.filter(is_active=True)
        .exclude(category=None)
        .values('category__name')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    category_availability = list(
        Product.objects.filter(is_active=True)
        .exclude(category=None)
        .values('category__name')
        .annotate(
            total=Count('id'),
            available=Count('id', filter=Q(stock=1))
        )
        .order_by('-total')[:10]
    )
    for c in category_availability:
        c['availability_pct'] = round((c['available'] / c['total']) * 100, 1) if c['total'] else 0

    # ── Upload Analytics ──────────────────────
    total_uploads = UploadLog.objects.count()
    successful_uploads = UploadLog.objects.filter(status='success').count()
    failed_uploads = UploadLog.objects.filter(status='failed').count()
    total_loaded = sum(UploadLog.objects.values_list('loaded', flat=True))
    total_skipped = sum(UploadLog.objects.values_list('skipped', flat=True))

    uploads_per_retailer = list(
        UploadLog.objects.values('retailer_name')
        .annotate(uploads=Count('id'))
        .order_by('-uploads')
    )

    upload_timeline = list(
        UploadLog.objects
        .annotate(date=TruncDate('created_at'))
        .values('date', 'retailer_name')
        .annotate(loaded=Count('loaded'))
        .order_by('date')
    )
    for u in upload_timeline:
        u['date'] = str(u['date'])

    return Response({
        'product_analytics': {
            'total_products': total_products,
            'in_stock': in_stock,
            'out_of_stock': out_of_stock,
            'with_sale': with_sale,
            'without_sale': without_sale,
            'products_per_retailer': products_per_retailer,
            'top_brands': top_brands,
            'products_over_time': products_over_time,
        },
        'price_analytics': {
            'avg_price_per_retailer': avg_price_per_retailer,
            'price_distribution': price_distribution,
        },
        'category_analytics': {
            'top_categories': top_categories,
            'category_availability': category_availability,
        },
        'upload_analytics': {
            'total_uploads': total_uploads,
            'successful_uploads': successful_uploads,
            'failed_uploads': failed_uploads,
            'total_loaded': total_loaded,
            'total_skipped': total_skipped,
            'uploads_per_retailer': uploads_per_retailer,
            'upload_timeline': upload_timeline,
        }
    })


# ─────────────────────────────────────────────
# QA — Data Quality Report
# ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def qa_data_quality(request):
    from django.db.models import Count, Q

    retailer_filter = request.GET.get('retailer', None)
    qs = Product.objects.filter(is_active=True).select_related('retailer', 'category')
    if retailer_filter:
        qs = qs.filter(retailer__name=retailer_filter)

    total = qs.count()
    if total == 0:
        return Response({'error': 'No products found'}, status=400)

    # ── Field completeness issues ─────────────
    missing_image       = qs.filter(Q(image_url='') | Q(image_url__isnull=True))
    missing_description = qs.filter(Q(description='') | Q(description__isnull=True))
    missing_brand       = qs.filter(Q(brand='') | Q(brand__isnull=True))
    missing_category    = qs.filter(category__isnull=True)
    missing_colors      = qs.filter(Q(colors='') | Q(colors__isnull=True))
    missing_sizes       = qs.filter(Q(sizes='') | Q(sizes__isnull=True))

    # ── Price issues ──────────────────────────
    zero_price          = qs.filter(price=0)
    negative_price      = qs.filter(price__lt=0)
    sale_exceeds_price  = qs.filter(sale_price__isnull=False, sale_price__gte=models.F('price'))
    unusually_high      = qs.filter(price__gt=500000)

    # ── Stock ─────────────────────────────────
    out_of_stock        = qs.filter(stock=0)
    in_stock            = qs.filter(stock=1)

    # ── Per-retailer quality scores ───────────
    retailers = Retailer.objects.filter(is_active=True)
    retailer_scores = []
    for r in retailers:
        r_qs = qs.filter(retailer=r)
        r_total = r_qs.count()
        if r_total == 0:
            continue
        issues = (
            r_qs.filter(Q(image_url='') | Q(image_url__isnull=True)).count() +
            r_qs.filter(Q(description='') | Q(description__isnull=True)).count() +
            r_qs.filter(Q(brand='') | Q(brand__isnull=True)).count() +
            r_qs.filter(category__isnull=True).count() +
            r_qs.filter(price=0).count() +
            r_qs.filter(sale_price__isnull=False, sale_price__gte=models.F('price')).count()
        )
        max_issues = r_total * 6
        score = round(((max_issues - issues) / max_issues) * 100, 1) if max_issues > 0 else 100.0
        retailer_scores.append({
            'retailer': r.name,
            'total': r_total,
            'issues': issues,
            'score': score,
        })
    retailer_scores.sort(key=lambda x: x['score'])

    # ── Sample bad products (first 10 per issue) ──
    def sample(queryset, fields=['id', 'sku', 'name', 'retailer__name', 'price', 'sale_price',
                                  'image_url', 'description', 'brand', 'category__name']):
        return list(queryset.values(*fields)[:10])

    return Response({
        'summary': {
            'total_products': total,
            'total_issues': (
                missing_image.count() + missing_description.count() +
                missing_brand.count() + missing_category.count() +
                zero_price.count() + sale_exceeds_price.count()
            ),
            'overall_score': round((1 - (
                missing_image.count() + missing_description.count() +
                missing_brand.count() + missing_category.count() +
                zero_price.count() + sale_exceeds_price.count()
            ) / (total * 6) * 1) * 100, 1),
        },
        'field_issues': {
            'missing_image':       {'count': missing_image.count(),       'pct': round(missing_image.count()/total*100,1),       'samples': sample(missing_image)},
            'missing_description': {'count': missing_description.count(), 'pct': round(missing_description.count()/total*100,1), 'samples': sample(missing_description)},
            'missing_brand':       {'count': missing_brand.count(),       'pct': round(missing_brand.count()/total*100,1),       'samples': sample(missing_brand)},
            'missing_category':    {'count': missing_category.count(),    'pct': round(missing_category.count()/total*100,1),    'samples': sample(missing_category)},
            'missing_colors':      {'count': missing_colors.count(),      'pct': round(missing_colors.count()/total*100,1),      'samples': sample(missing_colors)},
            'missing_sizes':       {'count': missing_sizes.count(),       'pct': round(missing_sizes.count()/total*100,1),       'samples': sample(missing_sizes)},
        },
        'price_issues': {
            'zero_price':         {'count': zero_price.count(),         'pct': round(zero_price.count()/total*100,1),         'samples': sample(zero_price)},
            'negative_price':     {'count': negative_price.count(),     'pct': round(negative_price.count()/total*100,1),     'samples': sample(negative_price)},
            'sale_exceeds_price': {'count': sale_exceeds_price.count(), 'pct': round(sale_exceeds_price.count()/total*100,1), 'samples': sample(sale_exceeds_price)},
            'unusually_high':     {'count': unusually_high.count(),     'pct': round(unusually_high.count()/total*100,1),     'samples': sample(unusually_high)},
        },
        'stock_summary': {
            'in_stock':     in_stock.count(),
            'out_of_stock': out_of_stock.count(),
            'in_stock_pct': round(in_stock.count()/total*100, 1),
        },
        'retailer_scores': retailer_scores,
    })


# ─────────────────────────────────────────────
# QA — XML Feed Validator (dry run, no DB save)
# ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def qa_validate_feed(request):
    file = request.FILES.get('file')
    if not file:
        return Response({'error': 'No file uploaded'}, status=400)

    try:
        xml_data = file.read()
        root = ET.fromstring(xml_data)
    except ET.ParseError as e:
        return Response({'error': f'Invalid XML: {str(e)}'}, status=400)

    products = [root] if root.tag == 'Product' else root.findall('Product')
    if not products:
        return Response({'error': 'No <Product> elements found in XML'}, status=400)

    total = len(products)

    # ── Per-product validation ────────────────
    issues_list = []
    field_counts = {
        'has_sku': 0, 'has_name': 0, 'has_price': 0,
        'has_image': 0, 'has_description': 0, 'has_brand': 0,
        'has_category': 0, 'has_stock': 0, 'has_colors': 0, 'has_sizes': 0,
    }
    price_format_issues = 0
    zero_price_count    = 0
    sale_exceeds_count  = 0
    duplicate_skus      = {}
    parsed_samples      = []

    for i, p in enumerate(products):
        issues = []

        # SKU
        sku = ''
        variant = p.find('Variant')
        if variant is not None:
            sku_el = variant.find('SKU')
            if sku_el is not None and sku_el.text:
                sku = sku_el.text.strip()
        if not sku:
            pid = p.find('ProductId')
            if pid is not None and pid.text:
                sku = pid.text.strip()
        if sku:
            field_counts['has_sku'] += 1
            duplicate_skus[sku] = duplicate_skus.get(sku, 0) + 1
        else:
            issues.append('Missing SKU')

        # Name
        name_el = p.find('n') or p.find('Name')
        name = (name_el.text or '').strip() if name_el is not None else ''
        if name:
            field_counts['has_name'] += 1
        else:
            issues.append('Missing name')

        # Price — scan ALL variants for first valid price
        price_raw = ''
        all_vars = p.findall('Variant')
        for v in all_vars:
            vp = v.findtext('Price') or ''
            if vp.strip():
                price_raw = vp.strip()
                break
        if not price_raw:
            raw = p.findtext('Price') or ''
            price_raw = raw.split(' - ')[0].strip() if ' - ' in raw else raw.strip()

        if price_raw:
            field_counts['has_price'] += 1
            try:
                price_val = parse_price(price_raw)
                if price_val == 0:
                    zero_price_count += 1
                    issues.append('Zero price')
                # Check sale price — scan all variants
                sale_raw = ''
                for v in all_vars:
                    vsp = v.findtext('SalePrice') or v.findtext('Sale_Price') or ''
                    if vsp.strip():
                        sale_raw = vsp.strip()
                        break
                if not sale_raw:
                    raw_s = p.findtext('SalePrice') or p.findtext('Sale_Price') or ''
                    sale_raw = raw_s.split(' - ')[0].strip() if ' - ' in raw_s else raw_s.strip()
                if sale_raw:
                    sale_val = parse_price(sale_raw)
                    if sale_val >= price_val:
                        sale_exceeds_count += 1
                        issues.append(f'Sale price ({sale_raw}) >= regular price ({price_raw})')
            except Exception:
                price_format_issues += 1
                issues.append(f'Price format unrecognized: {price_raw}')
        else:
            issues.append('Missing price')

        # Image — check PrimaryImageURL, ImageURL, Image, or inside <Color>
        img = ''
        for img_tag in ['PrimaryImageURL', 'ImageURL', 'Image', 'MainImage']:
            el = p.find(img_tag)
            if el is not None and el.text and el.text.strip():
                img = el.text.strip()
                break
        if not img:
            color_el = p.find('Color')
            if color_el is not None:
                for img_tag in ['ImageURL', 'PrimaryImageURL']:
                    el = color_el.find(img_tag)
                    if el is not None and el.text and el.text.strip():
                        img = el.text.strip()
                        break
        if img:
            field_counts['has_image'] += 1
        else:
            issues.append('Missing image URL')

        # Description
        desc_el = p.find('Description')
        if desc_el is not None and desc_el.text and len(desc_el.text.strip()) > 5:
            field_counts['has_description'] += 1
        else:
            issues.append('Missing or very short description')

        # Brand
        brand = ''
        for tag in ['Brand', 'BrandName', 'Manufacturer']:
            el = p.find(tag)
            if el is not None and el.text and el.text.strip():
                brand = el.text.strip()
                break
        if brand:
            field_counts['has_brand'] += 1
        else:
            issues.append('Missing brand')

        # Category
        cat_els = p.findall('Category')
        if cat_els:
            field_counts['has_category'] += 1
        else:
            issues.append('Missing category')

        # Stock — check <StockIndicator> at product level OR <Available> inside Variant
        stock_found = False
        for tag in ['StockIndicator', 'Stock', 'InStock']:
            el = p.find(tag)
            if el is not None and el.text and el.text.strip():
                stock_found = True
                break
        if not stock_found:
            for v in p.findall('Variant'):
                for tag in ['Available', 'InStock']:
                    el = v.find(tag)
                    if el is not None and el.text and el.text.strip():
                        stock_found = True
                        break
                if stock_found:
                    break
        if stock_found:
            field_counts['has_stock'] += 1
        else:
            issues.append('Missing stock indicator')

        # Colors — check <Color><n> or <Color><Name>
        colors = []
        for c in p.findall('Color'):
            cn = c.find('n') or c.find('Name')
            if cn is not None and cn.text and cn.text.strip():
                colors.append(cn.text.strip())
        if colors:
            field_counts['has_colors'] += 1

        # Sizes
        sizes = [s.text.strip() for s in p.findall('Size') if s.text]
        if sizes:
            field_counts['has_sizes'] += 1

        if issues:
            issues_list.append({
                'index': i + 1,
                'sku': sku or '(no SKU)',
                'name': name or '(no name)',
                'issues': issues,
            })

        # First 5 as parsed preview
        if i < 5:
            parsed_samples.append({
                'sku': sku,
                'name': name,
                'price': price_raw,
                'brand': brand or '—',
                'category': ' > '.join(
                    [part.text.strip() for part in (cat_els[0].findall('Part') if cat_els else []) if part.text]
                ) if cat_els else '',
                'colors': ', '.join(colors[:3]),
                'sizes': ', '.join(sizes[:5]),
                'has_image': field_counts['has_image'] > (i - 1),
            })

    # Duplicate SKUs
    duplicates = {sku: count for sku, count in duplicate_skus.items() if count > 1}

    # Field coverage percentages
    coverage = {k: {'count': v, 'pct': round(v / total * 100, 1)} for k, v in field_counts.items()}

    # Overall readiness score (weighted)
    weights = {'has_sku': 20, 'has_name': 20, 'has_price': 20,
               'has_image': 15, 'has_category': 10, 'has_description': 5,
               'has_brand': 5, 'has_stock': 5}
    score = sum(
        (field_counts[k] / total) * w for k, w in weights.items()
    )
    score = round(score, 1)

    return Response({
        'summary': {
            'total_products': total,
            'products_with_issues': len(issues_list),
            'clean_products': total - len(issues_list),
            'duplicate_skus': len(duplicates),
            'price_format_issues': price_format_issues,
            'zero_price_count': zero_price_count,
            'sale_exceeds_count': sale_exceeds_count,
            'readiness_score': score,
        },
        'field_coverage': coverage,
        'issues': issues_list[:50],        # first 50 products with issues
        'duplicate_sku_list': list(duplicates.items())[:20],
        'parsed_samples': parsed_samples,  # first 5 products preview
    })

# ─────────────────────────────────────────────
# QA — Fix Suggestions (field-level drill down)
# ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def qa_fix_suggestions(request):
    from django.db.models import Q
    retailer_filter = request.GET.get('retailer', None)
    qs = Product.objects.filter(is_active=True).select_related('retailer', 'category')
    if retailer_filter:
        qs = qs.filter(retailer__name=retailer_filter)
    total = qs.count()
    if total == 0:
        return Response({'error': 'No products found'}, status=400)

    suggestions = []

    no_brand = qs.filter(Q(brand='') | Q(brand__isnull=True))
    if no_brand.exists():
        sample_retailer = no_brand.first().retailer.name if no_brand.first().retailer else ''
        suggestions.append({
            'field': 'brand', 'icon': '🏷️', 'severity': 'medium',
            'affected': no_brand.count(), 'pct': round(no_brand.count() / total * 100, 1),
            'title': 'Missing Brand',
            'suggestion': f'Consider using the retailer name as brand (e.g. "{sample_retailer}"), or extract brand from the first word of product name.',
            'samples': list(no_brand.values('sku', 'name', 'retailer__name', 'source_url')[:5]),
        })

    no_image = qs.filter(Q(image_url='') | Q(image_url__isnull=True))
    if no_image.exists():
        suggestions.append({
            'field': 'image_url', 'icon': '🖼️', 'severity': 'high',
            'affected': no_image.count(), 'pct': round(no_image.count() / total * 100, 1),
            'title': 'Missing Image URL',
            'suggestion': 'Check if the XML feed uses a different tag: <ImageURL>, <Image>, <PrimaryImageURL>, or inside <Color><ImageURL>.',
            'samples': list(no_image.values('sku', 'name', 'retailer__name', 'source_url')[:5]),
        })

    no_desc = qs.filter(Q(description='') | Q(description__isnull=True))
    if no_desc.exists():
        suggestions.append({
            'field': 'description', 'icon': '📝', 'severity': 'medium',
            'affected': no_desc.count(), 'pct': round(no_desc.count() / total * 100, 1),
            'title': 'Missing Description',
            'suggestion': 'Products with "Not Available" description are saved as empty. Consider using product name + category as a fallback description.',
            'samples': list(no_desc.values('sku', 'name', 'retailer__name', 'source_url')[:5]),
        })

    zero_price = qs.filter(price__lte=0)
    if zero_price.exists():
        suggestions.append({
            'field': 'price', 'icon': '💰', 'severity': 'critical',
            'affected': zero_price.count(), 'pct': round(zero_price.count() / total * 100, 1),
            'title': 'Zero or Missing Price',
            'suggestion': "Price is ₹0 — variant's <Price> tag was likely empty. Check if price is inside <Variant> or at product level as a range like '₹999 - ₹1999'.",
            'samples': list(zero_price.values('sku', 'name', 'retailer__name', 'price', 'source_url')[:5]),
        })

    no_cat = qs.filter(category__isnull=True)
    if no_cat.exists():
        suggestions.append({
            'field': 'category', 'icon': '🗂️', 'severity': 'high',
            'affected': no_cat.count(), 'pct': round(no_cat.count() / total * 100, 1),
            'title': 'No Category Assigned',
            'suggestion': 'Products have no <Category><Part> elements in the feed. They are saved as Uncategorized. Ask the retailer to include category hierarchy.',
            'samples': list(no_cat.values('sku', 'name', 'retailer__name', 'source_url')[:5]),
        })

    no_colors = qs.filter(Q(colors='') | Q(colors__isnull=True))
    if no_colors.exists():
        suggestions.append({
            'field': 'colors', 'icon': '🎨', 'severity': 'low',
            'affected': no_colors.count(), 'pct': round(no_colors.count() / total * 100, 1),
            'title': 'Missing Colors',
            'suggestion': 'Color name not found in <Color><n> or <Color><Name> tags. Check if feed uses <ColorName> or <Colour>.',
            'samples': list(no_colors.values('sku', 'name', 'retailer__name', 'source_url')[:5]),
        })

    no_sizes = qs.filter(Q(sizes='') | Q(sizes__isnull=True))
    if no_sizes.exists():
        suggestions.append({
            'field': 'sizes', 'icon': '📐', 'severity': 'low',
            'affected': no_sizes.count(), 'pct': round(no_sizes.count() / total * 100, 1),
            'title': 'Missing Sizes',
            'suggestion': 'No <Size> tags found at product level. Sizes might be inside <Variant><Size> instead.',
            'samples': list(no_sizes.values('sku', 'name', 'retailer__name', 'source_url')[:5]),
        })

    bad_sale = qs.filter(sale_price__isnull=False, sale_price__gte=models.F('price'))
    if bad_sale.exists():
        suggestions.append({
            'field': 'sale_price', 'icon': '🔄', 'severity': 'critical',
            'affected': bad_sale.count(), 'pct': round(bad_sale.count() / total * 100, 1),
            'title': 'Sale Price >= Regular Price',
            'suggestion': 'Sale price should always be lower than regular price. This is a data entry error in the retailer system — flag it to the retailer.',
            'samples': list(bad_sale.values('sku', 'name', 'retailer__name', 'price', 'sale_price', 'source_url')[:5]),
        })

    suggestions.sort(key=lambda x: {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}[x['severity']])
    return Response({'total_products': total, 'suggestions': suggestions, 'total_suggestions': len(suggestions)})


# ─────────────────────────────────────────────
# QA — Advanced Validation Rules
# ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def qa_advanced_rules(request):
    from django.db.models import Q, Avg, Count as Cnt
    retailer_filter = request.GET.get('retailer', None)
    qs = Product.objects.filter(is_active=True).select_related('retailer', 'category')
    if retailer_filter:
        qs = qs.filter(retailer__name=retailer_filter)
    total = qs.count()
    if total == 0:
        return Response({'error': 'No products found'}, status=400)

    results = {}

    # 1. Duplicate Product Names
    dup_names = (
        qs.values('name', 'retailer__name')
        .annotate(cnt=Cnt('id'))
        .filter(cnt__gt=1)
        .order_by('-cnt')[:20]
    )
    results['duplicate_names'] = {
        'count': dup_names.count(),
        'items': list(dup_names),
        'description': 'Products with identical names within the same retailer — possible duplicates or variants that should be merged.',
    }

    # 2. Price Outliers per Category
    outliers = []
    categories_with_products = (
        qs.exclude(category=None)
        .values('category__name', 'category__id')
        .annotate(cnt=Cnt('id'), avg=Avg('price'))
        .filter(cnt__gte=5)
        .order_by('-cnt')[:20]
    )
    for cat in categories_with_products:
        cat_qs = qs.filter(category__id=cat['category__id'])
        avg = float(cat['avg'] or 0)
        if avg == 0:
            continue
        high_outliers = cat_qs.filter(price__gt=avg * 3).count()
        low_outliers  = cat_qs.filter(price__gt=0, price__lt=avg * 0.1).count()
        if high_outliers > 0 or low_outliers > 0:
            outliers.append({
                'category': cat['category__name'],
                'avg_price': round(avg, 2),
                'total_products': cat['cnt'],
                'high_outliers': high_outliers,
                'low_outliers': low_outliers,
                'high_threshold': round(avg * 3, 2),
                'low_threshold': round(avg * 0.1, 2),
                'samples_high': list(cat_qs.filter(price__gt=avg * 3).values('sku', 'name', 'price', 'retailer__name')[:3]),
                'samples_low':  list(cat_qs.filter(price__gt=0, price__lt=avg * 0.1).values('sku', 'name', 'price', 'retailer__name')[:3]),
            })
    results['price_outliers'] = {
        'count': len(outliers),
        'items': outliers[:15],
        'description': 'Products priced 3x above or below their category average — likely data entry errors.',
    }

    # 3. SKU Format Validation
    all_skus = list(qs.values_list('sku', 'name', 'retailer__name')[:5000])
    short_skus   = [{'sku': s, 'name': n, 'retailer': r} for s, n, r in all_skus if len(s) < 4]
    long_skus    = [{'sku': s, 'name': n, 'retailer': r} for s, n, r in all_skus if len(s) > 30]
    special_char = [{'sku': s, 'name': n, 'retailer': r} for s, n, r in all_skus if re.search(r'[^a-zA-Z0-9\-_]', s)]
    results['sku_validation'] = {
        'total_checked': len(all_skus),
        'short_skus':    {'count': len(short_skus),   'description': 'SKUs shorter than 4 characters',        'samples': short_skus[:10]},
        'long_skus':     {'count': len(long_skus),    'description': 'SKUs longer than 30 characters',        'samples': long_skus[:10]},
        'special_chars': {'count': len(special_char), 'description': 'SKUs with special characters',          'samples': special_char[:10]},
    }

    # 4. Image URL Reachability (sample 20)
    import urllib.request as urlreq
    image_sample = list(qs.exclude(Q(image_url='') | Q(image_url__isnull=True)).values_list('sku', 'name', 'image_url', 'retailer__name')[:20])
    reachable = []
    unreachable = []
    for sku, name, url, r_name in image_sample:
        try:
            req = urlreq.Request(url, method='HEAD', headers={'User-Agent': 'Mozilla/5.0'})
            with urlreq.urlopen(req, timeout=3) as resp:
                if resp.status < 400:
                    reachable.append({'sku': sku, 'name': name[:40], 'url': url, 'status': resp.status})
                else:
                    unreachable.append({'sku': sku, 'name': name[:40], 'url': url, 'status': resp.status})
        except Exception as e:
            unreachable.append({'sku': sku, 'name': name[:40], 'url': url, 'error': str(e)[:80]})

    total_with_image = qs.exclude(Q(image_url='') | Q(image_url__isnull=True)).count()
    results['image_reachability'] = {
        'sampled': len(image_sample),
        'total_with_image': total_with_image,
        'reachable': len(reachable),
        'unreachable': len(unreachable),
        'reachable_pct': round(len(reachable) / len(image_sample) * 100, 1) if image_sample else 0,
        'note': f'Sampled 20 of {total_with_image} products with images (HEAD request, 3s timeout)',
        'unreachable_samples': unreachable[:10],
    }

    return Response({'total_products': total, 'rules': results})


# ─────────────────────────────────────────────
# QA — Retailer Comparison Table
# ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def qa_retailer_comparison(request):
    from django.db.models import Q, Avg
    rows = []
    for retailer in Retailer.objects.filter(is_active=True).order_by('name'):
        qs = Product.objects.filter(is_active=True, retailer=retailer)
        total = qs.count()
        if total == 0:
            continue
        def pct(n): return round(n / total * 100, 1)
        has_image    = qs.exclude(Q(image_url='') | Q(image_url__isnull=True)).count()
        has_brand    = qs.exclude(Q(brand='') | Q(brand__isnull=True)).count()
        has_desc     = qs.exclude(Q(description='') | Q(description__isnull=True)).count()
        has_category = qs.filter(category__isnull=False).count()
        has_colors   = qs.exclude(Q(colors='') | Q(colors__isnull=True)).count()
        has_sizes    = qs.exclude(Q(sizes='') | Q(sizes__isnull=True)).count()
        has_sale     = qs.filter(sale_price__isnull=False).count()
        in_stock     = qs.filter(stock=1).count()
        zero_price   = qs.filter(price__lte=0).count()
        avg_price    = qs.aggregate(a=Avg('price'))['a'] or 0
        score = round((pct(has_image) + pct(has_brand) + pct(has_desc) +
                       pct(has_category) + pct(in_stock) + (100 - pct(zero_price))) / 6, 1)
        rows.append({
            'retailer': retailer.name, 'total': total, 'score': score,
            'avg_price': round(float(avg_price), 2),
            'fields': {
                'image':       {'count': has_image,    'pct': pct(has_image)},
                'brand':       {'count': has_brand,    'pct': pct(has_brand)},
                'description': {'count': has_desc,     'pct': pct(has_desc)},
                'category':    {'count': has_category, 'pct': pct(has_category)},
                'colors':      {'count': has_colors,   'pct': pct(has_colors)},
                'sizes':       {'count': has_sizes,    'pct': pct(has_sizes)},
                'sale_price':  {'count': has_sale,     'pct': pct(has_sale)},
                'in_stock':    {'count': in_stock,     'pct': pct(in_stock)},
                'zero_price':  {'count': zero_price,   'pct': pct(zero_price)},
            },
        })
    rows.sort(key=lambda x: x['score'], reverse=True)
    return Response({'retailers': rows, 'fields': ['image','brand','description','category','colors','sizes','sale_price','in_stock','zero_price']})


# ─────────────────────────────────────────────
# QA — Auto-flag issues after upload
# ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def qa_upload_flags(request):
    from django.db.models import Q
    logs = UploadLog.objects.order_by('-created_at')[:20]
    flagged = []
    for log in logs:
        qs = Product.objects.filter(is_active=True, retailer__name=log.retailer_name)
        total = qs.count()
        flags = []
        if total > 0:
            zero_p = qs.filter(price__lte=0).count()
            no_img = qs.filter(Q(image_url='') | Q(image_url__isnull=True)).count()
            no_cat = qs.filter(category__isnull=True).count()
            bad_sp = qs.filter(sale_price__isnull=False, sale_price__gte=models.F('price')).count()
            no_br  = qs.filter(Q(brand='') | Q(brand__isnull=True)).count()
            if zero_p > 0: flags.append({'type': 'critical', 'msg': f'{zero_p} products with ₹0 price'})
            if bad_sp > 0: flags.append({'type': 'critical', 'msg': f'{bad_sp} products: sale price >= regular price'})
            if no_img > 0: flags.append({'type': 'high',     'msg': f'{no_img} products missing image URL'})
            if no_cat > 0: flags.append({'type': 'high',     'msg': f'{no_cat} products with no category'})
            if no_br  > 0: flags.append({'type': 'medium',   'msg': f'{no_br} products missing brand'})
        flagged.append({
            'log_id': log.id, 'retailer': log.retailer_name,
            'uploaded_at': str(log.created_at)[:19],
            'loaded': log.loaded, 'flags': flags, 'flag_count': len(flags),
            'has_critical': any(f['type'] == 'critical' for f in flags),
        })
    return Response({'upload_flags': flagged})


# ─────────────────────────────────────────────
# Price History — snapshot helper
# ─────────────────────────────────────────────

def snapshot_price_change(product_obj, new_data, was_created):
    """
    Called after every update_or_create in refresh_feed.
    Compares old price vs new price and writes a PriceHistory row if anything changed.
    """
    from decimal import Decimal

    new_price      = Decimal(str(new_data['price']))
    new_sale       = Decimal(str(new_data['sale_price'])) if new_data['sale_price'] else None
    old_price      = product_obj.price
    old_sale       = product_obj.sale_price
    currency       = new_data.get('currency', '₹')
    sku            = new_data['sku']
    product_name   = new_data['name']
    retailer_name  = product_obj.retailer.name if product_obj.retailer else ''
    source_url     = new_data.get('source_url', '')

    entries = []

    if was_created:
        entries.append(PriceHistory(
            sku=sku, product_name=product_name, retailer_name=retailer_name,
            source_url=source_url, old_price=None, new_price=new_price,
            old_sale_price=None, new_sale_price=new_sale,
            change_type='new_product', change_pct=None, currency=currency,
        ))
    else:
        # Regular price changed
        if old_price is not None and new_price != old_price:
            pct = round((float(new_price) - float(old_price)) / float(old_price) * 100, 2) if old_price else None
            entries.append(PriceHistory(
                sku=sku, product_name=product_name, retailer_name=retailer_name,
                source_url=source_url, old_price=old_price, new_price=new_price,
                old_sale_price=old_sale, new_sale_price=new_sale,
                change_type='price_up' if new_price > old_price else 'price_down',
                change_pct=pct, currency=currency,
            ))

        # Sale price changes (independent of regular price)
        if old_sale is None and new_sale is not None:
            entries.append(PriceHistory(
                sku=sku, product_name=product_name, retailer_name=retailer_name,
                source_url=source_url, old_price=old_price, new_price=new_price,
                old_sale_price=None, new_sale_price=new_sale,
                change_type='sale_added', change_pct=None, currency=currency,
            ))
        elif old_sale is not None and new_sale is None:
            entries.append(PriceHistory(
                sku=sku, product_name=product_name, retailer_name=retailer_name,
                source_url=source_url, old_price=old_price, new_price=new_price,
                old_sale_price=old_sale, new_sale_price=None,
                change_type='sale_removed', change_pct=None, currency=currency,
            ))
        elif old_sale is not None and new_sale is not None and old_sale != new_sale:
            entries.append(PriceHistory(
                sku=sku, product_name=product_name, retailer_name=retailer_name,
                source_url=source_url, old_price=old_price, new_price=new_price,
                old_sale_price=old_sale, new_sale_price=new_sale,
                change_type='sale_changed', change_pct=None, currency=currency,
            ))

    if entries:
        PriceHistory.objects.bulk_create(entries)


# ─────────────────────────────────────────────
# QA — Price Change History
# ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def qa_price_changes(request):
    from django.db.models import Q

    retailer_filter = request.GET.get('retailer', None)
    change_type     = request.GET.get('change_type', None)   # price_up, price_down, sale_added...
    days            = int(request.GET.get('days', 30))
    page            = int(request.GET.get('page', 1))
    page_size       = 50

    from django.utils import timezone
    import datetime
    since = timezone.now() - datetime.timedelta(days=days)

    qs = PriceHistory.objects.filter(detected_at__gte=since)

    if retailer_filter:
        qs = qs.filter(retailer_name=retailer_filter)
    if change_type:
        qs = qs.filter(change_type=change_type)

    # Summary counts
    total       = qs.count()
    price_ups   = qs.filter(change_type='price_up').count()
    price_downs = qs.filter(change_type='price_down').count()
    sale_added  = qs.filter(change_type='sale_added').count()
    sale_removed= qs.filter(change_type='sale_removed').count()
    new_products= qs.filter(change_type='new_product').count()

    # Biggest price jumps
    biggest_increases = list(
        qs.filter(change_type='price_up')
        .order_by('-change_pct')[:5]
        .values('sku', 'product_name', 'retailer_name', 'old_price', 'new_price', 'change_pct', 'currency', 'source_url', 'detected_at')
    )
    biggest_decreases = list(
        qs.filter(change_type='price_down')
        .order_by('change_pct')[:5]
        .values('sku', 'product_name', 'retailer_name', 'old_price', 'new_price', 'change_pct', 'currency', 'source_url', 'detected_at')
    )

    # Paginated full list
    offset = (page - 1) * page_size
    records = list(
        qs.order_by('-detected_at')[offset:offset + page_size]
        .values('sku', 'product_name', 'retailer_name', 'old_price', 'new_price',
                'old_sale_price', 'new_sale_price', 'change_type', 'change_pct',
                'currency', 'source_url', 'detected_at')
    )

    # Convert decimals and datetimes to strings for JSON
    def fmt(r):
        r['old_price']      = str(r['old_price']) if r['old_price'] is not None else None
        r['new_price']      = str(r['new_price'])
        r['old_sale_price'] = str(r['old_sale_price']) if r['old_sale_price'] is not None else None
        r['new_sale_price'] = str(r['new_sale_price']) if r['new_sale_price'] is not None else None
        r['change_pct']     = float(r['change_pct']) if r['change_pct'] is not None else None
        r['detected_at']    = str(r['detected_at'])[:19]
        return r

    return Response({
        'summary': {
            'total': total, 'price_ups': price_ups, 'price_downs': price_downs,
            'sale_added': sale_added, 'sale_removed': sale_removed, 'new_products': new_products,
        },
        'biggest_increases': [fmt(r) for r in biggest_increases],
        'biggest_decreases': [fmt(r) for r in biggest_decreases],
        'records': [fmt(r) for r in records],
        'total_records': total,
        'page': page,
        'page_size': page_size,
        'total_pages': max(1, (total + page_size - 1) // page_size),
    })


# ─────────────────────────────────────────────
# Scrape Health Dashboard
# ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def scrape_health(request):
    from django.db.models import Avg, Count, Min, Max, Q
    from django.utils import timezone
    import datetime

    days     = int(request.GET.get('days', 30))
    since    = timezone.now() - datetime.timedelta(days=days)

    # All upload logs in window
    logs_qs  = UploadLog.objects.filter(created_at__gte=since).order_by('retailer_name', 'created_at')

    # Group logs by retailer
    from collections import defaultdict
    retailer_logs = defaultdict(list)
    for log in logs_qs.values(
        'id', 'retailer_name', 'loaded', 'skipped', 'total_found',
        'status', 'error_message', 'created_at', 'duration_seconds'
    ):
        retailer_logs[log['retailer_name']].append(log)

    retailers_health = []
    all_alerts       = []

    for retailer_name, logs in retailer_logs.items():
        total_runs    = len(logs)
        failed_runs   = sum(1 for l in logs if l['status'] == 'failed')
        success_runs  = total_runs - failed_runs
        success_rate  = round(success_runs / total_runs * 100, 1) if total_runs else 0

        # Duration stats (only runs that have it)
        durations = [l['duration_seconds'] for l in logs if l['duration_seconds'] is not None]
        avg_dur   = round(sum(durations) / len(durations), 1) if durations else None
        max_dur   = round(max(durations), 1) if durations else None

        # Last run info
        last_log  = logs[-1]
        last_run  = str(last_log['created_at'])[:19]

        # Product count trend — last 10 runs
        trend     = [{'date': str(l['created_at'])[:10], 'loaded': l['loaded'], 'total_found': l['total_found']} for l in logs[-10:]]

        # Data quality score per run
        # Quality = loaded / total_found % (how much we successfully parsed)
        # NOTE: skipped includes BOTH duplicate SKUs (normal) and parse errors.
        # We show the score for info only — not used for alerting.
        quality_scores = []
        for l in logs[-10:]:
            if l['total_found'] and l['total_found'] > 0:
                score = round(l['loaded'] / l['total_found'] * 100, 1)
            else:
                score = 0
            quality_scores.append({
                'date':        str(l['created_at'])[:10],
                'score':       score,
                'loaded':      l['loaded'],
                'skipped':     l['skipped'],
                'total_found': l['total_found'],
                'status':      l['status'],
            })

        # ── Auto-alerts ────────────────────────────────
        alerts = []

        # 1. total_found dropped 50%+ vs previous run
        # Use total_found (products in the XML feed), NOT loaded (which drops
        # naturally when products already exist in DB from previous runs).
        found_counts = [l['total_found'] for l in logs if l['status'] != 'failed' and l['total_found']]
        if len(found_counts) >= 2:
            prev   = found_counts[-2]
            latest = found_counts[-1]
            if prev > 0:
                drop_pct = (prev - latest) / prev * 100
                if drop_pct >= 50:
                    alerts.append({
                        'type':    'critical',
                        'icon':    '📉',
                        'message': f'Feed size dropped {round(drop_pct)}% ({prev} → {latest} products in XML)',
                        'detail':  'Possible scraper block, site structure change, or feed truncated',
                    })

        # 2. 3 consecutive failures
        last_3 = [l['status'] for l in logs[-3:]]
        if len(last_3) == 3 and all(s == 'failed' for s in last_3):
            alerts.append({
                'type':    'critical',
                'icon':    '❌',
                'message': '3 consecutive scrape failures',
                'detail':  last_log.get('error_message', '') or 'Check feed URL or site availability',
            })

        # 3. Zero products found in feed (not zero loaded — that's normal for refresh)
        if last_log['status'] != 'failed' and last_log['total_found'] == 0:
            alerts.append({
                'type':    'high',
                'icon':    '⚠️',
                'message': 'Last scrape found 0 products in feed',
                'detail':  'Feed may be empty or XML structure changed',
            })

        # Overall health status
        if any(a['type'] == 'critical' for a in alerts):
            health = 'critical'
        elif any(a['type'] == 'high' for a in alerts):
            health = 'warning'
        elif alerts:
            health = 'watch'
        else:
            health = 'healthy'

        for a in alerts:
            all_alerts.append({ **a, 'retailer': retailer_name, 'detected_at': last_run })

        retailers_health.append({
            'retailer':       retailer_name,
            'health':         health,
            'total_runs':     total_runs,
            'success_rate':   success_rate,
            'failed_runs':    failed_runs,
            'last_run':       last_run,
            'last_status':    last_log['status'],
            'last_loaded':    last_log['loaded'],
            'last_skipped':   last_log['skipped'],
            'last_total':     last_log['total_found'],
            'avg_duration':   avg_dur,
            'max_duration':   max_dur,
            'trend':          trend,
            'quality_scores': quality_scores,
            'alert_count':    len(alerts),
            'alerts':         alerts,
        })

    # Sort: critical first, then warning, then healthy
    order = {'critical': 0, 'warning': 1, 'watch': 2, 'healthy': 3}
    retailers_health.sort(key=lambda x: order.get(x['health'], 9))

    # Sort alerts: critical first
    all_alerts.sort(key=lambda x: {'critical': 0, 'high': 1, 'medium': 2}.get(x['type'], 3))

    return Response({
        'retailers':     retailers_health,
        'all_alerts':    all_alerts,
        'total_alerts':  len(all_alerts),
        'critical_count': sum(1 for a in all_alerts if a['type'] == 'critical'),
        'days':          days,
    })