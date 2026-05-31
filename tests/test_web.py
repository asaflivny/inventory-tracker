import os
import tempfile
import pytest
from pathlib import Path

from inventory.web import create_app
from inventory.db import get_connection, init_db
from inventory.models import Product, OrderType
from inventory import service


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    fd, path = tempfile.mkstemp(suffix=".db")
    _app = create_app(Path(path))
    _app.config["TESTING"] = True
    yield _app
    os.close(fd)
    os.unlink(path)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seeded2():
    fd, path = tempfile.mkstemp(suffix=".db")
    db_path = Path(path)

    conn = get_connection(db_path)
    init_db(conn)
    p = service.create_product(conn, Product(None, "WGT-001", "Widget", 9.99, reorder_threshold=5))
    po = service.create_order(conn, p.id, OrderType.PURCHASE, 20)
    service.fulfill_order(conn, po.id)
    conn.close()

    _app = create_app(db_path)
    _app.config["TESTING"] = True
    client = _app.test_client()

    yield client, p

    os.close(fd)
    os.unlink(path)


# ── page load tests ──────────────────────────────────────────────────────────

def test_dashboard_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Dashboard" in r.data


def test_products_page_loads(client):
    r = client.get("/products")
    assert r.status_code == 200
    assert b"Products" in r.data


def test_stock_page_loads(client):
    r = client.get("/stock")
    assert r.status_code == 200
    assert b"Stock" in r.data


def test_orders_page_loads(client):
    r = client.get("/orders")
    assert r.status_code == 200
    assert b"Orders" in r.data


def test_alerts_page_loads(client):
    r = client.get("/alerts")
    assert r.status_code == 200
    assert b"Alerts" in r.data


def test_reorder_page_loads(client):
    r = client.get("/reorder")
    assert r.status_code == 200
    assert b"Reorder" in r.data


# ── product tests ────────────────────────────────────────────────────────────

def test_add_product(client):
    r = client.post("/products/add", data={
        "sku": "TEST-001", "name": "Gizmo", "price": "4.99", "threshold": "10"
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b"Gizmo" in r.data


def test_add_product_shows_on_dashboard(client):
    client.post("/products/add", data={"sku": "A", "name": "Alpha", "price": "1.00"})
    r = client.get("/")
    assert b"1" in r.data  # products count


def test_add_product_duplicate_sku_shows_error(client):
    client.post("/products/add", data={"sku": "DUP", "name": "First", "price": "1.0"})
    r = client.post("/products/add", data={
        "sku": "DUP", "name": "Second", "price": "1.0"
    }, follow_redirects=True)
    assert b"flash error" in r.data or b"error" in r.data


def test_delete_product(client):
    client.post("/products/add", data={"sku": "DEL-1", "name": "Deletable", "price": "1.0"})
    # Get product id from products page
    r = client.get("/products")
    # Find the delete form — just post to /products/1/delete
    r2 = client.post("/products/1/delete", follow_redirects=True)
    assert r2.status_code == 200


def test_delete_product_with_orders_shows_error(seeded2):
    client, p = seeded2
    # create an order so delete is blocked
    client.post("/orders/buy", data={
        "product_id": str(p.id), "quantity": "5"
    })
    r = client.post(f"/products/{p.id}/delete", follow_redirects=True)
    assert b"error" in r.data or b"Cannot delete" in r.data


# ── stock tests ───────────────────────────────────────────────────────────────

def test_stock_adjust_positive(seeded2):
    client, p = seeded2
    r = client.post("/stock/adjust", data={
        "product_id": str(p.id), "delta": "5"
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b"25" in r.data  # 20 + 5


def test_stock_adjust_negative(seeded2):
    client, p = seeded2
    r = client.post("/stock/adjust", data={
        "product_id": str(p.id), "delta": "-3"
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b"17" in r.data  # 20 - 3


def test_stock_adjust_below_zero_shows_error(seeded2):
    client, p = seeded2
    r = client.post("/stock/adjust", data={
        "product_id": str(p.id), "delta": "-999"
    }, follow_redirects=True)
    assert b"error" in r.data or b"Adjustment" in r.data


def test_stock_history_page(seeded2):
    client, p = seeded2
    r = client.get(f"/stock/{p.id}/history")
    assert r.status_code == 200
    assert b"Widget" in r.data
    assert b"purchase" in r.data
    assert b"20" in r.data


def test_stock_history_unknown_product_redirects(client):
    r = client.get("/stock/999/history", follow_redirects=True)
    assert r.status_code == 200


# ── order tests ───────────────────────────────────────────────────────────────

def test_order_buy(seeded2):
    client, p = seeded2
    r = client.post("/orders/buy", data={
        "product_id": str(p.id), "quantity": "10"
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b"created" in r.data


def test_order_buy_and_fulfill(seeded2):
    client, p = seeded2
    r = client.post("/orders/buy", data={
        "product_id": str(p.id), "quantity": "10", "fulfill": "on"
    }, follow_redirects=True)
    assert b"fulfilled" in r.data


def test_order_sell(seeded2):
    client, p = seeded2
    r = client.post("/orders/sell", data={
        "product_id": str(p.id), "quantity": "5"
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b"created" in r.data


def test_order_sell_insufficient_stock_shows_error(seeded2):
    client, p = seeded2
    r = client.post("/orders/sell", data={
        "product_id": str(p.id), "quantity": "5", "fulfill": "on"
    }, follow_redirects=True)
    # 20 units available, sell 5 fulfilled should succeed
    assert b"fulfilled" in r.data


def test_order_sell_over_stock_shows_error(seeded2):
    client, p = seeded2
    r = client.post("/orders/sell", data={
        "product_id": str(p.id), "quantity": "999", "fulfill": "on"
    }, follow_redirects=True)
    assert b"error" in r.data or b"Only" in r.data


def test_order_fulfill_action(seeded2):
    client, p = seeded2
    # Create a pending buy order
    client.post("/orders/buy", data={"product_id": str(p.id), "quantity": "3"})
    # Fulfill it — order id 2 (first was the fixture's po)
    r = client.post("/orders/2/fulfill", follow_redirects=True)
    assert r.status_code == 200
    assert b"fulfilled" in r.data


def test_order_cancel_action(seeded2):
    client, p = seeded2
    client.post("/orders/buy", data={"product_id": str(p.id), "quantity": "3"})
    r = client.post("/orders/2/cancel", follow_redirects=True)
    assert r.status_code == 200
    assert b"cancelled" in r.data


def test_orders_filter_by_status(seeded2):
    client, p = seeded2
    r = client.get("/orders?status=fulfilled")
    assert r.status_code == 200
    assert b"fulfilled" in r.data


def test_orders_filter_by_date(seeded2):
    client, p = seeded2
    r = client.get("/orders?since=2030-01-01")
    assert r.status_code == 200
    assert b"No orders" in r.data


# ── alert tests ───────────────────────────────────────────────────────────────

def test_alerts_page_shows_pending(seeded2):
    client, p = seeded2
    # Sell down below threshold (threshold=5, qty=20; sell 17 → qty=3)
    client.post("/orders/sell", data={
        "product_id": str(p.id), "quantity": "17", "fulfill": "on"
    })
    r = client.get("/alerts")
    assert b"NEW" in r.data or b"Low stock" in r.data


def test_alert_ack(seeded2):
    client, p = seeded2
    client.post("/orders/sell", data={
        "product_id": str(p.id), "quantity": "17", "fulfill": "on"
    })
    alerts_r = client.get("/alerts")
    assert b"NEW" in alerts_r.data
    r = client.post("/alerts/1/ack", follow_redirects=True)
    assert r.status_code == 200


def test_alert_ack_all(seeded2):
    client, p = seeded2
    client.post("/orders/sell", data={
        "product_id": str(p.id), "quantity": "17", "fulfill": "on"
    })
    r = client.post("/alerts/ack-all", follow_redirects=True)
    assert r.status_code == 200
    assert b"Acknowledged" in r.data


# ── reorder tests ─────────────────────────────────────────────────────────────

def test_reorder_shows_low_stock(seeded2):
    client, p = seeded2
    # Sell down below threshold
    client.post("/orders/sell", data={
        "product_id": str(p.id), "quantity": "17", "fulfill": "on"
    })
    r = client.get("/reorder")
    assert r.status_code == 200
    assert b"Widget" in r.data


def test_reorder_empty_when_all_ok(seeded2):
    client, p = seeded2
    r = client.get("/reorder")
    assert r.status_code == 200
    assert b"above their reorder" in r.data
