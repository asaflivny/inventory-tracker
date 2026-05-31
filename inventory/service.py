import csv
import io
import sqlite3
from datetime import datetime
from typing import IO, Optional, Union

from .db import transaction
from .models import Alert, Order, OrderStatus, OrderType, Product, StockLevel, Supplier


class InventoryError(Exception):
    pass


class ProductNotFound(InventoryError):
    pass


class SupplierNotFound(InventoryError):
    pass


class InsufficientStock(InventoryError):
    pass


class InvalidStatusTransition(InventoryError):
    pass


# ---------- products ----------

def create_product(conn: sqlite3.Connection, product: Product) -> Product:
    if product.supplier_id is not None:
        _require_supplier(conn, product.supplier_id)
    try:
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO products (sku, name, unit_price, reorder_threshold, supplier_id) VALUES (?, ?, ?, ?, ?)",
                (product.sku, product.name, product.unit_price, product.reorder_threshold, product.supplier_id),
            )
            product_id = cur.lastrowid
            conn.execute(
                "INSERT INTO stock_levels (product_id, quantity) VALUES (?, 0)",
                (product_id,),
            )
    except sqlite3.IntegrityError:
        raise InventoryError(f"A product with SKU '{product.sku}' already exists")
    return _get_product_by_id(conn, product_id)


def get_product(conn: sqlite3.Connection, product_id: int) -> Product:
    return _get_product_by_id(conn, product_id)


def list_products(conn: sqlite3.Connection) -> list[Product]:
    rows = conn.execute("SELECT * FROM products ORDER BY sku").fetchall()
    return [_row_to_product(r) for r in rows]


def update_product(conn: sqlite3.Connection, product: Product) -> Product:
    _require_product(conn, product.id)
    if product.supplier_id is not None:
        _require_supplier(conn, product.supplier_id)
    with transaction(conn):
        conn.execute(
            "UPDATE products SET sku=?, name=?, unit_price=?, reorder_threshold=?, supplier_id=? WHERE id=?",
            (product.sku, product.name, product.unit_price, product.reorder_threshold, product.supplier_id, product.id),
        )
    return _get_product_by_id(conn, product.id)


def delete_product(conn: sqlite3.Connection, product_id: int) -> None:
    _require_product(conn, product_id)
    order_count = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE product_id = ?", (product_id,)
    ).fetchone()[0]
    if order_count > 0:
        raise InventoryError(
            f"Cannot delete product {product_id}: it has {order_count} associated order(s)"
        )
    with transaction(conn):
        conn.execute("DELETE FROM alerts WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM stock_levels WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))


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


def adjust_stock(conn: sqlite3.Connection, product_id: int, delta: int) -> StockLevel:
    """Apply a direct stock correction. Positive delta adds units, negative removes."""
    _require_product(conn, product_id)
    stock = get_stock(conn, product_id)
    new_qty = stock.quantity + delta
    if new_qty < 0:
        raise InsufficientStock(
            f"Adjustment of {delta:+d} would bring stock to {new_qty}"
        )
    with transaction(conn):
        conn.execute(
            "UPDATE stock_levels SET quantity = ? WHERE product_id = ?",
            (new_qty, product_id),
        )
        _insert_alert_if_low(conn, product_id, new_qty)
    return StockLevel(product_id=product_id, quantity=new_qty)


def stock_history(conn: sqlite3.Connection, product_id: int) -> list[dict]:
    """Return fulfilled orders for a product with a running stock balance."""
    _require_product(conn, product_id)
    rows = conn.execute("""
        SELECT id, order_type, quantity, unit_price, created_at
        FROM orders
        WHERE product_id = ? AND status = 'fulfilled'
        ORDER BY created_at ASC
    """, (product_id,)).fetchall()
    history = []
    balance = 0
    for r in rows:
        delta = r["quantity"] if r["order_type"] == "purchase" else -r["quantity"]
        balance += delta
        history.append({
            "order_id": r["id"],
            "order_type": r["order_type"],
            "quantity": r["quantity"],
            "delta": delta,
            "balance": balance,
            "unit_price": r["unit_price"],
            "created_at": datetime.fromisoformat(r["created_at"]),
        })
    return history


def list_reorder(conn: sqlite3.Connection) -> list[dict]:
    """Return products whose stock is at or below their reorder threshold."""
    rows = conn.execute("""
        SELECT p.id, p.sku, p.name, p.reorder_threshold, s.quantity,
               (p.reorder_threshold - s.quantity) AS shortfall
        FROM products p
        JOIN stock_levels s ON p.id = s.product_id
        WHERE s.quantity <= p.reorder_threshold
        ORDER BY shortfall DESC, p.sku
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
                status: Optional[OrderStatus] = None,
                since: Optional[datetime] = None,
                until: Optional[datetime] = None) -> list[Order]:
    query = """
        SELECT o.*, p.name AS product_name, p.sku AS product_sku
        FROM orders o
        JOIN products p ON p.id = o.product_id
        WHERE 1=1
    """
    params: list = []
    if product_id is not None:
        query += " AND o.product_id = ?"
        params.append(product_id)
    if status is not None:
        query += " AND o.status = ?"
        params.append(status.value)
    if since is not None:
        query += " AND o.created_at >= ?"
        params.append(since.strftime("%Y-%m-%dT00:00:00"))
    if until is not None:
        query += " AND o.created_at <= ?"
        params.append(until.strftime("%Y-%m-%dT23:59:59"))
    query += " ORDER BY o.created_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_order(r) for r in rows]


# ---------- summary ----------

def summary(conn: sqlite3.Connection) -> dict:
    product_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    stock_value = conn.execute("""
        SELECT COALESCE(SUM(s.quantity * p.unit_price), 0)
        FROM stock_levels s JOIN products p ON p.id = s.product_id
    """).fetchone()[0]
    pending_orders = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE status = ?", (OrderStatus.PENDING.value,)
    ).fetchone()[0]
    low_stock = conn.execute("""
        SELECT COUNT(*) FROM stock_levels s
        JOIN products p ON p.id = s.product_id
        WHERE s.quantity <= p.reorder_threshold
    """).fetchone()[0]
    unacked_alerts = conn.execute(
        "SELECT COUNT(*) FROM alerts WHERE acknowledged = 0"
    ).fetchone()[0]
    return {
        "products": product_count,
        "stock_value": stock_value,
        "pending_orders": pending_orders,
        "low_stock_products": low_stock,
        "unacknowledged_alerts": unacked_alerts,
    }


# ---------- alerts ----------

def list_alerts(conn: sqlite3.Connection, unacknowledged_only: bool = False) -> list[Alert]:
    query = "SELECT * FROM alerts"
    if unacknowledged_only:
        query += " WHERE acknowledged = 0"
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query).fetchall()
    return [_row_to_alert(r) for r in rows]


def acknowledge_all_alerts(conn: sqlite3.Connection) -> int:
    rows = conn.execute("SELECT id FROM alerts WHERE acknowledged = 0").fetchall()
    if not rows:
        return 0
    with transaction(conn):
        conn.execute("UPDATE alerts SET acknowledged = 1 WHERE acknowledged = 0")
    return len(rows)


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
    keys = row.keys()
    return Product(
        id=row["id"],
        sku=row["sku"],
        name=row["name"],
        unit_price=row["unit_price"],
        reorder_threshold=row["reorder_threshold"],
        supplier_id=row["supplier_id"] if "supplier_id" in keys else None,
    )


def _row_to_order(row) -> Order:
    keys = row.keys()
    return Order(
        id=row["id"],
        product_id=row["product_id"],
        order_type=OrderType(row["order_type"]),
        quantity=row["quantity"],
        unit_price=row["unit_price"],
        status=OrderStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        product_name=row["product_name"] if "product_name" in keys else None,
        product_sku=row["product_sku"] if "product_sku" in keys else None,
    )


# ---------- bulk import / export ----------

_IMPORT_REQUIRED = {"sku", "name", "unit_price"}
_IMPORT_OPTIONAL = {"reorder_threshold", "supplier_id", "opening_stock"}


def import_products_csv(conn: sqlite3.Connection, fileobj: IO[str]) -> dict:
    """
    Import products (and optional opening stock) from a CSV file-like object.

    Required columns: sku, name, unit_price
    Optional columns: reorder_threshold, supplier_id, opening_stock

    Returns a summary dict: {created, skipped, errors: [{row, reason}]}
    """
    reader = csv.DictReader(fileobj)
    if not reader.fieldnames:
        raise InventoryError("CSV file is empty or has no header row")

    missing = _IMPORT_REQUIRED - {f.strip().lower() for f in reader.fieldnames}
    if missing:
        raise InventoryError(f"CSV missing required columns: {', '.join(sorted(missing))}")

    created = 0
    skipped = 0
    errors: list[dict] = []

    for i, row in enumerate(reader, start=2):  # row 1 is header
        row = {k.strip().lower(): v.strip() for k, v in row.items() if k}
        try:
            supplier_id = int(row["supplier_id"]) if row.get("supplier_id") else None
            threshold = int(row["reorder_threshold"]) if row.get("reorder_threshold") else 10
            opening = int(row["opening_stock"]) if row.get("opening_stock") else 0
            if opening < 0:
                raise ValueError("opening_stock must be non-negative")

            product = Product(
                id=None,
                sku=row["sku"],
                name=row["name"],
                unit_price=float(row["unit_price"]),
                reorder_threshold=threshold,
                supplier_id=supplier_id,
            )
            p = create_product(conn, product)
            created += 1

            if opening > 0:
                order = create_order(conn, p.id, OrderType.PURCHASE, opening, p.unit_price)
                fulfill_order(conn, order.id)

        except InventoryError as e:
            if "already exists" in str(e):
                skipped += 1
            else:
                errors.append({"row": i, "reason": str(e)})
        except (ValueError, KeyError) as e:
            errors.append({"row": i, "reason": str(e)})

    return {"created": created, "skipped": skipped, "errors": errors}


def export_stock_csv(conn: sqlite3.Connection, fileobj: IO[str]) -> None:
    """Write current stock levels to fileobj as CSV."""
    rows = list_stock(conn)
    writer = csv.DictWriter(
        fileobj,
        fieldnames=["id", "sku", "name", "quantity", "reorder_threshold", "status"],
        lineterminator="\n",
    )
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "id": r["id"],
            "sku": r["sku"],
            "name": r["name"],
            "quantity": r["quantity"],
            "reorder_threshold": r["reorder_threshold"],
            "status": "LOW" if r["quantity"] <= r["reorder_threshold"] else "OK",
        })


def export_orders_csv(conn: sqlite3.Connection, fileobj: IO[str],
                      product_id: Optional[int] = None,
                      status: Optional[OrderStatus] = None) -> None:
    """Write orders to fileobj as CSV. Supports same filters as list_orders."""
    orders = list_orders(conn, product_id=product_id, status=status)
    writer = csv.DictWriter(
        fileobj,
        fieldnames=["id", "product_id", "product_sku", "product_name", "order_type",
                    "quantity", "unit_price", "total", "status", "created_at"],
        lineterminator="\n",
    )
    writer.writeheader()
    for o in orders:
        writer.writerow({
            "id": o.id,
            "product_id": o.product_id,
            "product_sku": o.product_sku or "",
            "product_name": o.product_name or "",
            "order_type": o.order_type.value,
            "quantity": o.quantity,
            "unit_price": o.unit_price,
            "total": o.total,
            "status": o.status.value,
            "created_at": o.created_at.isoformat(),
        })


# ---------- suppliers ----------

def create_supplier(conn: sqlite3.Connection, supplier: Supplier) -> Supplier:
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO suppliers (name, contact_name, email, phone, lead_time_days) VALUES (?, ?, ?, ?, ?)",
            (supplier.name, supplier.contact_name, supplier.email, supplier.phone, supplier.lead_time_days),
        )
    return _get_supplier_by_id(conn, cur.lastrowid)


def get_supplier(conn: sqlite3.Connection, supplier_id: int) -> Supplier:
    return _require_supplier(conn, supplier_id)


def list_suppliers(conn: sqlite3.Connection) -> list[Supplier]:
    rows = conn.execute("SELECT * FROM suppliers ORDER BY name").fetchall()
    return [_row_to_supplier(r) for r in rows]


def update_supplier(conn: sqlite3.Connection, supplier: Supplier) -> Supplier:
    _require_supplier(conn, supplier.id)
    with transaction(conn):
        conn.execute(
            "UPDATE suppliers SET name=?, contact_name=?, email=?, phone=?, lead_time_days=? WHERE id=?",
            (supplier.name, supplier.contact_name, supplier.email, supplier.phone, supplier.lead_time_days, supplier.id),
        )
    return _get_supplier_by_id(conn, supplier.id)


def delete_supplier(conn: sqlite3.Connection, supplier_id: int) -> None:
    _require_supplier(conn, supplier_id)
    product_count = conn.execute(
        "SELECT COUNT(*) FROM products WHERE supplier_id = ?", (supplier_id,)
    ).fetchone()[0]
    if product_count > 0:
        raise InventoryError(
            f"Cannot delete supplier {supplier_id}: {product_count} product(s) still reference it"
        )
    with transaction(conn):
        conn.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))


def _require_supplier(conn: sqlite3.Connection, supplier_id: int) -> Supplier:
    row = conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
    if not row:
        raise SupplierNotFound(f"Supplier {supplier_id} not found")
    return _row_to_supplier(row)


def _get_supplier_by_id(conn: sqlite3.Connection, supplier_id: int) -> Supplier:
    return _require_supplier(conn, supplier_id)


def _row_to_supplier(row) -> Supplier:
    return Supplier(
        id=row["id"],
        name=row["name"],
        contact_name=row["contact_name"],
        email=row["email"],
        phone=row["phone"],
        lead_time_days=row["lead_time_days"],
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
