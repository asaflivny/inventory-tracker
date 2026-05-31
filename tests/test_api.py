import os
import tempfile
import pytest
from pathlib import Path

from inventory.web import create_app
from inventory.db import get_connection, init_db
from inventory.models import Product, OrderType, Supplier
from inventory import service


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    fd, path = tempfile.mkstemp(suffix=".db")
    app = create_app(Path(path))
    app.config["TESTING"] = True
    yield app.test_client()
    os.close(fd)
    os.unlink(path)


@pytest.fixture
def seeded():
    """Client with one product (Widget, 20 units fulfilled) pre-loaded."""
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


def get(c, path, **kw):
    return c.get(f"/api/v1{path}", **kw)


def post(c, path, json=None, **kw):
    return c.post(f"/api/v1{path}", json=json, **kw)


def patch(c, path, json=None, **kw):
    return c.patch(f"/api/v1{path}", json=json, **kw)


def delete(c, path, **kw):
    return c.delete(f"/api/v1{path}", **kw)


# ── products ──────────────────────────────────────────────────────────────────

def test_products_list_empty(client):
    r = get(client, "/products")
    assert r.status_code == 200
    assert r.get_json() == []


def test_products_create(client):
    r = post(client, "/products", json={"sku": "A-1", "name": "Alpha", "unit_price": 4.99})
    assert r.status_code == 201
    data = r.get_json()
    assert data["sku"] == "A-1"
    assert data["unit_price"] == 4.99
    assert data["id"] is not None


def test_products_create_missing_field(client):
    r = post(client, "/products", json={"sku": "X"})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_products_create_duplicate_sku(client):
    post(client, "/products", json={"sku": "DUP", "name": "First", "unit_price": 1.0})
    r = post(client, "/products", json={"sku": "DUP", "name": "Second", "unit_price": 1.0})
    assert r.status_code == 400


def test_products_get(seeded):
    client, p = seeded
    r = get(client, f"/products/{p.id}")
    assert r.status_code == 200
    assert r.get_json()["name"] == "Widget"


def test_products_get_not_found(client):
    r = get(client, "/products/999")
    assert r.status_code == 404


def test_products_update(seeded):
    client, p = seeded
    r = patch(client, f"/products/{p.id}", json={"name": "Super Widget", "unit_price": 14.99})
    assert r.status_code == 200
    data = r.get_json()
    assert data["name"] == "Super Widget"
    assert data["unit_price"] == 14.99


def test_products_update_not_found(client):
    r = patch(client, "/products/999", json={"name": "Ghost"})
    assert r.status_code == 404


def test_products_delete(client):
    r = post(client, "/products", json={"sku": "DEL", "name": "Deletable", "unit_price": 1.0})
    pid = r.get_json()["id"]
    r2 = delete(client, f"/products/{pid}")
    assert r2.status_code == 204
    assert get(client, f"/products/{pid}").status_code == 404


def test_products_delete_blocked_by_orders(seeded):
    client, p = seeded
    r = delete(client, f"/products/{p.id}")
    assert r.status_code == 409


def test_products_delete_not_found(client):
    r = delete(client, "/products/999")
    assert r.status_code == 404


# ── stock ─────────────────────────────────────────────────────────────────────

def test_stock_list(seeded):
    client, p = seeded
    r = get(client, "/stock")
    assert r.status_code == 200
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["quantity"] == 20


def test_stock_get(seeded):
    client, p = seeded
    r = get(client, f"/stock/{p.id}")
    assert r.status_code == 200
    assert r.get_json()["quantity"] == 20


def test_stock_get_not_found(client):
    assert get(client, "/stock/999").status_code == 404


def test_stock_adjust_positive(seeded):
    client, p = seeded
    r = post(client, f"/stock/{p.id}/adjust", json={"delta": 5})
    assert r.status_code == 200
    assert r.get_json()["quantity"] == 25


def test_stock_adjust_negative(seeded):
    client, p = seeded
    r = post(client, f"/stock/{p.id}/adjust", json={"delta": -3})
    assert r.get_json()["quantity"] == 17


def test_stock_adjust_below_zero(seeded):
    client, p = seeded
    r = post(client, f"/stock/{p.id}/adjust", json={"delta": -999})
    assert r.status_code == 409


def test_stock_adjust_missing_delta(seeded):
    client, p = seeded
    r = post(client, f"/stock/{p.id}/adjust", json={})
    assert r.status_code == 400


def test_stock_history(seeded):
    client, p = seeded
    r = get(client, f"/stock/{p.id}/history")
    assert r.status_code == 200
    history = r.get_json()
    assert len(history) == 1
    assert history[0]["balance"] == 20
    assert history[0]["order_type"] == "purchase"


def test_stock_history_not_found(client):
    assert get(client, "/stock/999/history").status_code == 404


# ── orders ────────────────────────────────────────────────────────────────────

def test_orders_list(seeded):
    client, p = seeded
    r = get(client, "/orders")
    assert r.status_code == 200
    orders = r.get_json()
    assert len(orders) == 1
    assert orders[0]["product_name"] == "Widget"


def test_orders_buy(seeded):
    client, p = seeded
    r = post(client, "/orders/buy", json={"product_id": p.id, "quantity": 10})
    assert r.status_code == 201
    data = r.get_json()
    assert data["order_type"] == "purchase"
    assert data["status"] == "pending"


def test_orders_buy_and_fulfill(seeded):
    client, p = seeded
    r = post(client, "/orders/buy", json={"product_id": p.id, "quantity": 5, "fulfill": True})
    assert r.get_json()["status"] == "fulfilled"


def test_orders_buy_missing_field(client):
    r = post(client, "/orders/buy", json={"quantity": 5})
    assert r.status_code == 400


def test_orders_buy_unknown_product(client):
    r = post(client, "/orders/buy", json={"product_id": 999, "quantity": 5})
    assert r.status_code == 404


def test_orders_sell(seeded):
    client, p = seeded
    r = post(client, "/orders/sell", json={"product_id": p.id, "quantity": 5})
    assert r.status_code == 201
    assert r.get_json()["order_type"] == "sale"


def test_orders_sell_over_stock(seeded):
    client, p = seeded
    r = post(client, "/orders/sell", json={"product_id": p.id, "quantity": 999, "fulfill": True})
    assert r.status_code == 409


def test_orders_fulfill(seeded):
    client, p = seeded
    buy = post(client, "/orders/buy", json={"product_id": p.id, "quantity": 5}).get_json()
    r = post(client, f"/orders/{buy['id']}/fulfill")
    assert r.status_code == 200
    assert r.get_json()["status"] == "fulfilled"


def test_orders_fulfill_already_fulfilled(seeded):
    client, p = seeded
    orders = get(client, "/orders").get_json()
    fulfilled_id = orders[0]["id"]
    r = post(client, f"/orders/{fulfilled_id}/fulfill")
    assert r.status_code == 409


def test_orders_cancel(seeded):
    client, p = seeded
    buy = post(client, "/orders/buy", json={"product_id": p.id, "quantity": 3}).get_json()
    r = post(client, f"/orders/{buy['id']}/cancel")
    assert r.status_code == 200
    assert r.get_json()["status"] == "cancelled"


def test_orders_filter_by_status(seeded):
    client, p = seeded
    r = get(client, "/orders?status=fulfilled")
    orders = r.get_json()
    assert all(o["status"] == "fulfilled" for o in orders)


def test_orders_filter_invalid_status(seeded):
    client, p = seeded
    r = get(client, "/orders?status=bogus")
    assert r.status_code == 400


def test_orders_filter_by_date_excludes_past(seeded):
    client, p = seeded
    r = get(client, "/orders?since=2030-01-01")
    assert r.get_json() == []


# ── alerts ────────────────────────────────────────────────────────────────────

def test_alerts_list_empty(client):
    r = get(client, "/alerts")
    assert r.status_code == 200
    assert r.get_json() == []


def test_alerts_list_after_low_stock(seeded):
    client, p = seeded
    post(client, "/orders/sell", json={"product_id": p.id, "quantity": 17, "fulfill": True})
    r = get(client, "/alerts?unacked=1")
    alerts = r.get_json()
    assert len(alerts) == 1
    assert alerts[0]["acknowledged"] is False


def test_alerts_ack(seeded):
    client, p = seeded
    post(client, "/orders/sell", json={"product_id": p.id, "quantity": 17, "fulfill": True})
    alert_id = get(client, "/alerts").get_json()[0]["id"]
    r = post(client, f"/alerts/{alert_id}/ack")
    assert r.status_code == 200
    assert r.get_json()["acknowledged"] is True


def test_alerts_ack_not_found(client):
    r = post(client, "/alerts/999/ack")
    assert r.status_code == 404


def test_alerts_ack_all(seeded):
    client, p = seeded
    post(client, "/orders/sell", json={"product_id": p.id, "quantity": 17, "fulfill": True})
    r = post(client, "/alerts/ack-all")
    assert r.status_code == 200
    assert r.get_json()["acknowledged"] == 1
    assert get(client, "/alerts?unacked=1").get_json() == []


# ── summary & reorder ─────────────────────────────────────────────────────────

def test_summary(seeded):
    client, p = seeded
    r = get(client, "/summary")
    assert r.status_code == 200
    data = r.get_json()
    assert data["products"] == 1
    assert data["stock_value"] == pytest.approx(20 * 9.99, rel=1e-6)


def test_reorder_empty(seeded):
    client, p = seeded
    r = get(client, "/reorder")
    assert r.status_code == 200
    assert r.get_json() == []


def test_reorder_shows_low_stock(seeded):
    client, p = seeded
    post(client, "/orders/sell", json={"product_id": p.id, "quantity": 17, "fulfill": True})
    r = get(client, "/reorder")
    rows = r.get_json()
    assert len(rows) == 1
    assert rows[0]["shortfall"] == 2  # threshold=5, qty=3


# ── suppliers ─────────────────────────────────────────────────────────────────

def test_suppliers_list_empty(client):
    r = get(client, "/suppliers")
    assert r.status_code == 200
    assert r.get_json() == []


def test_suppliers_create(client):
    r = post(client, "/suppliers", json={"name": "Acme", "lead_time_days": 3})
    assert r.status_code == 201
    data = r.get_json()
    assert data["name"] == "Acme"
    assert data["lead_time_days"] == 3
    assert data["id"] is not None


def test_suppliers_create_missing_name(client):
    r = post(client, "/suppliers", json={"lead_time_days": 1})
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_suppliers_get(client):
    sid = post(client, "/suppliers", json={"name": "Globex"}).get_json()["id"]
    r = get(client, f"/suppliers/{sid}")
    assert r.status_code == 200
    assert r.get_json()["name"] == "Globex"


def test_suppliers_get_not_found(client):
    assert get(client, "/suppliers/999").status_code == 404


def test_suppliers_update(client):
    sid = post(client, "/suppliers", json={"name": "OldName"}).get_json()["id"]
    r = patch(client, f"/suppliers/{sid}", json={"name": "NewName", "lead_time_days": 7})
    assert r.status_code == 200
    data = r.get_json()
    assert data["name"] == "NewName"
    assert data["lead_time_days"] == 7


def test_suppliers_update_not_found(client):
    r = patch(client, "/suppliers/999", json={"name": "Ghost"})
    assert r.status_code == 404


def test_suppliers_delete(client):
    sid = post(client, "/suppliers", json={"name": "ToDelete"}).get_json()["id"]
    r = delete(client, f"/suppliers/{sid}")
    assert r.status_code == 204
    assert get(client, f"/suppliers/{sid}").status_code == 404


def test_suppliers_delete_blocked_by_product(client):
    sid = post(client, "/suppliers", json={"name": "Linked"}).get_json()["id"]
    post(client, "/products", json={"sku": "S-1", "name": "Item", "unit_price": 1.0, "supplier_id": sid})
    r = delete(client, f"/suppliers/{sid}")
    assert r.status_code == 409


def test_suppliers_delete_not_found(client):
    assert delete(client, "/suppliers/999").status_code == 404


def test_suppliers_products(client):
    sid = post(client, "/suppliers", json={"name": "Acme"}).get_json()["id"]
    post(client, "/products", json={"sku": "P-1", "name": "Prod1", "unit_price": 5.0, "supplier_id": sid})
    post(client, "/products", json={"sku": "P-2", "name": "Prod2", "unit_price": 6.0})
    r = get(client, f"/suppliers/{sid}/products")
    assert r.status_code == 200
    products = r.get_json()
    assert len(products) == 1
    assert products[0]["sku"] == "P-1"


def test_product_includes_supplier_id(client):
    sid = post(client, "/suppliers", json={"name": "Acme"}).get_json()["id"]
    r = post(client, "/products", json={"sku": "X-1", "name": "Linked", "unit_price": 3.0, "supplier_id": sid})
    assert r.get_json()["supplier_id"] == sid


def test_product_create_invalid_supplier(client):
    r = post(client, "/products", json={"sku": "X-2", "name": "Ghost", "unit_price": 1.0, "supplier_id": 999})
    assert r.status_code == 400
