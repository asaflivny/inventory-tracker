from datetime import datetime

from flask import Blueprint, jsonify, request

from . import service
from .models import OrderStatus, OrderType, Product
from .web import get_db

bp = Blueprint("api", __name__, url_prefix="/api/v1")


# ── serializers ───────────────────────────────────────────────────────────────

def _product(p):
    return {
        "id": p.id, "sku": p.sku, "name": p.name,
        "unit_price": p.unit_price, "reorder_threshold": p.reorder_threshold,
    }


def _order(o):
    return {
        "id": o.id, "product_id": o.product_id,
        "product_name": o.product_name, "product_sku": o.product_sku,
        "order_type": o.order_type.value, "quantity": o.quantity,
        "unit_price": o.unit_price, "total": o.total,
        "status": o.status.value, "created_at": o.created_at.isoformat(),
    }


def _alert(a):
    return {
        "id": a.id, "product_id": a.product_id, "message": a.message,
        "quantity_at_alert": a.quantity_at_alert,
        "created_at": a.created_at.isoformat(), "acknowledged": a.acknowledged,
    }


def _stock_entry(h):
    return {**h, "created_at": h["created_at"].isoformat()}


def _err(msg, status=400):
    return jsonify({"error": msg}), status


# ── products ──────────────────────────────────────────────────────────────────

@bp.route("/products")
def products_list():
    return jsonify([_product(p) for p in service.list_products(get_db())])


@bp.route("/products", methods=["POST"])
def products_create():
    data = request.get_json(force=True) or {}
    try:
        p = Product(
            None,
            sku=data["sku"],
            name=data["name"],
            unit_price=float(data["unit_price"]),
            reorder_threshold=int(data.get("reorder_threshold", 10)),
        )
        return jsonify(_product(service.create_product(get_db(), p))), 201
    except KeyError as e:
        return _err(f"Missing field: {e.args[0]}")
    except (ValueError, service.InventoryError) as e:
        return _err(str(e))


@bp.route("/products/<int:product_id>")
def products_get(product_id):
    try:
        return jsonify(_product(service.get_product(get_db(), product_id)))
    except service.ProductNotFound as e:
        return _err(str(e), 404)


@bp.route("/products/<int:product_id>", methods=["PATCH"])
def products_update(product_id):
    try:
        p = service.get_product(get_db(), product_id)
        data = request.get_json(force=True) or {}
        p.sku = data.get("sku", p.sku)
        p.name = data.get("name", p.name)
        if "unit_price" in data:
            p.unit_price = float(data["unit_price"])
        if "reorder_threshold" in data:
            p.reorder_threshold = int(data["reorder_threshold"])
        return jsonify(_product(service.update_product(get_db(), p)))
    except service.ProductNotFound as e:
        return _err(str(e), 404)
    except (ValueError, service.InventoryError) as e:
        return _err(str(e))


@bp.route("/products/<int:product_id>", methods=["DELETE"])
def products_delete(product_id):
    try:
        service.delete_product(get_db(), product_id)
        return "", 204
    except service.ProductNotFound as e:
        return _err(str(e), 404)
    except service.InventoryError as e:
        return _err(str(e), 409)


# ── stock ─────────────────────────────────────────────────────────────────────

@bp.route("/stock")
def stock_list():
    return jsonify(service.list_stock(get_db()))


@bp.route("/stock/<int:product_id>")
def stock_get(product_id):
    try:
        s = service.get_stock(get_db(), product_id)
        return jsonify({"product_id": s.product_id, "quantity": s.quantity})
    except service.ProductNotFound as e:
        return _err(str(e), 404)


@bp.route("/stock/<int:product_id>/adjust", methods=["POST"])
def stock_adjust(product_id):
    data = request.get_json(force=True) or {}
    try:
        delta = int(data["delta"])
        s = service.adjust_stock(get_db(), product_id, delta)
        return jsonify({"product_id": s.product_id, "quantity": s.quantity})
    except KeyError:
        return _err("Missing field: delta")
    except service.ProductNotFound as e:
        return _err(str(e), 404)
    except service.InventoryError as e:
        return _err(str(e), 409)


@bp.route("/stock/<int:product_id>/history")
def stock_history(product_id):
    try:
        service.get_product(get_db(), product_id)
        return jsonify([_stock_entry(h) for h in service.stock_history(get_db(), product_id)])
    except service.ProductNotFound as e:
        return _err(str(e), 404)


# ── orders ────────────────────────────────────────────────────────────────────

@bp.route("/orders")
def orders_list():
    status_val = request.args.get("status")
    since_str = request.args.get("since")
    until_str = request.args.get("until")
    product_id = request.args.get("product_id", type=int)
    try:
        status_filter = OrderStatus(status_val) if status_val else None
    except ValueError:
        return _err(f"Invalid status '{status_val}'")
    since = datetime.strptime(since_str, "%Y-%m-%d") if since_str else None
    until = datetime.strptime(until_str, "%Y-%m-%d") if until_str else None
    return jsonify([_order(o) for o in service.list_orders(get_db(), product_id, status_filter, since, until)])


@bp.route("/orders/buy", methods=["POST"])
def orders_buy():
    data = request.get_json(force=True) or {}
    try:
        o = service.create_order(
            get_db(), int(data["product_id"]), OrderType.PURCHASE,
            int(data["quantity"]),
            float(data["unit_price"]) if "unit_price" in data else None,
        )
        if data.get("fulfill"):
            o = service.fulfill_order(get_db(), o.id)
        return jsonify(_order(o)), 201
    except KeyError as e:
        return _err(f"Missing field: {e.args[0]}")
    except service.ProductNotFound as e:
        return _err(str(e), 404)
    except service.InventoryError as e:
        return _err(str(e), 409)


@bp.route("/orders/sell", methods=["POST"])
def orders_sell():
    data = request.get_json(force=True) or {}
    try:
        o = service.create_order(
            get_db(), int(data["product_id"]), OrderType.SALE,
            int(data["quantity"]),
            float(data["unit_price"]) if "unit_price" in data else None,
        )
        if data.get("fulfill"):
            o = service.fulfill_order(get_db(), o.id)
        return jsonify(_order(o)), 201
    except KeyError as e:
        return _err(f"Missing field: {e.args[0]}")
    except service.ProductNotFound as e:
        return _err(str(e), 404)
    except service.InventoryError as e:
        return _err(str(e), 409)


@bp.route("/orders/<int:order_id>/fulfill", methods=["POST"])
def orders_fulfill(order_id):
    try:
        return jsonify(_order(service.fulfill_order(get_db(), order_id)))
    except service.InventoryError as e:
        return _err(str(e), 409)


@bp.route("/orders/<int:order_id>/cancel", methods=["POST"])
def orders_cancel(order_id):
    try:
        return jsonify(_order(service.cancel_order(get_db(), order_id)))
    except service.InventoryError as e:
        return _err(str(e), 409)


# ── alerts ────────────────────────────────────────────────────────────────────

@bp.route("/alerts")
def alerts_list():
    unacked_only = request.args.get("unacked") == "1"
    return jsonify([_alert(a) for a in service.list_alerts(get_db(), unacknowledged_only=unacked_only)])


@bp.route("/alerts/<int:alert_id>/ack", methods=["POST"])
def alerts_ack(alert_id):
    try:
        return jsonify(_alert(service.acknowledge_alert(get_db(), alert_id)))
    except service.InventoryError as e:
        return _err(str(e), 404)


@bp.route("/alerts/ack-all", methods=["POST"])
def alerts_ack_all():
    return jsonify({"acknowledged": service.acknowledge_all_alerts(get_db())})


# ── summary & reorder ─────────────────────────────────────────────────────────

@bp.route("/summary")
def summary():
    return jsonify(service.summary(get_db()))


@bp.route("/reorder")
def reorder():
    return jsonify(service.list_reorder(get_db()))
