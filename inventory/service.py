import sqlite3
from datetime import datetime
from typing import Optional

from .db import transaction
from .models import Alert, Order, OrderStatus, OrderType, Product, StockLevel


class InventoryError(Exception):
    pass


class ProductNotFound(InventoryError):
    pass


class InsufficientStock(InventoryError):
    pass


class InvalidStatusTransition(InventoryError):
    pass


# ---------- products ----------

def create_product(conn: sqlite3.Connection, product: Product) -> Product:
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO products (sku, name, unit_price, reorder_threshold) VALUES (?, ?, ?, ?)",
            (product.sku, product.name, product.unit_price, product.reorder_threshold),
        )
        product_id = cur.lastrowid
        conn.execute(
            "INSERT INTO stock_levels (product_id, quantity) VALUES (?, 0)",
            (product_id,),
        )
    return _get_product_by_id(conn, product_id)


def get_product(conn: sqlite3.Connection, product_id: int) -> Product:
    return _get_product_by_id(conn, product_id)


def list_products(conn: sqlite3.Connection) -> list[Product]:
    rows = conn.execute("SELECT * FROM products ORDER BY sku").fetchall()
    return [_row_to_product(r) for r in rows]


def update_product(conn: sqlite3.Connection, product: Product) -> Product:
    _require_product(conn, product.id)
    with transaction(conn):
        conn.execute(
            "UPDATE products SET sku=?, name=?, unit_price=?, reorder_threshold=? WHERE id=?",
            (product.sku, product.name, product.unit_price, product.reorder_threshold, product.id),
        )
    return _get_product_by_id(conn, product.id)


# ---------- stock ----------

def get_stock(conn: sqlite3.Connection, product_id: int) -> StockLevel:
    _require_product(conn, product_id)
    row = conn.execute(
        "SELECT * FROM stock_levels WHERE product_id = ?", (product_id,)
    ).fetchone()
    return StockLevel(product_id=row["product_id"], quantity=row["quantity"])


def list_stock(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT p.id, p.sku, p.name, p.reorder_threshold, s.quantity
        FROM products p
        JOIN stock_levels s ON p.id = s.product_id
        ORDER BY p.sku
    """).fetchall()
    return [dict(r) for r in rows]


# ---------- orders ----------

def create_order(conn: sqlite3.Connection, product_id: int, order_type: OrderType,
                 quantity: int, unit_price: Optional[float] = None) -> Order:
    product = _require_product(conn, product_id)
    price = unit_price if unit_price is not None else product.unit_price

    order = Order(
        id=None,
        product_id=product_id,
        order_type=order_type,
        quantity=quantity,
        unit_price=price,
    )

    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO orders (product_id, order_type, quantity, unit_price, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (product_id, order_type.value, quantity, price,
             OrderStatus.PENDING.value, datetime.utcnow().isoformat()),
        )
        order.id = cur.lastrowid

    return order


def fulfill_order(conn: sqlite3.Connection, order_id: int) -> Order:
    order = _get_order(conn, order_id)

    if order.status != OrderStatus.PENDING:
        raise InvalidStatusTransition(
            f"Cannot fulfill order in status '{order.status.value}'"
        )

    with transaction(conn):
        if order.order_type == OrderType.SALE:
            stock = get_stock(conn, order.product_id)
            if stock.quantity < order.quantity:
                raise InsufficientStock(
                    f"Only {stock.quantity} units available, order requires {order.quantity}"
                )
            new_qty = stock.quantity - order.quantity
        else:
            stock = get_stock(conn, order.product_id)
            new_qty = stock.quantity + order.quantity

        conn.execute(
            "UPDATE stock_levels SET quantity = ? WHERE product_id = ?",
            (new_qty, order.product_id),
        )
        conn.execute(
            "UPDATE orders SET status = ? WHERE id = ?",
            (OrderStatus.FULFILLED.value, order_id),
        )
        order.status = OrderStatus.FULFILLED
        _insert_alert_if_low(conn, order.product_id, new_qty)

    return order


def cancel_order(conn: sqlite3.Connection, order_id: int) -> Order:
    order = _get_order(conn, order_id)

    if order.status == OrderStatus.CANCELLED:
        raise InvalidStatusTransition("Order is already cancelled")
    if order.status == OrderStatus.PENDING:
        # Pending orders haven't touched stock — just cancel
        with transaction(conn):
            conn.execute(
                "UPDATE orders SET status = ? WHERE id = ?",
                (OrderStatus.CANCELLED.value, order_id),
            )
        order.status = OrderStatus.CANCELLED
        return order

    # Fulfilled order: reverse the stock adjustment
    with transaction(conn):
        stock = get_stock(conn, order.product_id)
        if order.order_type == OrderType.SALE:
            new_qty = stock.quantity + order.quantity  # return stock
        else:
            new_qty = stock.quantity - order.quantity  # remove received stock
            if new_qty < 0:
                raise InsufficientStock(
                    f"Cannot cancel: reversing this purchase would bring stock to {new_qty}"
                )
        conn.execute(
            "UPDATE stock_levels SET quantity = ? WHERE product_id = ?",
            (new_qty, order.product_id),
        )
        conn.execute(
            "UPDATE orders SET status = ? WHERE id = ?",
            (OrderStatus.CANCELLED.value, order_id),
        )
        order.status = OrderStatus.CANCELLED
        _insert_alert_if_low(conn, order.product_id, new_qty)

    return order


def list_orders(conn: sqlite3.Connection, product_id: Optional[int] = None,
                status: Optional[OrderStatus] = None) -> list[Order]:
    query = "SELECT * FROM orders WHERE 1=1"
    params: list = []
    if product_id is not None:
        query += " AND product_id = ?"
        params.append(product_id)
    if status is not None:
        query += " AND status = ?"
        params.append(status.value)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_order(r) for r in rows]


# ---------- alerts ----------

def list_alerts(conn: sqlite3.Connection, unacknowledged_only: bool = False) -> list[Alert]:
    query = "SELECT * FROM alerts"
    if unacknowledged_only:
        query += " WHERE acknowledged = 0"
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query).fetchall()
    return [_row_to_alert(r) for r in rows]


def acknowledge_alert(conn: sqlite3.Connection, alert_id: int) -> Alert:
    row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if not row:
        raise InventoryError(f"Alert {alert_id} not found")
    with transaction(conn):
        conn.execute("UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
    return _row_to_alert(conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone())


# ---------- internal helpers ----------

def _insert_alert_if_low(conn: sqlite3.Connection, product_id: int, new_qty: int) -> None:
    product = _get_product_by_id(conn, product_id)
    if new_qty <= product.reorder_threshold:
        msg = (
            f"Low stock: {product.name} (SKU {product.sku}) has {new_qty} units "
            f"(threshold: {product.reorder_threshold})"
        )
        conn.execute(
            "INSERT INTO alerts (product_id, message, quantity_at_alert, created_at) VALUES (?, ?, ?, ?)",
            (product_id, msg, new_qty, datetime.utcnow().isoformat()),
        )


def _require_product(conn: sqlite3.Connection, product_id: int) -> Product:
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        raise ProductNotFound(f"Product {product_id} not found")
    return _row_to_product(row)


def _get_product_by_id(conn: sqlite3.Connection, product_id: int) -> Product:
    return _require_product(conn, product_id)


def _get_order(conn: sqlite3.Connection, order_id: int) -> Order:
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        raise InventoryError(f"Order {order_id} not found")
    return _row_to_order(row)


def _row_to_product(row) -> Product:
    return Product(
        id=row["id"],
        sku=row["sku"],
        name=row["name"],
        unit_price=row["unit_price"],
        reorder_threshold=row["reorder_threshold"],
    )


def _row_to_order(row) -> Order:
    return Order(
        id=row["id"],
        product_id=row["product_id"],
        order_type=OrderType(row["order_type"]),
        quantity=row["quantity"],
        unit_price=row["unit_price"],
        status=OrderStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_alert(row) -> Alert:
    return Alert(
        id=row["id"],
        product_id=row["product_id"],
        message=row["message"],
        quantity_at_alert=row["quantity_at_alert"],
        created_at=datetime.fromisoformat(row["created_at"]),
        acknowledged=bool(row["acknowledged"]),
    )
