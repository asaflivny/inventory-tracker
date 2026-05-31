import click
from pathlib import Path

from .db import get_connection, init_db
from .models import OrderStatus, OrderType
from . import service


def _conn(ctx):
    return ctx.obj["conn"]


@click.group()
@click.option("--db", default=None, help="Path to SQLite database file")
@click.pass_context
def cli(ctx, db):
    db_path = Path(db) if db else None
    conn = get_connection(db_path) if db_path else get_connection()
    init_db(conn)
    ctx.ensure_object(dict)
    ctx.obj["conn"] = conn


# ── products ────────────────────────────────────────────────────────────────

@cli.group()
def product():
    """Manage products."""


@product.command("add")
@click.option("--sku", required=True)
@click.option("--name", required=True)
@click.option("--price", required=True, type=float)
@click.option("--threshold", default=10, show_default=True, type=int)
@click.pass_context
def product_add(ctx, sku, name, price, threshold):
    from .models import Product
    p = service.create_product(_conn(ctx), Product(None, sku, name, price, threshold))
    click.echo(f"Created product #{p.id}: {p.name} (SKU: {p.sku}, price: ${p.unit_price:.2f})")


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
@click.pass_context
def product_update(ctx, product_id, sku, name, price, threshold):
    p = service.get_product(_conn(ctx), product_id)
    p.sku = sku or p.sku
    p.name = name or p.name
    p.unit_price = price if price is not None else p.unit_price
    p.reorder_threshold = threshold if threshold is not None else p.reorder_threshold
    updated = service.update_product(_conn(ctx), p)
    click.echo(f"Updated product #{updated.id}: {updated.name}")


# ── stock ────────────────────────────────────────────────────────────────────

@cli.command("stock")
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


# ── orders ───────────────────────────────────────────────────────────────────

@cli.group()
def order():
    """Manage purchase and sale orders."""


@order.command("buy")
@click.argument("product_id", type=int)
@click.argument("quantity", type=int)
@click.option("--price", type=float, help="Override unit price")
@click.pass_context
def order_buy(ctx, product_id, quantity, price):
    """Create a purchase order (stock in)."""
    o = service.create_order(_conn(ctx), product_id, OrderType.PURCHASE, quantity, price)
    click.echo(f"Purchase order #{o.id} created: {quantity} units @ ${o.unit_price:.2f} = ${o.total:.2f}")


@order.command("sell")
@click.argument("product_id", type=int)
@click.argument("quantity", type=int)
@click.option("--price", type=float, help="Override unit price")
@click.pass_context
def order_sell(ctx, product_id, quantity, price):
    """Create a sale order (stock out)."""
    o = service.create_order(_conn(ctx), product_id, OrderType.SALE, quantity, price)
    click.echo(f"Sale order #{o.id} created: {quantity} units @ ${o.unit_price:.2f} = ${o.total:.2f}")


@order.command("fulfill")
@click.argument("order_id", type=int)
@click.pass_context
def order_fulfill(ctx, order_id):
    """Fulfill a pending order (applies stock change)."""
    o = service.fulfill_order(_conn(ctx), order_id)
    click.echo(f"Order #{o.id} fulfilled.")


@order.command("cancel")
@click.argument("order_id", type=int)
@click.pass_context
def order_cancel(ctx, order_id):
    """Cancel an order (reverses stock if fulfilled)."""
    o = service.cancel_order(_conn(ctx), order_id)
    click.echo(f"Order #{o.id} cancelled.")


@order.command("list")
@click.option("--product", "product_id", type=int)
@click.option("--status", type=click.Choice([s.value for s in OrderStatus]))
@click.pass_context
def order_list(ctx, product_id, status):
    status_filter = OrderStatus(status) if status else None
    orders = service.list_orders(_conn(ctx), product_id, status_filter)
    if not orders:
        click.echo("No orders.")
        return
    click.echo(f"{'ID':<5} {'Product':<8} {'Type':<10} {'Qty':>5} {'Price':>8} {'Total':>8} {'Status':<12} Created")
    click.echo("-" * 80)
    for o in orders:
        click.echo(
            f"{o.id:<5} {o.product_id:<8} {o.order_type.value:<10} {o.quantity:>5} "
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
def alert_ack(ctx, alert_id):
    """Acknowledge an alert."""
    a = service.acknowledge_alert(_conn(ctx), alert_id)
    click.echo(f"Alert #{a.id} acknowledged.")


def main():
    cli()
