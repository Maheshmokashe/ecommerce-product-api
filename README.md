# 🛒 ECommerce Product API

A production-ready **Django REST API + FastAPI microservice** for ingesting, managing, and searching multi-retailer product catalogs from XML feeds.

---

## 🚀 Live Demo
- **Backend API:** `http://127.0.0.1:8000/api/`
- **FastAPI Search:** `http://127.0.0.1:8001/docs`
- **Frontend:** [ecommerce-frontend](https://github.com/Maheshmokashe/ecommerce-frontend)

---

## 🏗️ Tech Stack

| Layer | Technology |
|---|---|
| REST API | Django 5.x + Django REST Framework |
| Search Microservice | FastAPI + Uvicorn |
| Database | MySQL 8.0 |
| Authentication | JWT (SimpleJWT) |
| XML Parsing | Python xml.etree.ElementTree |
| CORS | django-cors-headers |

---

## ✨ Features

### 🔄 XML Feed Ingestion
- Upload XML product feeds via REST API
- **Auto-detects retailer name, website, currency** from feed
- Supports **20+ currencies** — ₹ IN, £ UK, € DE/ES/NL/FR, ₩ KR, ¥ JP, and more
- Handles **European price formats** (1.299,00 → 1299.00) and **Korean comma-thousands** (12,000 → 12000)
- Extracts: SKU, name, brand, price, sale price, stock, images, colors, sizes
- Strips HTML entities from descriptions (&#243; → ó)
- Deduplicates by SKU on re-upload

### 🗂️ Hierarchical Categories
- Full **parent → child category tree** built from XML feed Parts
- Supports Top → Mid → Sub → Leaf levels
- `unique_together` constraint on name + parent
- Tree statistics with product counts per node and all descendants

### 🔍 FastAPI Search Microservice
- Separate microservice on port 8001
- Full-text search across name, SKU, brand, description
- Filter by: retailer, brand, color, size, min/max price, in-stock
- `/search` endpoint — returns up to 500 results
- `/filters` endpoint — returns available filter options for UI dropdowns

### 📋 Activity Log
- Every XML upload logged to `UploadLog` model
- Tracks: retailer, filename, loaded count, skipped count, total found, status, uploaded_by, timestamp
- Available via `/api/upload-logs/`

### ⏰ Feed Scheduler
- Store XML feed URL per retailer
- One-click refresh endpoint — fetches XML from URL and runs `update_or_create`
- Tracks `last_fetched_at` per retailer
- No duplicates — existing products update, new ones added

### ☑️ Bulk Operations
- Bulk delete products by ID list via `/api/bulk-delete/`

---

## 📁 Project Structure

```
ecommerce_api/
├── config/
│   ├── settings.py
│   └── urls.py
├── products/
│   ├── models.py          # Retailer, Category (hierarchical), Product, UploadLog
│   ├── serializers.py
│   ├── views.py           # All API views + XML parser
│   └── urls.py
├── fastapi_search/
│   └── main.py            # FastAPI search + filters endpoints
├── manage.py
└── requirements.txt
```

---

## 🗄️ Database Models

### Retailer
```python
name, slug, website, feed_url, last_fetched_at, is_active, created_at
```

### Category (Hierarchical)
```python
name, slug, parent (FK self), level (0=Top, 1=Mid, 2=Sub, 3=Leaf)
```

### Product
```python
sku (indexed), name, description, category (FK), retailer (FK CASCADE),
brand, price (indexed), sale_price, currency, stock,
source_url, image_url, additional_images, colors, sizes, is_active
```

### UploadLog
```python
retailer_name, filename, loaded, skipped, total_found, status, error_message, uploaded_by, created_at
```

---

## 🔌 API Endpoints

### Authentication
```
POST   /api/token/              # Get JWT access + refresh tokens
POST   /api/token/refresh/      # Refresh access token
```

### Products
```
GET    /api/products/           # List all products
POST   /api/bulk-delete/        # Bulk delete by ID list
```

### Categories
```
GET    /api/categories/         # Flat list
GET    /api/category-stats/     # Hierarchical tree with product counts
GET    /api/category-stats/?retailer=Westside IN   # Filter by retailer
```

### Retailers
```
GET    /api/retailers/                          # List retailers
DELETE /api/retailers/{id}/                     # Delete retailer + all products (CASCADE)
POST   /api/retailers/{id}/update-feed/         # Set feed URL
POST   /api/retailers/{id}/refresh-feed/        # Fetch XML from URL + update products
```

### XML Upload
```
POST   /api/upload-xml/         # Upload XML file (multipart/form-data)
```

### Activity Log
```
GET    /api/upload-logs/        # All upload history
```

### FastAPI Search (port 8001)
```
GET    /search?q=dress&retailer=Westside IN&min_price=500&max_price=2000
GET    /filters                 # Returns retailers, brands, colors, sizes
```

---

## ⚙️ Setup & Installation

### Prerequisites
- Python 3.11+
- MySQL 8.0
- Node.js (for frontend)

### 1. Clone the repo
```bash
git clone https://github.com/Maheshmokashe/ecommerce-product-api.git
cd ecommerce-product-api
```

### 2. Create virtual environment
```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Mac/Linux
```

### 3. Install dependencies
```bash
pip install django djangorestframework djangorestframework-simplejwt django-cors-headers mysqlclient fastapi uvicorn mysql-connector-python
```

### 4. Configure MySQL
Create database:
```sql
CREATE DATABASE ecommerce_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Update `config/settings.py`:
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'ecommerce_db',
        'USER': 'your_user',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '3306',
    }
}
```

### 5. Run migrations
```bash
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

### 6. Start servers

**Terminal 1 — Django:**
```bash
python manage.py runserver
```

**Terminal 2 — FastAPI:**
```bash
uvicorn fastapi_search.main:app --reload --port 8001
```

---

## 📊 Data Stats
- **real products** across multiple retailers
- **categories** in hierarchical tree
- Supports **unlimited retailers** — just upload a new XML feed
- Multi-currency support: ₹, £, €, ₩, $, and 20+ more

---

## 👨‍💻 Author
**Mahesh Mokashe**
- GitHub: [@Maheshmokashe](https://github.com/Maheshmokashe)
- Experience: 3.7 years at KrawlNet Technologies
