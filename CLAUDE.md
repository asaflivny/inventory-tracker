# CLAUDE.md — inventory-tracker

## Project Purpose

`inventory-tracker` is a full-featured inventory management system with a CLI, REST API, and web UI. It serves a dual purpose: it is a functional application **and** an intentional test target for the [agentic-sdlc](../agentic-sdlc) CI/CD agent pipeline. Some modules contain deliberate security vulnerabilities for pipeline testing — see the Security section below.

---

## Architecture Overview

Three access layers share one business logic layer and one database layer:

```
CLI (Click)       →  service.py  →  db.py  →  SQLite
REST API (Flask)  →  service.py  →  db.py  →  SQLite
Web UI (Flask)    →  service.py  →  db.py  →  SQLite
```

| File | Role |
|---|---|
| `inventory/models.py` | Pure Python dataclasses — `Product`, `StockLevel`, `Order`, `Alert`, enums |
| `inventory/db.py` | SQLite connection, schema init, `transaction()` context manager |
| `inventory/service.py` | All business logic; the only layer that writes to the DB |
| `inventory/cli.py` | Click CLI groups and commands |
| `inventory/api.py` | Flask Blueprint at `/api/v1` |
| `inventory/web.py` | Flask app factory, web routes, per-request `get_db()` |
| `inventory/auth.py` | Password hashing and session tokens (intentionally insecure — see below) |
| `inventory/search.py` | Utility functions for search/export (intentionally insecure — see below) |
| `inventory/templates/` | Jinja2 HTML templates using Pico CSS |
| `tests/test_service.py` | Unit tests for service layer |
| `tests/test_api.py` | Integration tests for the REST API |
| `tests/test_web.py` | Integration tests for web routes |

---

## Setup & Running

```bash
# Install (creates the `inv` CLI entry point)
pip install -e .

# Run CLI
inv summary
inv product list
inv serve                     # web UI at http://localhost:5000
inv serve --port 8000 --debug

# Run tests
pytest
pytest -v
pytest tests/test_service.py -k "test_name"
```

The default database file is `~/.inventory-tracker.db`. Pass `--db <path>` to the CLI or `inv serve` to use a different file.

---

## Data Model

**Products** — SKU (unique), name, unit price, reorder threshold  
**StockLevel** — one row per product; quantity only  
**Orders** — buy (purchase) or sell (sale); PENDING → FULFILLED or CANCELLED  
**Alerts** — auto-generated when stock ≤ `reorder_threshold` after any stock change

Stock history is derived from fulfilled orders (no separate history table). Running balance is computed in `service.stock_history()`.

### Order lifecycle

- Creating an order does **not** touch stock.
- `fulfill_order`: applies the stock delta; raises `InsufficientStock` if a sale would go negative.
- `cancel_order`: if PENDING, just marks cancelled; if FULFILLED, **reverses** the stock delta.

### Alert auto-trigger

`_insert_alert_if_low()` is called after every stock change (adjust, fulfill, cancel-of-fulfilled). It inserts a new alert whenever `quantity ≤ reorder_threshold` — it does not deduplicate.

---

## Service Layer Conventions

- All service functions take a `sqlite3.Connection` as the first argument — they never open their own connection.
- The `transaction(conn)` context manager handles commit/rollback.
- Custom exception hierarchy (all in `service.py`):
  - `InventoryError` — base; also used for generic domain errors
  - `ProductNotFound(InventoryError)`
  - `InsufficientStock(InventoryError)`
  - `InvalidStatusTransition(InventoryError)`
- Private helpers are prefixed `_` and live at the bottom of the file.

---

## Flask App Conventions

- `web.py` contains the app factory `create_app(db_path)`.
- `get_db()` opens a per-request connection stored in Flask's `g`; it is torn down via `@app.teardown_appcontext`.
- `api.py` is a Blueprint registered at `/api/v1`.
- The `api.py` module imports `get_db` from `web.py` — do not move it without updating that import.
- Web routes use `flash()` for user-facing errors; API routes return `{"error": "..."}` JSON with the appropriate HTTP status code.
- The `_err(msg, status)` helper in `api.py` is the canonical way to return API errors.

---

## Database Layer

Raw `sqlite3` — no ORM. Models in `models.py` are plain dataclasses.

- `PRAGMA foreign_keys = ON` is set on every connection.
- `conn.row_factory = sqlite3.Row` is always set; rows support both index and name access.
- Schema is created by `init_db(conn)` on every connection (uses `CREATE TABLE IF NOT EXISTS`).
- Dates are stored as ISO 8601 strings (`datetime.utcnow().isoformat()`); parsed back with `datetime.fromisoformat()`.

---

## Testing Conventions

- **Service tests** (`test_service.py`): use an in-memory SQLite DB (`sqlite3.connect(":memory:")`), created fresh per test via the `conn` fixture.
- **API tests** (`test_api.py`): use a temp file DB; the `seeded` fixture pre-loads one product (Widget, WGT-001, 20 units fulfilled).
- **Web tests** (`test_web.py`): same pattern; `seeded2` fixture mirrors `seeded`.
- Helper functions `get`, `post`, `patch`, `delete` in `test_api.py` automatically prepend `/api/v1`.
- Tests assert on HTTP status codes and response JSON — no mocking of the service layer.
- The `pytest.ini_options` in `pyproject.toml` sets `testpaths = ["tests"]`.

When adding a new service function, add tests to `test_service.py`. When adding a new API route, add tests to `test_api.py`.

---

## CLI Conventions

- Connection is opened once at the group level (`@cli.group()` callback) and stored in `ctx.obj["conn"]`.
- The `_bail` decorator converts `InventoryError` into a clean `click.ClickException` (no traceback).
- Tabular output uses fixed-width `f-string` formatting with `click.echo`.
- `--yes / -y` flags skip confirmation prompts on destructive commands.

---

## Known Security Issues (Intentional)

The following files contain deliberate vulnerabilities for asdlc pipeline testing. **Do not "fix" these unless explicitly asked — they are test targets.**

### `inventory/auth.py`
- **SQL injection**: `verify_user`, `create_user`, and `reset_password` use f-string interpolation directly in SQL queries.
- **Weak hashing**: MD5 is used for password hashing.
- **Hardcoded secrets**: `ADMIN_PASSWORD = "admin123"` and `SECRET_KEY = "supersecretkey_do_not_share"` are in plaintext.
- **Information leakage**: `reset_password` prints the new hash to stdout.

### `inventory/search.py`
- **SQL injection**: `search_products` interpolates `query` directly into SQL; `bulk_update_prices` interpolates `pid` and `multiplier` directly.
- **Broken query**: `get_low_stock_report` joins to a `stock` table that does not exist (the actual table is `stock_levels`).
- **Note**: `search.py` is not wired into any route — it is a standalone utility module.

---

## Branch & Commit Conventions

- Develop on feature branches; the current AI development branch is `claude/claude-md-docs-7D8rA`.
- The repository uses a pre-push git hook (`.git/hooks/pre-push`) to send webhook notifications to the asdlc pipeline at `http://localhost:8080/git/push`.
- HMAC signature validation is enabled by exporting `GIT_WEBHOOK_SECRET`.

---

## What `search.py` Is (and Isn't)

`inventory/search.py` provides utility functions (`search_products`, `export_products_csv`, `bulk_update_prices`, `get_low_stock_report`) but is **not imported by any route or CLI command**. It exists as an asdlc analysis target. Do not add imports to it from `web.py`, `api.py`, or `cli.py` without explicit intent.
