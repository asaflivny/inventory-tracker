import pytest
import sqlite3

from inventory.db import init_db
from inventory.models import OrderType, OrderStatus, Product, Supplier
from inventory import service
from inventory.service import InsufficientStock, InvalidStatusTransition, ProductNotFound, SupplierNotFound


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


# ── delete product tests ─────────────────────────────────────────────────────

def test_delete_product(conn):
    from inventory.models import Product
    p = service.create_product(conn, Product(None, "DEL-001", "Deletable", 1.00))
    service.delete_product(conn, p.id)
    with pytest.raises(service.ProductNotFound):
        service.get_product(conn, p.id)


def test_delete_product_blocked_when_has_orders(conn, widget):
    service.create_order(conn, widget.id, OrderType.PURCHASE, 5)
    with pytest.raises(service.InventoryError):
        service.delete_product(conn, widget.id)


def test_delete_product_not_found(conn):
    with pytest.raises(service.ProductNotFound):
        service.delete_product(conn, 999)


# ── summary tests ────────────────────────────────────────────────────────────

def test_summary_empty(conn):
    s = service.summary(conn)
    assert s["products"] == 0
    assert s["stock_value"] == 0
    assert s["pending_orders"] == 0
    assert s["low_stock_products"] == 0
    assert s["unacknowledged_alerts"] == 0


def test_summary_counts(conn, widget):
    # pending order
    service.create_order(conn, widget.id, OrderType.PURCHASE, 3)
    # fulfilled purchase that leaves stock below threshold (widget threshold=5, qty=3)
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 3)
    service.fulfill_order(conn, po.id)

    s = service.summary(conn)
    assert s["products"] == 1
    assert s["pending_orders"] == 1
    assert s["low_stock_products"] == 1
    assert s["unacknowledged_alerts"] == 1
    assert s["stock_value"] == pytest.approx(3 * widget.unit_price, rel=1e-6)


# ── acknowledge_all_alerts tests ─────────────────────────────────────────────

def test_acknowledge_all_alerts(conn, widget):
    # generate two alerts: bring stock to 0 via two separate fulfilled purchases
    po1 = service.create_order(conn, widget.id, OrderType.PURCHASE, 2)
    service.fulfill_order(conn, po1.id)
    po2 = service.create_order(conn, widget.id, OrderType.PURCHASE, 1)
    service.fulfill_order(conn, po2.id)

    pending = service.list_alerts(conn, unacknowledged_only=True)
    assert len(pending) >= 1

    count = service.acknowledge_all_alerts(conn)
    assert count == len(pending)
    assert service.list_alerts(conn, unacknowledged_only=True) == []


def test_acknowledge_all_alerts_no_pending(conn):
    count = service.acknowledge_all_alerts(conn)
    assert count == 0


# ── adjust_stock tests ───────────────────────────────────────────────────────

def test_adjust_stock_positive(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 10)
    service.fulfill_order(conn, po.id)
    s = service.adjust_stock(conn, widget.id, 5)
    assert s.quantity == 15


def test_adjust_stock_negative(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 10)
    service.fulfill_order(conn, po.id)
    s = service.adjust_stock(conn, widget.id, -3)
    assert s.quantity == 7


def test_adjust_stock_to_zero(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 5)
    service.fulfill_order(conn, po.id)
    s = service.adjust_stock(conn, widget.id, -5)
    assert s.quantity == 0


def test_adjust_stock_below_zero_raises(conn, widget):
    with pytest.raises(service.InsufficientStock):
        service.adjust_stock(conn, widget.id, -1)


def test_adjust_stock_triggers_alert(conn, widget):
    # widget threshold=5; add 5 then remove 3 → qty=2, below threshold
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 10)
    service.fulfill_order(conn, po.id)
    service.acknowledge_all_alerts(conn)

    service.adjust_stock(conn, widget.id, -8)  # 10 - 8 = 2, below threshold 5
    alerts = service.list_alerts(conn, unacknowledged_only=True)
    assert len(alerts) == 1


# ── stock_history tests ──────────────────────────────────────────────────────

def test_stock_history_empty(conn, widget):
    assert service.stock_history(conn, widget.id) == []


def test_stock_history_running_balance(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 20)
    service.fulfill_order(conn, po.id)
    so = service.create_order(conn, widget.id, OrderType.SALE, 7)
    service.fulfill_order(conn, so.id)

    history = service.stock_history(conn, widget.id)
    assert len(history) == 2
    assert history[0]["balance"] == 20
    assert history[1]["balance"] == 13


def test_stock_history_excludes_pending(conn, widget):
    service.create_order(conn, widget.id, OrderType.PURCHASE, 10)  # pending, not fulfilled
    assert service.stock_history(conn, widget.id) == []


def test_stock_history_excludes_cancelled(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 10)
    service.fulfill_order(conn, po.id)
    service.cancel_order(conn, po.id)
    assert service.stock_history(conn, widget.id) == []


# ── list_reorder tests ───────────────────────────────────────────────────────

def test_list_reorder_empty_when_all_ok(conn, widget):
    po = service.create_order(conn, widget.id, OrderType.PURCHASE, 100)
    service.fulfill_order(conn, po.id)
    assert service.list_reorder(conn) == []


def test_list_reorder_shows_low_stock(conn, widget):
    # widget starts at qty=0, threshold=5 → should appear
    rows = service.list_reorder(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == widget.id
    assert rows[0]["shortfall"] == 5


def test_list_reorder_sorted_by_shortfall(conn):
    from inventory.models import Product
    p1 = service.create_product(conn, Product(None, "A", "Alpha", 1.0, reorder_threshold=10))
    p2 = service.create_product(conn, Product(None, "B", "Beta", 1.0, reorder_threshold=20))
    # Both at qty=0; p2 has bigger shortfall
    rows = service.list_reorder(conn)
    assert rows[0]["id"] == p2.id
    assert rows[1]["id"] == p1.id


# ── list_orders date filter + product name tests ─────────────────────────────

def test_list_orders_includes_product_name(conn, widget):
    service.create_order(conn, widget.id, OrderType.PURCHASE, 5)
    orders = service.list_orders(conn)
    assert orders[0].product_name == widget.name
    assert orders[0].product_sku == widget.sku


def test_list_orders_filter_by_since(conn, widget):
    from datetime import datetime, timedelta
    service.create_order(conn, widget.id, OrderType.PURCHASE, 5)
    tomorrow = datetime.utcnow() + timedelta(days=1)
    orders = service.list_orders(conn, since=tomorrow)
    assert orders == []


def test_list_orders_filter_by_until(conn, widget):
    from datetime import datetime, timedelta
    service.create_order(conn, widget.id, OrderType.PURCHASE, 5)
    yesterday = datetime.utcnow() - timedelta(days=1)
    orders = service.list_orders(conn, until=yesterday)
    assert orders == []


def test_list_orders_since_until_includes_today(conn, widget):
    from datetime import datetime
    service.create_order(conn, widget.id, OrderType.PURCHASE, 5)
    today = datetime.utcnow()
    orders = service.list_orders(conn, since=today, until=today)
    assert len(orders) == 1


# ── supplier tests ───────────────────────────────────────────────────────────

@pytest.fixture
def acme(conn):
    return service.create_supplier(conn, Supplier(None, "Acme Corp", "Alice", "alice@acme.com", "555-1234", 3))


def test_create_supplier(conn):
    s = service.create_supplier(conn, Supplier(None, "Globex", lead_time_days=5))
    assert s.id is not None
    assert s.name == "Globex"
    assert s.lead_time_days == 5


def test_get_supplier(conn, acme):
    s = service.get_supplier(conn, acme.id)
    assert s.name == "Acme Corp"
    assert s.email == "alice@acme.com"


def test_get_supplier_not_found(conn):
    with pytest.raises(SupplierNotFound):
        service.get_supplier(conn, 999)


def test_list_suppliers(conn, acme):
    service.create_supplier(conn, Supplier(None, "Initech"))
    suppliers = service.list_suppliers(conn)
    assert len(suppliers) == 2
    assert suppliers[0].name == "Acme Corp"  # alphabetical


def test_update_supplier(conn, acme):
    acme.lead_time_days = 7
    acme.contact_name = "Bob"
    updated = service.update_supplier(conn, acme)
    assert updated.lead_time_days == 7
    assert updated.contact_name == "Bob"


def test_delete_supplier(conn, acme):
    service.delete_supplier(conn, acme.id)
    with pytest.raises(SupplierNotFound):
        service.get_supplier(conn, acme.id)


def test_delete_supplier_blocked_when_product_linked(conn, acme):
    service.create_product(conn, Product(None, "S-1", "SuppliedItem", 5.0, supplier_id=acme.id))
    with pytest.raises(service.InventoryError):
        service.delete_supplier(conn, acme.id)


def test_create_product_with_supplier(conn, acme):
    p = service.create_product(conn, Product(None, "S-2", "Widget Pro", 19.99, supplier_id=acme.id))
    assert p.supplier_id == acme.id


def test_create_product_with_invalid_supplier_raises(conn):
    with pytest.raises(SupplierNotFound):
        service.create_product(conn, Product(None, "S-3", "Ghost Item", 9.99, supplier_id=999))


def test_update_product_supplier(conn, acme):
    p = service.create_product(conn, Product(None, "S-4", "Switchable", 5.0))
    p.supplier_id = acme.id
    updated = service.update_product(conn, p)
    assert updated.supplier_id == acme.id


def test_supplier_invalid_name_raises():
    with pytest.raises(ValueError):
        Supplier(None, "")


def test_supplier_negative_lead_time_raises():
    with pytest.raises(ValueError):
        Supplier(None, "Bad", lead_time_days=-1)
