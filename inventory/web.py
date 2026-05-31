from flask import Flask, render_template, redirect, url_for, request, flash, g, current_app
from pathlib import Path

from .db import get_connection, init_db, DEFAULT_DB_PATH
from . import service
from .models import OrderType, OrderStatus, Product


def get_db():
    """Return the per-request DB connection, opening one if needed."""
    if "db" not in g:
        path = current_app.config["DB_PATH"]
        g.db = get_connection(path)
        init_db(g.db)
    return g.db


def create_app(db_path: Path = None):
    app = Flask(__name__)
    app.secret_key = "inventory-tracker-dev"
    app.config["DB_PATH"] = db_path or DEFAULT_DB_PATH

    @app.teardown_appcontext
    def close_db(e=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    from .api import bp as api_bp
    app.register_blueprint(api_bp)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _try(fn, *args, **kwargs):
        """Call fn; on InventoryError flash the message and return None."""
        try:
            return fn(*args, **kwargs)
        except service.InventoryError as e:
            flash(str(e), "error")
            return None

    # ── pages ─────────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        s = service.summary(get_db())
        alerts = service.list_alerts(get_db(), unacknowledged_only=True)
        return render_template("index.html", summary=s, alerts=alerts)

    @app.route("/products")
    def products():
        products = service.list_products(get_db())
        return render_template("products.html", products=products)

    @app.route("/products/add", methods=["POST"])
    def product_add():
        try:
            p = Product(
                None,
                sku=request.form["sku"].strip(),
                name=request.form["name"].strip(),
                unit_price=float(request.form["price"]),
                reorder_threshold=int(request.form.get("threshold", 10)),
            )
            service.create_product(get_db(), p)
            flash(f"Product '{p.name}' created.", "success")
        except (service.InventoryError, ValueError) as e:
            flash(str(e), "error")
        return redirect(url_for("products"))

    @app.route("/products/<int:product_id>/delete", methods=["POST"])
    def product_delete(product_id):
        try:
            service.delete_product(get_db(), product_id)
            flash("Product deleted.", "success")
        except service.InventoryError as e:
            flash(str(e), "error")
        return redirect(url_for("products"))

    @app.route("/stock")
    def stock():
        rows = service.list_stock(get_db())
        products = service.list_products(get_db())
        return render_template("stock.html", rows=rows, products=products)

    @app.route("/stock/adjust", methods=["POST"])
    def stock_adjust():
        try:
            product_id = int(request.form["product_id"])
            delta = int(request.form["delta"])
            s = service.adjust_stock(get_db(), product_id, delta)
            sign = "+" if delta >= 0 else ""
            flash(f"Adjusted by {sign}{delta}. New quantity: {s.quantity}", "success")
        except (service.InventoryError, ValueError) as e:
            flash(str(e), "error")
        return redirect(url_for("stock"))

    @app.route("/stock/<int:product_id>/history")
    def stock_history(product_id):
        product = _try(service.get_product, get_db(), product_id)
        if product is None:
            return redirect(url_for("stock"))
        history = service.stock_history(get_db(), product_id)
        return render_template("stock_history.html", product=product, history=history)

    @app.route("/orders")
    def orders():
        from datetime import datetime
        status_val = request.args.get("status")
        since_str = request.args.get("since")
        until_str = request.args.get("until")
        status_filter = OrderStatus(status_val) if status_val else None
        since = datetime.strptime(since_str, "%Y-%m-%d") if since_str else None
        until = datetime.strptime(until_str, "%Y-%m-%d") if until_str else None
        order_list = service.list_orders(get_db(), status=status_filter, since=since, until=until)
        products = service.list_products(get_db())
        return render_template(
            "orders.html",
            orders=order_list,
            products=products,
            statuses=[s.value for s in OrderStatus],
            current_status=status_val or "",
            current_since=since_str or "",
            current_until=until_str or "",
        )

    @app.route("/orders/buy", methods=["POST"])
    def order_buy():
        try:
            product_id = int(request.form["product_id"])
            quantity = int(request.form["quantity"])
            price = float(request.form["price"]) if request.form.get("price") else None
            fulfill = "fulfill" in request.form
            o = service.create_order(get_db(), product_id, OrderType.PURCHASE, quantity, price)
            if fulfill:
                service.fulfill_order(get_db(), o.id)
                flash(f"Purchase order #{o.id} created and fulfilled.", "success")
            else:
                flash(f"Purchase order #{o.id} created (pending).", "success")
        except (service.InventoryError, ValueError) as e:
            flash(str(e), "error")
        return redirect(url_for("orders"))

    @app.route("/orders/sell", methods=["POST"])
    def order_sell():
        try:
            product_id = int(request.form["product_id"])
            quantity = int(request.form["quantity"])
            price = float(request.form["price"]) if request.form.get("price") else None
            fulfill = "fulfill" in request.form
            o = service.create_order(get_db(), product_id, OrderType.SALE, quantity, price)
            if fulfill:
                service.fulfill_order(get_db(), o.id)
                flash(f"Sale order #{o.id} created and fulfilled.", "success")
            else:
                flash(f"Sale order #{o.id} created (pending).", "success")
        except (service.InventoryError, ValueError) as e:
            flash(str(e), "error")
        return redirect(url_for("orders"))

    @app.route("/orders/<int:order_id>/fulfill", methods=["POST"])
    def order_fulfill(order_id):
        result = _try(service.fulfill_order, get_db(), order_id)
        if result is not None:
            flash(f"Order #{order_id} fulfilled.", "success")
        return redirect(url_for("orders"))

    @app.route("/orders/<int:order_id>/cancel", methods=["POST"])
    def order_cancel(order_id):
        result = _try(service.cancel_order, get_db(), order_id)
        if result is not None:
            flash(f"Order #{order_id} cancelled.", "success")
        return redirect(url_for("orders"))

    @app.route("/alerts")
    def alerts():
        show_all = request.args.get("all") == "1"
        alert_list = service.list_alerts(get_db(), unacknowledged_only=not show_all)
        return render_template("alerts.html", alerts=alert_list, show_all=show_all)

    @app.route("/alerts/<int:alert_id>/ack", methods=["POST"])
    def alert_ack(alert_id):
        _try(service.acknowledge_alert, get_db(), alert_id)
        return redirect(url_for("alerts"))

    @app.route("/alerts/ack-all", methods=["POST"])
    def alert_ack_all():
        count = service.acknowledge_all_alerts(get_db())
        flash(f"Acknowledged {count} alert(s).", "success")
        return redirect(url_for("alerts"))

    @app.route("/reorder")
    def reorder():
        rows = service.list_reorder(get_db())
        return render_template("reorder.html", rows=rows)

    return app
