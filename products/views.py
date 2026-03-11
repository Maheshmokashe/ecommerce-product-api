from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from .models import Product, Category, Retailer, UploadLog
from .serializers import ProductSerializer, CategorySerializer, RetailerSerializer, UploadLogSerializer
from urllib.parse import urlparse
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
    price_str = ''
    sale_price_str = ''
    if variant is not None:
        price_str = variant.findtext('Price') or ''
        sale_price_str = variant.findtext('SalePrice') or variant.findtext('Sale_Price') or ''
    if not price_str:
        price_str = product.findtext('Price') or '0'
    if not sale_price_str:
        sale_price_str = product.findtext('SalePrice') or product.findtext('Sale_Price') or ''

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
        uploaded_by=uploaded_by
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
    from django.utils import timezone

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

            product_obj, _ = Product.objects.update_or_create(
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
        uploaded_by=request.user.username
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