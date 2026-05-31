import io
import os
import tempfile
import pytest
from pathlib import Path

from inventory.db import get_connection, init_db
from inventory.models import Product, OrderType, Supplier
from inventory import service
from inventory.web import create_app


# ── shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def client():
    fd, path = tempfile.mkstemp(suffix=".db")
    app = create_app(Path(path))
    app.config["TESTING"] = True
    yield app.test_client()
    os.close(fd)
    os.unlink(path)


@pytest.fixture
def seeded_client():
    fd, path = tempfile.mkstemp(suffix=".db")
    db_path = Path(path)
    conn = get_connection(db_path)
    init_db(conn)
    p = service.create_product(conn, Product(None, "WGT-001", "Widget", 9.99, reorder_threshold=5))
    po = service.create_order(conn, p.id, OrderType.PURCHASE, 20)
    service.fulfill_order(conn, po.id)
    conn.close()
    app = create_app(db_path)
    app.config["TESTING"] = True
    yield app.test_client(), p
    os.close(fd)
    os.unlink(path)


def _csv(text):
    return io.StringIO(text)


# ── import_products_csv service tests ────────────────────────────────────────

def test_import_basic(conn):
    csv_data = _csv("sku,name,unit_price\nA-1,Alpha,4.99\nB-2,Beta,9.99\n")
    result = service.import_products_csv(conn, csv_data)
    assert result["created"] == 2
    assert result["skipped"] == 0
    assert result["errors"] == []
    products = service.list_products(conn)
    assert len(products) == 2


def test_import_with_all_optional_columns(conn):
    csv_data = _csv(
        "sku,name,unit_price,reorder_threshold,opening_stock\n"
        "C-1,Gamma,5.00,20,50\n"
    )
    result = service.import_products_csv(conn, csv_data)
    assert result["created"] == 1
    stock = service.get_stock(conn, service.list_products(conn)[0].id)
    assert stock.quantity == 50


def test_import_zero_opening_stock(conn):
    csv_data = _csv("sku,name,unit_price,opening_stock\nD-1,Delta,3.00,0\n")
    service.import_products_csv(conn, csv_data)
    p = service.list_products(conn)[0]
    assert service.get_stock(conn, p.id).quantity == 0


def test_import_skips_duplicate_sku(conn):
    service.create_product(conn, Product(None, "DUP", "Existing", 1.0))
    csv_data = _csv("sku,name,unit_price\nDUP,Duplicate,2.0\n")
    result = service.import_products_csv(conn, csv_data)
    assert result["created"] == 0
    assert result["skipped"] == 1
    assert result["errors"] == []


def test_import_records_error_on_bad_price(conn):
    csv_data = _csv("sku,name,unit_price\nE-1,Epsilon,notanumber\n")
    result = service.import_products_csv(conn, csv_data)
    assert result["created"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"][0]["row"] == 2


def test_import_records_error_on_negative_opening_stock(conn):
    csv_data = _csv("sku,name,unit_price,opening_stock\nF-1,Zeta,1.0,-5\n")
    result = service.import_products_csv(conn, csv_data)
    assert len(result["errors"]) == 1


def test_import_partial_success(conn):
    csv_data = _csv("sku,name,unit_price\nG-1,Good,1.0\n,Bad Row,\nH-1,AlsoGood,2.0\n")
    result = service.import_products_csv(conn, csv_data)
    assert result["created"] == 2
    assert len(result["errors"]) == 1


def test_import_missing_required_column_raises(conn):
    csv_data = _csv("sku,unit_price\nA-1,4.99\n")
    with pytest.raises(service.InventoryError, match="missing required columns"):
        service.import_products_csv(conn, csv_data)


def test_import_empty_file_raises(conn):
    with pytest.raises(service.InventoryError, match="empty"):
        service.import_products_csv(conn, _csv(""))


def test_import_with_supplier(conn):
    s = service.create_supplier(conn, Supplier(None, "Acme"))
    csv_data = _csv(f"sku,name,unit_price,supplier_id\nX-1,Item,5.00,{s.id}\n")
    result = service.import_products_csv(conn, csv_data)
    assert result["created"] == 1
    p = service.list_products(conn)[0]
    assert p.supplier_id == s.id


def test_import_invalid_supplier_records_error(conn):
    csv_data = _csv("sku,name,unit_price,supplier_id\nY-1,Item,5.00,999\n")
    result = service.import_products_csv(conn, csv_data)
    assert result["created"] == 0
    assert len(result["errors"]) == 1


# ── export_stock_csv service tests ───────────────────────────────────────────

def test_export_stock_headers(conn):
    buf = io.StringIO()
    service.export_stock_csv(conn, buf)
    lines = buf.getvalue().splitlines()
    assert lines[0] == "id,sku,name,quantity,reorder_threshold,status"


def test_export_stock_content(conn):
    p = service.create_product(conn, Product(None, "Z-1", "Zeta", 1.0, reorder_threshold=5))
    po = service.create_order(conn, p.id, OrderType.PURCHASE, 10)
    service.fulfill_order(conn, po.id)
    buf = io.StringIO()
    service.export_stock_csv(conn, buf)
    lines = buf.getvalue().splitlines()
    assert len(lines) == 2
    assert "Z-1" in lines[1]
    assert "10" in lines[1]
    assert "OK" in lines[1]


def test_export_stock_low_status(conn):
    p = service.create_product(conn, Product(None, "LOW-1", "LowItem", 1.0, reorder_threshold=10))
    buf = io.StringIO()
    service.export_stock_csv(conn, buf)
    assert "LOW" in buf.getvalue()


def test_export_stock_empty(conn):
    buf = io.StringIO()
    service.export_stock_csv(conn, buf)
    lines = buf.getvalue().splitlines()
    assert len(lines) == 1  # header only


# ── export_orders_csv service tests ──────────────────────────────────────────

def test_export_orders_headers(conn):
    buf = io.StringIO()
    service.export_orders_csv(conn, buf)
    header = buf.getvalue().splitlines()[0]
    assert "order_type" in header
    assert "status" in header


def test_export_orders_content(conn):
    p = service.create_product(conn, Product(None, "ORD-1", "OrderItem", 5.0))
    o = service.create_order(conn, p.id, OrderType.PURCHASE, 3)
    buf = io.StringIO()
    service.export_orders_csv(conn, buf)
    content = buf.getvalue()
    assert "purchase" in content
    assert "pending" in content
    assert "OrderItem" in content


def test_export_orders_status_filter(conn):
    p = service.create_product(conn, Product(None, "ORD-2", "FilterItem", 5.0))
    o = service.create_order(conn, p.id, OrderType.PURCHASE, 5)
    service.fulfill_order(conn, o.id)
    service.create_order(conn, p.id, OrderType.PURCHASE, 2)

    from inventory.models import OrderStatus
    buf = io.StringIO()
    service.export_orders_csv(conn, buf, status=OrderStatus.FULFILLED)
    lines = buf.getvalue().splitlines()
    assert len(lines) == 2  # header + 1 fulfilled row
    assert "fulfilled" in lines[1]


# ── API import/export tests ───────────────────────────────────────────────────

def _upload(client, csv_text):
    data = {"file": (io.BytesIO(csv_text.encode()), "products.csv")}
    return client.post("/api/v1/import/products", data=data, content_type="multipart/form-data")


def test_api_import_products(client):
    r = _upload(client, "sku,name,unit_price\nA-1,Alpha,4.99\n")
    assert r.status_code == 200
    data = r.get_json()
    assert data["created"] == 1
    assert data["skipped"] == 0
    assert data["errors"] == []


def test_api_import_products_partial_errors_returns_207(client):
    r = _upload(client, "sku,name,unit_price\nA-1,Good,5.0\n,Bad,\n")
    assert r.status_code == 207
    data = r.get_json()
    assert data["created"] == 1
    assert len(data["errors"]) == 1


def test_api_import_no_file(client):
    r = client.post("/api/v1/import/products")
    assert r.status_code == 400


def test_api_import_missing_column(client):
    r = _upload(client, "sku,unit_price\nA-1,4.99\n")
    assert r.status_code == 400


def test_api_export_stock(seeded_client):
    client, _ = seeded_client
    r = client.get("/api/v1/export/stock.csv")
    assert r.status_code == 200
    assert r.content_type.startswith("text/csv")
    assert b"sku" in r.data
    assert b"WGT-001" in r.data


def test_api_export_stock_empty(client):
    r = client.get("/api/v1/export/stock.csv")
    assert r.status_code == 200
    lines = r.data.decode().splitlines()
    assert len(lines) == 1  # header only


def test_api_export_orders(seeded_client):
    client, _ = seeded_client
    r = client.get("/api/v1/export/orders.csv")
    assert r.status_code == 200
    assert r.content_type.startswith("text/csv")
    assert b"order_type" in r.data
    assert b"purchase" in r.data


def test_api_export_orders_status_filter(seeded_client):
    client, _ = seeded_client
    r = client.get("/api/v1/export/orders.csv?status=fulfilled")
    assert r.status_code == 200
    lines = r.data.decode().splitlines()
    assert len(lines) == 2  # header + 1 fulfilled row


def test_api_export_orders_invalid_status(seeded_client):
    client, _ = seeded_client
    r = client.get("/api/v1/export/orders.csv?status=bogus")
    assert r.status_code == 400


def test_api_export_orders_disposition_header(seeded_client):
    client, _ = seeded_client
    r = client.get("/api/v1/export/orders.csv")
    assert "orders.csv" in r.headers.get("Content-Disposition", "")
