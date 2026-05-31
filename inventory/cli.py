import click
import functools
from pathlib import Path

from .db import get_connection, init_db
from .models import OrderStatus, OrderType, Supplier
from . import service


def _conn(ctx):
    return ctx.obj["conn"]


def _bail(fn):
    """Catch InventoryError and turn it into a clean CLI message."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except service.InventoryError as e:
            raise click.ClickException(str(e))
    return wrapper


@click.group()
@click.option("--db", default=None, help="Path to SQLite database file")
@click.pass_context
def cli(ctx, db):
    db_path = Path(db) if db else None
    conn = get_connection(db_path) if db_path else get_connection()
    init_db(conn)
    ctx.ensure_object(dict)
    ctx.obj["conn"] = conn


# ── summary ──────────────────────────────────────────────────────────────────

@cli.command("summary")
@click.pass_context
def summary(ctx):
    """Show inventory dashboard."""
    s = service.summary(_conn(ctx))
    click.echo(f"{'Products:':<26}{s['products']}")
    click.echo(f"{'Total stock value:':<26}${s['stock_value']:,.2f}")
    click.echo(f"{'Pending orders:':<26}{s['pending_orders']}")
    click.echo(f"{'Low-stock products:':<26}{s['low_stock_products']}")
    click.echo(f"{'Unacknowledged alerts:':<26}{s['unacknowledged_alerts']}")


# ── reorder ───────────────────────────────────────────────────────────────────

@cli.command("reorder")
@click.pass_context
def reorder(ctx):
    """List products that need restocking, sorted by largest shortfall."""
    rows = service.list_reorder(_conn(ctx))
    if not rows:
        click.echo("All products are above reorder thresholds.")
        return
    click.echo(f"{'ID':<5} {'SKU':<15} {'Name':<25} {'Qty':>6} {'Threshold':>10} {'Shortfall':>10}")
    click.echo("-" * 75)
    for r in rows:
        click.echo(
            f"{r['id']:<5} {r['sku']:<15} {r['name']:<25} "
            f"{r['quantity']:>6} {r['reorder_threshold']:>10} {r['shortfall']:>10}"
        )


# ── products ────────────────────────────────────────────────────────────────

@cli.group()
def product():
    """Manage products."""


@product.command("add")
@click.option("--sku", required=True)
@click.option("--name", required=True)
@click.option("--price", required=True, type=float)
@click.option("--threshold", default=10, show_default=True, type=int)
@click.option("--supplier-id", type=int, default=None, help="Link to supplier by ID")
@click.pass_context
@_bail
def product_add(ctx, sku, name, price, threshold, supplier_id):
    from .models import Product
    p = service.create_product(_conn(ctx), Product(None, sku, name, price, threshold, supplier_id=supplier_id))
    supplier_info = f", supplier: #{p.supplier_id}" if p.supplier_id else ""
    click.echo(f"Created product #{p.id}: {p.name} (SKU: {p.sku}, price: ${p.unit_price:.2f}{supplier_info})")


@product.command("list")
@click.pass_context
def product_list(ctx):
    products = service.list_products(_conn(ctx))
    if not products:
        click.echo("No products.")
        return
    click.echo(f"{'ID':<5} {'SKU':<15} {'Name':<25} {'Price':>8} {'Threshold':>10}")
    click.echo("-" * 65)
    for p in products:
        click.echo(f"{p.id:<5} {p.sku:<15} {p.name:<25} {p.unit_price:>8.2f} {p.reorder_threshold:>10}")


@product.command("update")
@click.argument("product_id", type=int)
@click.option("--sku")
@click.option("--name")
@click.option("--price", type=float)
@click.option("--threshold", type=int)
@click.option("--supplier-id", type=int, default=None, help="Link to supplier by ID (0 to unlink)")
@click.pass_context
@_bail
def product_update(ctx, product_id, sku, name, price, threshold, supplier_id):
    p = service.get_product(_conn(ctx), product_id)
    p.sku = sku or p.sku
    p.name = name or p.name
    p.unit_price = price if price is not None else p.unit_price
    p.reorder_threshold = threshold if threshold is not None else p.reorder_threshold
    if supplier_id is not None:
        p.supplier_id = None if supplier_id == 0 else supplier_id
    updated = service.update_product(_conn(ctx), p)
    click.echo(f"Updated product #{updated.id}: {updated.name}")


@product.command("delete")
@click.argument("product_id", type=int)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
@_bail
def product_delete(ctx, product_id, yes):
    """Delete a product (blocked if it has orders)."""
    p = service.get_product(_conn(ctx), product_id)
    if not yes:
        click.confirm(f"Delete '{p.name}' (#{p.id})?", abort=True)
    service.delete_product(_conn(ctx), product_id)
    click.echo(f"Deleted product #{p.id}: {p.name}")


# ── suppliers ────────────────────────────────────────────────────────────────

@cli.group()
def supplier():
    """Manage suppliers."""


@supplier.command("add")
@click.option("--name", required=True)
@click.option("--contact", default=None)
@click.option("--email", default=None)
@click.option("--phone", default=None)
@click.option("--lead-time", default=0, show_default=True, type=int, help="Lead time in days")
@click.pass_context
@_bail
def supplier_add(ctx, name, contact, email, phone, lead_time):
    """Add a new supplier."""
    s = service.create_supplier(_conn(ctx), Supplier(None, name, contact, email, phone, lead_time))
    click.echo(f"Created supplier #{s.id}: {s.name} (lead time: {s.lead_time_days}d)")


@supplier.command("list")
@click.pass_context
def supplier_list(ctx):
    """List all suppliers."""
    suppliers = service.list_suppliers(_conn(ctx))
    if not suppliers:
        click.echo("No suppliers.")
        return
    click.echo(f"{'ID':<5} {'Name':<25} {'Contact':<20} {'Email':<25} {'Lead (d)':>8}")
    click.echo("-" * 85)
    for s in suppliers:
        click.echo(
            f"{s.id:<5} {s.name:<25} {(s.contact_name or ''):<20} "
            f"{(s.email or ''):<25} {s.lead_time_days:>8}"
        )


@supplier.command("show")
@click.argument("supplier_id", type=int)
@click.pass_context
@_bail
def supplier_show(ctx, supplier_id):
    """Show supplier details."""
    s = service.get_supplier(_conn(ctx), supplier_id)
    click.echo(f"ID:           {s.id}")
    click.echo(f"Name:         {s.name}")
    click.echo(f"Contact:      {s.contact_name or '—'}")
    click.echo(f"Email:        {s.email or '—'}")
    click.echo(f"Phone:        {s.phone or '—'}")
    click.echo(f"Lead time:    {s.lead_time_days} day(s)")


@supplier.command("update")
@click.argument("supplier_id", type=int)
@click.option("--name")
@click.option("--contact")
@click.option("--email")
@click.option("--phone")
@click.option("--lead-time", type=int)
@click.pass_context
@_bail
def supplier_update(ctx, supplier_id, name, contact, email, phone, lead_time):
    """Update supplier details."""
    s = service.get_supplier(_conn(ctx), supplier_id)
    s.name = name or s.name
    s.contact_name = contact if contact is not None else s.contact_name
    s.email = email if email is not None else s.email
    s.phone = phone if phone is not None else s.phone
    s.lead_time_days = lead_time if lead_time is not None else s.lead_time_days
    updated = service.update_supplier(_conn(ctx), s)
    click.echo(f"Updated supplier #{updated.id}: {updated.name}")


@supplier.command("delete")
@click.argument("supplier_id", type=int)
@click.option("--yes", "-y", is_flag=True)
@click.pass_context
@_bail
def supplier_delete(ctx, supplier_id, yes):
    """Delete a supplier (blocked if products still reference it)."""
    s = service.get_supplier(_conn(ctx), supplier_id)
    if not yes:
        click.confirm(f"Delete supplier '{s.name}' (#{s.id})?", abort=True)
    service.delete_supplier(_conn(ctx), supplier_id)
    click.echo(f"Deleted supplier #{s.id}: {s.name}")


# ── stock ────────────────────────────────────────────────────────────────────

@cli.group()
def stock():
    """Manage stock levels."""


@stock.command("list")
@click.pass_context
def stock_list(ctx):
    """Show current stock levels."""
    rows = service.list_stock(_conn(ctx))
    if not rows:
        click.echo("No stock data.")
        return
    click.echo(f"{'ID':<5} {'SKU':<15} {'Name':<25} {'Qty':>6} {'Threshold':>10} {'Status':<10}")
    click.echo("-" * 75)
    for r in rows:
        status = "LOW" if r["quantity"] <= r["reorder_threshold"] else "OK"
        click.echo(
            f"{r['id']:<5} {r['sku']:<15} {r['name']:<25} "
            f"{r['quantity']:>6} {r['reorder_threshold']:>10} {status:<10}"
        )


@stock.command("adjust")
@click.argument("product_id", type=int)
@click.argument("delta", type=int)
@click.pass_context
@_bail
def stock_adjust(ctx, product_id, delta):
    """Apply a direct stock correction (e.g. -3 for shrinkage, +5 for a count correction)."""
    s = service.adjust_stock(_conn(ctx), product_id, delta)
    sign = "+" if delta >= 0 else ""
    click.echo(f"Stock adjusted by {sign}{delta}. New quantity: {s.quantity}")


@stock.command("history")
@click.argument("product_id", type=int)
@click.pass_context
@_bail
def stock_history(ctx, product_id):
    """Show fulfilled order history with running balance for a product."""
    p = service.get_product(_conn(ctx), product_id)
    history = service.stock_history(_conn(ctx), product_id)
    if not history:
        click.echo(f"No fulfilled orders for {p.name}.")
        return
    click.echo(f"History for {p.name} (SKU: {p.sku})")
    click.echo(f"{'Order':<7} {'Type':<10} {'Qty':>6} {'Delta':>7} {'Balance':>8}  Date")
    click.echo("-" * 55)
    for h in history:
        sign = "+" if h["delta"] >= 0 else ""
        click.echo(
            f"{h['order_id']:<7} {h['order_type']:<10} {h['quantity']:>6} "
            f"{sign}{h['delta']:>6} {h['balance']:>8}  "
            f"{h['created_at'].strftime('%Y-%m-%d %H:%M')}"
        )


# ── orders ───────────────────────────────────────────────────────────────────

@cli.group()
def order():
    """Manage purchase and sale orders."""


@order.command("buy")
@click.argument("product_id", type=int)
@click.argument("quantity", type=int)
@click.option("--price", type=float, help="Override unit price")
@click.option("--fulfill", is_flag=True, help="Fulfill immediately after creating")
@click.pass_context
@_bail
def order_buy(ctx, product_id, quantity, price, fulfill):
    """Create a purchase order (stock in)."""
    o = service.create_order(_conn(ctx), product_id, OrderType.PURCHASE, quantity, price)
    click.echo(f"Purchase order #{o.id} created: {quantity} units @ ${o.unit_price:.2f} = ${o.total:.2f}")
    if fulfill:
        service.fulfill_order(_conn(ctx), o.id)
        click.echo(f"Order #{o.id} fulfilled.")


@order.command("sell")
@click.argument("product_id", type=int)
@click.argument("quantity", type=int)
@click.option("--price", type=float, help="Override unit price")
@click.option("--fulfill", is_flag=True, help="Fulfill immediately after creating")
@click.pass_context
@_bail
def order_sell(ctx, product_id, quantity, price, fulfill):
    """Create a sale order (stock out)."""
    o = service.create_order(_conn(ctx), product_id, OrderType.SALE, quantity, price)
    click.echo(f"Sale order #{o.id} created: {quantity} units @ ${o.unit_price:.2f} = ${o.total:.2f}")
    if fulfill:
        service.fulfill_order(_conn(ctx), o.id)
        click.echo(f"Order #{o.id} fulfilled.")


@order.command("fulfill")
@click.argument("order_id", type=int)
@click.pass_context
@_bail
def order_fulfill(ctx, order_id):
    """Fulfill a pending order (applies stock change)."""
    o = service.fulfill_order(_conn(ctx), order_id)
    click.echo(f"Order #{o.id} fulfilled.")


@order.command("cancel")
@click.argument("order_id", type=int)
@click.pass_context
@_bail
def order_cancel(ctx, order_id):
    """Cancel an order (reverses stock if fulfilled)."""
    o = service.cancel_order(_conn(ctx), order_id)
    click.echo(f"Order #{o.id} cancelled.")


@order.command("list")
@click.option("--product", "product_id", type=int)
@click.option("--status", type=click.Choice([s.value for s in OrderStatus]))
@click.option("--since", type=click.DateTime(formats=["%Y-%m-%d"]), metavar="YYYY-MM-DD")
@click.option("--until", type=click.DateTime(formats=["%Y-%m-%d"]), metavar="YYYY-MM-DD")
@click.pass_context
def order_list(ctx, product_id, status, since, until):
    status_filter = OrderStatus(status) if status else None
    orders = service.list_orders(_conn(ctx), product_id, status_filter, since, until)
    if not orders:
        click.echo("No orders.")
        return
    click.echo(f"{'ID':<5} {'Product':<20} {'Type':<10} {'Qty':>5} {'Price':>8} {'Total':>8} {'Status':<12} Created")
    click.echo("-" * 90)
    for o in orders:
        product_label = o.product_name or str(o.product_id)
        click.echo(
            f"{o.id:<5} {product_label:<20} {o.order_type.value:<10} {o.quantity:>5} "
            f"{o.unit_price:>8.2f} {o.total:>8.2f} {o.status.value:<12} "
            f"{o.created_at.strftime('%Y-%m-%d %H:%M')}"
        )


# ── alerts ───────────────────────────────────────────────────────────────────

@cli.group()
def alert():
    """Manage low-stock alerts."""


@alert.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include acknowledged alerts")
@click.pass_context
def alert_list(ctx, show_all):
    alerts = service.list_alerts(_conn(ctx), unacknowledged_only=not show_all)
    if not alerts:
        click.echo("No alerts.")
        return
    for a in alerts:
        ack = "[ACK]" if a.acknowledged else "[NEW]"
        click.echo(f"#{a.id} {ack} {a.created_at.strftime('%Y-%m-%d %H:%M')} — {a.message}")


@alert.command("ack")
@click.argument("alert_id", type=int)
@click.pass_context
@_bail
def alert_ack(ctx, alert_id):
    """Acknowledge an alert."""
    a = service.acknowledge_alert(_conn(ctx), alert_id)
    click.echo(f"Alert #{a.id} acknowledged.")


@alert.command("ack-all")
@click.pass_context
def alert_ack_all(ctx):
    """Acknowledge all pending alerts."""
    count = service.acknowledge_all_alerts(_conn(ctx))
    if count == 0:
        click.echo("No pending alerts.")
    else:
        click.echo(f"Acknowledged {count} alert(s).")


# ── import ───────────────────────────────────────────────────────────────────

@cli.group("import")
def import_cmd():
    """Import data from CSV files."""


@import_cmd.command("products")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
@_bail
def import_products(ctx, file):
    """Import products (and optional opening stock) from a CSV file."""
    with open(file, newline="", encoding="utf-8") as f:
        result = service.import_products_csv(_conn(ctx), f)
    click.echo(f"Import complete: {result['created']} created, {result['skipped']} skipped.")
    for err in result["errors"]:
        click.echo(f"  Row {err['row']}: {err['reason']}", err=True)


# ── export ───────────────────────────────────────────────────────────────────

@cli.group()
def export():
    """Export data to CSV files."""


@export.command("stock")
@click.argument("file", type=click.Path(dir_okay=False))
@click.pass_context
def export_stock(ctx, file):
    """Export current stock levels to a CSV file."""
    with open(file, "w", newline="", encoding="utf-8") as f:
        service.export_stock_csv(_conn(ctx), f)
    click.echo(f"Stock levels exported to {file}")


@export.command("orders")
@click.argument("file", type=click.Path(dir_okay=False))
@click.option("--product", "product_id", type=int)
@click.option("--status", type=click.Choice([s.value for s in OrderStatus]))
@click.pass_context
def export_orders(ctx, file, product_id, status):
    """Export orders to a CSV file."""
    status_filter = OrderStatus(status) if status else None
    with open(file, "w", newline="", encoding="utf-8") as f:
        service.export_orders_csv(_conn(ctx), f, product_id=product_id, status=status_filter)
    click.echo(f"Orders exported to {file}")


@cli.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=5000, show_default=True, type=int)
@click.option("--debug", is_flag=True)
@click.option("--db", "db_path", default=None, help="Path to SQLite database file")
def serve(host, port, debug, db_path):
    """Start the web UI."""
    from .web import create_app
    from pathlib import Path
    app = create_app(Path(db_path) if db_path else None)
    click.echo(f"Web UI running at http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)


def main():
    cli()
