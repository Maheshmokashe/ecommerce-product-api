from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import mysql.connector
from typing import Optional

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="root",
        database="ecommerce_db"
    )

@app.get("/search")
def search_products(
    q: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    retailer: Optional[str] = None,
    brand: Optional[str] = None,
    color: Optional[str] = None,
    size: Optional[str] = None,
    in_stock: Optional[bool] = None,
    limit: int = 500
):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    conditions = ["p.is_active = 1"]
    params = []

    if q:
        conditions.append("(p.name LIKE %s OR p.sku LIKE %s OR p.description LIKE %s OR p.brand LIKE %s)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]

    if min_price is not None:
        conditions.append("p.price >= %s")
        params.append(min_price)

    if max_price is not None:
        conditions.append("p.price <= %s")
        params.append(max_price)

    if retailer:
        conditions.append("r.name = %s")
        params.append(retailer)

    if brand:
        conditions.append("p.brand = %s")
        params.append(brand)

    if color:
        conditions.append("p.colors LIKE %s")
        params.append(f"%{color}%")

    if size:
        conditions.append("p.sizes LIKE %s")
        params.append(f"%{size}%")

    if in_stock is not None:
        conditions.append("p.stock = %s")
        params.append(1 if in_stock else 0)

    where = " AND ".join(conditions)
    limit = min(limit, 500)

    query = f"""
        SELECT p.id, p.sku, p.name, p.price, p.sale_price, p.currency,
               p.stock, p.source_url, p.image_url, p.brand,
               p.colors, p.sizes, p.additional_images,
               c.name as category, r.name as retailer
        FROM products_product p
        LEFT JOIN products_category c ON p.category_id = c.id
        LEFT JOIN products_retailer r ON p.retailer_id = r.id
        WHERE {where}
        ORDER BY p.created_at DESC
        LIMIT %s
    """
    params.append(limit)
    cursor.execute(query, params)
    results = cursor.fetchall()

    for r in results:
        r['price'] = float(r['price']) if r['price'] else 0
        r['sale_price'] = float(r['sale_price']) if r['sale_price'] else None

    cursor.close()
    conn.close()
    return {"results": results, "count": len(results)}


@app.get("/filters")
def get_filters():
    """Return all available filter options"""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT DISTINCT name FROM products_retailer WHERE is_active=1 ORDER BY name")
    retailers = [r['name'] for r in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT brand FROM products_product WHERE brand != '' AND is_active=1 ORDER BY brand")
    brands = [r['brand'] for r in cursor.fetchall()]

    cursor.execute("SELECT DISTINCT colors FROM products_product WHERE colors != '' AND is_active=1")
    color_rows = cursor.fetchall()
    colors = sorted(set(
        c.strip() for row in color_rows
        for c in row['colors'].split(',') if c.strip()
    ))

    cursor.execute("SELECT DISTINCT sizes FROM products_product WHERE sizes != '' AND is_active=1")
    size_rows = cursor.fetchall()
    sizes = sorted(set(
        s.strip() for row in size_rows
        for s in row['sizes'].split(',') if s.strip()
    ))

    cursor.close()
    conn.close()
    return {
        "retailers": retailers,
        "brands": brands,
        "colors": colors[:50],
        "sizes": sizes[:50]
    }