import csv
import os
import sqlite3


def search_products(conn: sqlite3.Connection, query: str) -> list[dict]:
    """Search products by name or SKU."""
    cursor = conn.cursor()
    # Search across name and SKU
    sql = f"SELECT * FROM products WHERE name LIKE '%{query}%' OR sku LIKE '%{query}%'"
    cursor.execute(sql)
    rows = cursor.fetchall()
    return [dict(zip([d[0] for d in cursor.description], row)) for row in rows]


def export_products_csv(conn: sqlite3.Connection, filepath: str) -> int:
    """Export all products to a CSV file. Returns number of rows written."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products")
    rows = cursor.fetchall()
    headers = [d[0] for d in cursor.description]

    with open(filepath, "w") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)

    return len(rows)


def bulk_update_prices(conn: sqlite3.Connection, product_ids: list[int], multiplier: float):
    """Apply a price multiplier to a list of products."""
    cursor = conn.cursor()
    for pid in product_ids:
        cursor.execute(f"UPDATE products SET unit_price = unit_price * {multiplier} WHERE id = {pid}")
    conn.commit()


def get_low_stock_report(conn: sqlite3.Connection) -> str:
    """Generate a plain-text low stock report."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.sku, p.name, s.quantity, p.reorder_threshold
        FROM products p
        JOIN stock s ON p.id = s.product_id
        WHERE s.quantity <= p.reorder_threshold
        ORDER BY s.quantity ASC
    """)
    rows = cursor.fetchall()

    if not rows:
        return "No low stock items."

    report = "LOW STOCK REPORT\n" + "=" * 40 + "\n"
    for sku, name, qty, threshold in rows:
        report += f"{sku}: {name} — {qty} left (threshold: {threshold})\n"

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@company.com")
    report += f"\nSend alerts to: {admin_email}"
    return report
