# inventory-tracker

A full-featured inventory management system with CLI, REST API, and web UI. Built to serve dual purposes: as a functional application for managing products, stock, orders, and alerts—and as a test target for the [agentic-sdlc](../agentic-sdlc) project's CI/CD agent pipeline.

## Overview

**inventory-tracker** provides:

- **CLI**: Full command-line interface for all inventory operations
- **REST API** (`/api/v1`): JSON API for programmatic access to all features
- **Web UI**: Flask-based dashboard with interactive forms and real-time status
- **Database**: SQLite schema for products, stock levels, orders, and alerts
- **Authentication**: Password hashing and session token management
- **Business Logic**: Stock adjustments, order fulfillment, low-stock alerts, reorder forecasting

## Installation & Setup

```bash
pip install -e .
```

This installs the `inv` CLI command and dependencies (Click for CLI, Flask for web).

## Usage

### CLI

```bash
inv summary                          # Dashboard: stock value, pending orders, alerts
inv product list                     # List all products
inv product add SKU "Name" 29.99 5   # Add product with reorder threshold
inv product delete <id>              # Delete product (guards against orders)

inv stock list                       # View current stock levels
inv stock adjust <id> +5             # Adjust stock by delta (+ or -)
inv stock history <id>               # Show stock adjustment history

inv order buy <product_id> 10 --price 20    # Create a buy order (increase stock)
inv order sell <product_id> 5 --price 20   # Create a sell order (decrease stock)
inv order list --status pending             # Filter orders by status
inv order list --since 2026-01-01           # Filter orders by date range
inv order fulfill <order_id>         # Mark order as fulfilled
inv order cancel <order_id>          # Cancel an order

inv alert list                       # Show pending/all alerts
inv alert ack <alert_id>             # Acknowledge a low-stock alert
inv alert ack-all                    # Acknowledge all pending alerts

inv serve                            # Start web UI (default: localhost:5000)
inv serve --port 8000 --debug        # Custom port and debug mode
```

### REST API

All endpoints at `/api/v1` return JSON. Full list:

**Products**
- `GET /api/v1/products` — list all
- `POST /api/v1/products` — create (JSON body: `sku`, `name`, `unit_price`, `reorder_threshold`)
- `GET /api/v1/products/<id>` — get one
- `PATCH /api/v1/products/<id>` — update fields
- `DELETE /api/v1/products/<id>` — delete

**Stock**
- `GET /api/v1/stock` — all stock levels
- `GET /api/v1/stock/<id>` — stock for product
- `POST /api/v1/stock/<id>/adjust` — adjust stock (JSON: `delta`)
- `GET /api/v1/stock/<id>/history` — adjustment history

**Orders**
- `GET /api/v1/orders` — list orders (filters: `?status=pending`, `?since=`, `?until=`, `?product_id=`)
- `POST /api/v1/orders/buy` — buy order (JSON: `product_id`, `quantity`, `unit_price`)
- `POST /api/v1/orders/sell` — sell order (JSON: `product_id`, `quantity`, `unit_price`)
- `POST /api/v1/orders/<id>/fulfill` — fulfill order
- `POST /api/v1/orders/<id>/cancel` — cancel order

**Alerts**
- `GET /api/v1/alerts` — list (filter: `?unacked=1` for pending only)
- `POST /api/v1/alerts/<id>/ack` — acknowledge
- `POST /api/v1/alerts/ack-all` — acknowledge all

**Dashboard**
- `GET /api/v1/summary` — inventory metrics
- `GET /api/v1/reorder` — products below reorder threshold

### Web UI

Start the server with `inv serve`, then visit `http://localhost:5000`:

- **Dashboard**: Key metrics, pending alerts
- **Products**: List, add, edit, delete
- **Stock**: Current levels, adjustment form, per-product history
- **Orders**: List with status/date filters, inline buy/sell with optional fulfill
- **Alerts**: Acknowledge individually or bulk acknowledge all
- **Reorder**: Products at/below threshold, sorted by shortfall

## Testing

```bash
pytest              # Run all tests (112+ tests)
pytest -v           # Verbose output
pytest tests/test_service.py -k "test_name"  # Run specific test
```

Test coverage includes service layer, web endpoints, API responses, and error cases.

## Database

SQLite database at `inventory.db` (or custom path via `--db` flag).

Schema includes:
- `products` — SKU, name, unit price, reorder threshold
- `stock_adjustments` — history of all stock changes
- `orders` — buy/sell operations with dates and fulfillment status
- `alerts` — low-stock warnings with acknowledgment status

## Webhook Integration (asdlc Testing)

This repo serves as a test target for the [agentic-sdlc](../agentic-sdlc) CI/CD agent pipeline. Every git push triggers code review, security analysis, and performance evaluation agents.

**Pre-push hook** at `.git/hooks/pre-push` sends webhook notifications to `http://localhost:8080/git/push`.

To enable HMAC signature validation:

```bash
export GIT_WEBHOOK_SECRET=your-secret-here
git push origin main
```

To test the webhook manually:

```bash
echo "# test" >> notes.txt
git add notes.txt && git commit -m "test: trigger asdlc"
git push origin main
```

## Project Structure

```
inventory/
  ├── cli.py         # Click CLI commands and groups
  ├── api.py         # Flask REST API blueprint
  ├── web.py         # Flask app setup and web routes
  ├── service.py     # Business logic (create, update, list, filter)
  ├── models.py      # SQLAlchemy ORM models
  ├── db.py          # Database initialization and connection
  ├── auth.py        # Password hashing and session tokens
  ├── search.py      # Product search utilities
  └── templates/     # Flask HTML templates (Pico CSS)
tests/
  └── test_service.py     # Comprehensive service layer tests
```

## Error Handling

API and CLI both validate input and return meaningful error messages:
- `400` — invalid request (missing/malformed data)
- `404` — resource not found
- `409` — conflict (e.g., duplicate SKU, insufficient stock)
