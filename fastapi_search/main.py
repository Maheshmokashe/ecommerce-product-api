from fastapi import FastAPI, Query
import pymysql
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Product Search Service")

def get_connection():
    return pymysql.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        cursorclass=pymysql.cursors.DictCursor
    )

@app.get("/")
def root():
    return {"message": "Product Search Service is running"}

@app.get("/search")
def search_products(
    q: str = Query(..., description="Search keyword"),
    min_price: float = Query(None, description="Minimum price"),
    max_price: float = Query(None, description="Maximum price"),
    limit: int = Query(20, le=100)
):
    conn = get_connection()
    filters = ["p.is_active = 1", "(p.name LIKE %s OR p.sku LIKE %s)"]
    params = [f"%{q}%", f"%{q}%"]

    if min_price is not None:
        filters.append("p.price >= %s")
        params.append(min_price)
    if max_price is not None:
        filters.append("p.price <= %s")
        params.append(max_price)

    where = " AND ".join(filters)
    sql = f"""
        SELECT p.id, p.sku, p.name, p.price, p.stock, c.name AS category
        FROM products_product p
        LEFT JOIN products_category c ON p.category_id = c.id
        WHERE {where}
        ORDER BY p.price ASC
        LIMIT %s
    """
    params.append(limit)

    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        results = cursor.fetchall()
    conn.close()

    return {"query": q, "count": len(results), "results": results}