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
    Build category tree from parts list using in-memory cache.
    Cache key: "Women||Western Wear||Dresses" → Category object
    Avoids hitting DB for the same category on every product.
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
    for level in range(len(parts)):
        key = '||'.join(parts[:level + 1])
        if key not in cache:
            slug = re.sub(r'[^a-z0-9]+', '-', parts[level].lower()).strip('-')
            category, _ = Category.objects.get_or_create(
                name=parts[level],
                parent=parent,
                defaults={'slug': slug, 'level': level}
            )
            cache[key] = category
        else:
            category = cache[key]
        parent = category

    return category


def get_ancestors_from_cache(cat, ancestor_cache):
    """
    Walk up parent chain and return cat + all ancestors.
    Uses ancestor_cache { cat.id: [list] } to avoid re-walking same path.
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
    primary_parts = max(all_category_parts, key=len) if all_category_parts else []

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
# Upload XML  (OPTIMIZED — bulk insert + cache)
# ─────────────────────────────────────────────

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

    # ── STEP 1: Fetch all existing SKUs in ONE query ─────────────
    existing_skus = set(
        Product.objects.filter(retailer=retailer_obj).values_list('sku', flat=True)
    )

    # ── STEP 2: Parse + build categories with in-memory cache ────
    category_cache = {}   # "Women||Western Wear" → Category obj
    ancestor_cache = {}   # category.id → [cat, parent, grandparent, ...]

    to_create = []        # Product objects for bulk_create
    m2m_data = []         # [(sku, [cat_ids]), ...]
    skipped = 0
    errors = []
    total_found = len(products)

    for product in products:
        try:
            data = parse_product(product, retailer_obj)
            if not data:
                skipped += 1
                continue

            if data['sku'] in existing_skus:
                skipped += 1
                continue

            # Primary category for display on product cards
            primary_category = get_or_create_category_tree(
                data['primary_category_parts'], category_cache
            )

            # All categories + ancestors for M2M accurate counting
            all_cat_ids = set()
            for parts in data['all_category_parts']:
                leaf = get_or_create_category_tree(parts, category_cache)
                if leaf:
                    for anc in get_ancestors_from_cache(leaf, ancestor_cache):
                        all_cat_ids.add(anc.id)

            to_create.append(Product(
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
                is_active=True,
            ))
            m2m_data.append((data['sku'], list(all_cat_ids)))
            existing_skus.add(data['sku'])  # prevent in-file duplicates

        except Exception as e:
            errors.append(str(e))
            skipped += 1

    # ── STEP 3: Bulk create products in batches of 500 ───────────
    BATCH = 500
    for i in range(0, len(to_create), BATCH):
        Product.objects.bulk_create(to_create[i:i + BATCH], ignore_conflicts=True)

    loaded = len(to_create)

    # ── STEP 4: Bulk insert M2M rows ─────────────────────────────
    skus_in_batch = [p.sku for p in to_create]
    sku_to_id = dict(
        Product.objects.filter(sku__in=skus_in_batch).values_list('sku', 'id')
    )

    Through = Product.categories.through
    m2m_rows = []
    seen_pairs = set()
    for sku, cat_ids in m2m_data:
        product_id = sku_to_id.get(sku)
        if not product_id:
            continue
        for cat_id in cat_ids:
            pair = (product_id, cat_id)
            if pair not in seen_pairs:
                m2m_rows.append(Through(product_id=product_id, category_id=cat_id))
                seen_pairs.add(pair)

    for i in range(0, len(m2m_rows), BATCH):
        Through.objects.bulk_create(m2m_rows[i:i + BATCH], ignore_conflicts=True)

    # ── STEP 5: Save upload log ───────────────────────────────────
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
# Refresh Feed  (OPTIMIZED — bulk update + cache)
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

    # Fetch existing SKU → ID map for this retailer
    existing_sku_map = dict(
        Product.objects.filter(retailer=retailer).values_list('sku', 'id')
    )

    category_cache = {}
    ancestor_cache = {}

    to_create = []
    to_update = []
    m2m_data = []
    skipped = 0
    errors = []
    total_found = len(products)

    for product in products:
        try:
            data = parse_product(product, retailer)
            if not data:
                skipped += 1
                continue

            primary_category = get_or_create_category_tree(
                data['primary_category_parts'], category_cache
            )

            all_cat_ids = set()
            for parts in data['all_category_parts']:
                leaf = get_or_create_category_tree(parts, category_cache)
                if leaf:
                    for anc in get_ancestors_from_cache(leaf, ancestor_cache):
                        all_cat_ids.add(anc.id)

            product_fields = dict(
                name=data['name'],
                description=data['description'],
                category=primary_category,
                retailer=retailer,
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
                is_active=True,
            )

            if data['sku'] in existing_sku_map:
                p = Product(id=existing_sku_map[data['sku']], sku=data['sku'], **product_fields)
                to_update.append(p)
            else:
                to_create.append(Product(sku=data['sku'], **product_fields))

            m2m_data.append((data['sku'], list(all_cat_ids)))

        except Exception as e:
            errors.append(str(e))
            skipped += 1

    BATCH = 500

    # Bulk create new
    for i in range(0, len(to_create), BATCH):
        Product.objects.bulk_create(to_create[i:i + BATCH], ignore_conflicts=True)

    # Bulk update existing
    update_fields = [
        'name', 'description', 'category', 'brand', 'price', 'sale_price',
        'currency', 'stock', 'source_url', 'image_url',
        'additional_images', 'colors', 'sizes', 'is_active'
    ]
    for i in range(0, len(to_update), BATCH):
        Product.objects.bulk_update(to_update[i:i + BATCH], update_fields)

    loaded = len(to_create) + len(to_update)

    # Refresh M2M
    all_skus = [p.sku for p in to_create] + [p.sku for p in to_update]
    sku_to_id = dict(Product.objects.filter(sku__in=all_skus).values_list('sku', 'id'))

    Through = Product.categories.through

    # Clear old M2M for updated products before re-inserting
    updated_ids = [existing_sku_map[p.sku] for p in to_update if p.sku in existing_sku_map]
    if updated_ids:
        Through.objects.filter(product_id__in=updated_ids).delete()

    m2m_rows = []
    seen_pairs = set()
    for sku, cat_ids in m2m_data:
        product_id = sku_to_id.get(sku)
        if not product_id:
            continue
        for cat_id in cat_ids:
            pair = (product_id, cat_id)
            if pair not in seen_pairs:
                m2m_rows.append(Through(product_id=product_id, category_id=cat_id))
                seen_pairs.add(pair)

    for i in range(0, len(m2m_rows), BATCH):
        Through.objects.bulk_create(m2m_rows[i:i + BATCH], ignore_conflicts=True)

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