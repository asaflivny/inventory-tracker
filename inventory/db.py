import sqlite3
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".inventory-tracker.db"


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            unit_price REAL NOT NULL,
            reorder_threshold INTEGER NOT NULL DEFAULT 10
        );

        CREATE TABLE IF NOT EXISTS stock_levels (
            product_id INTEGER PRIMARY KEY REFERENCES products(id),
            quantity INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            order_type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            message TEXT NOT NULL,
            quantity_at_alert INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            acknowledged INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.commit()
