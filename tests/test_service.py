import pytest
import sqlite3

from inventory.db import init_db
from inventory.models import OrderType, OrderStatus, Product
from inventory import service
from inventory.service import InsufficientStock, InvalidStatusTransition, ProductNotFound


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def widget(conn):
    return service.create_product(conn, Product(None, "WGT-001", "Widget", 9.99, reorder_threshold=5))


# ── product tests ────────────────────────────────────────────────────────────

def test_create_product(conn):
    p = service.create_product(conn, Product(None, "SKU-1", "Gizmo", 4.99))
    assert p.id is not None
    assert p.sku == "SKU-1"


def test_create_product_initializes_zero_stock(conn, widget):
    stock = service.get_stock(conn, widget.id)
    assert stock.quantity == 0


def test_get_unknown_product_raises(conn):
    with pytest.raises(ProductNotFound):
        service.get_product(conn, 999)


def test_update_product(conn, widget):
    widget.name = "Super Widget"
    widget.unit_price = 14.99
    updated = service.update_product(conn, widget)
    assert updated.name == "Super Widget"
    assert updated.unit_price == 14.99


def test_list_products(conn):
    service.create_product(conn, Product(None, "A", "Alpha", 1.0))
    service.create_product(conn, Product(None, "B", "Beta", 2.0))
    products = service.list_products(conn)
    assert len(products) == 2


# ── order + stock tests ──────────────────────────────────────────────────────

def test_fulfill_purchase_increases_stock(conn, widget):
    order = service.create_order(conn, widget.id, OrderType.PURCHASE, 20)
    service.fulfill_order(conn, order.id)
    assert service.get_stock(conn, widget.id).quantity == 20


def test_fulfill_sale_decreases_stock(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 20)
    service.fulfill_order(conn, po.id)

    so = service.create_order(conn, widget.id, OrderType.SALE, 7)
    service.fulfill_order(conn, so.id)
    assert service.get_stock(conn, widget.id).quantity == 13


def test_fulfill_sale_raises_on_insufficient_stock(conn, widget):
    so = service.create_order(conn, widget.id, OrderType.SALE, 1)
    with pytest.raises(InsufficientStock):
        service.fulfill_order(conn, so.id)


def test_cannot_fulfill_already_fulfilled_order(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 10)
    service.fulfill_order(conn, po.id)
    with pytest.raises(InvalidStatusTransition):
        service.fulfill_order(conn, po.id)


def test_cancel_pending_order_does_not_change_stock(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 10)
    service.cancel_order(conn, po.id)
    assert service.get_stock(conn, widget.id).quantity == 0


def test_cancel_fulfilled_purchase_reverses_stock(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 10)
    service.fulfill_order(conn, po.id)
    service.cancel_order(conn, po.id)
    assert service.get_stock(conn, widget.id).quantity == 0


def test_cancel_fulfilled_sale_restores_stock(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 10)
    service.fulfill_order(conn, po.id)

    so = service.create_order(conn, widget.id, OrderType.SALE, 4)
    service.fulfill_order(conn, so.id)
    assert service.get_stock(conn, widget.id).quantity == 6

    service.cancel_order(conn, so.id)
    assert service.get_stock(conn, widget.id).quantity == 10


def test_cancel_already_cancelled_order_raises(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 5)
    service.cancel_order(conn, po.id)
    with pytest.raises(InvalidStatusTransition):
        service.cancel_order(conn, po.id)


def test_order_total(conn, widget):
    o = service.create_order(conn, widget.id, OrderType.PURCHASE, 3, unit_price=9.99)
    assert o.total == 29.97


def test_list_orders_filter_by_status(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 10)
    service.fulfill_order(conn, po.id)
    service.create_order(conn, widget.id, OrderType.PURCHASE, 5)

    pending = service.list_orders(conn, status=OrderStatus.PENDING)
    fulfilled = service.list_orders(conn, status=OrderStatus.FULFILLED)
    assert len(pending) == 1
    assert len(fulfilled) == 1


# ── alert tests ──────────────────────────────────────────────────────────────

def test_alert_created_when_stock_at_threshold(conn, widget):
    # widget threshold is 5; bring stock to exactly 5
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 5)
    service.fulfill_order(conn, po.id)
    alerts = service.list_alerts(conn, unacknowledged_only=True)
    assert len(alerts) == 1
    assert "Widget" in alerts[0].message


def test_no_alert_when_stock_above_threshold(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 20)
    service.fulfill_order(conn, po.id)
    assert service.list_alerts(conn, unacknowledged_only=True) == []


def test_acknowledge_alert(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 2)
    service.fulfill_order(conn, po.id)
    alerts = service.list_alerts(conn, unacknowledged_only=True)
    assert alerts

    service.acknowledge_alert(conn, alerts[0].id)
    assert service.list_alerts(conn, unacknowledged_only=True) == []
