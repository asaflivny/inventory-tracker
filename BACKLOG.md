# Inventory Tracker — Feature Backlog

Items marked ✅ are shipped. Everything below is outstanding, ordered by priority.

---

## ✅ Completed

| # | Feature |
|---|---------|
| 1 | Supplier model — full CRUD across CLI, API, service, DB |
| 4 | Bulk CSV import/export — products, stock, orders |

---

## P1 — Business logic enhancements

### #7 · Purchase order workflow
Full PO lifecycle separate from the simple buy/sell order model.

- Statuses: `draft → sent → partially_received → fully_received`
- Partial fulfillment updates stock incrementally (e.g. received 40 of 100 ordered)
- POs are linked to a supplier
- CLI: `inv po create/send/receive/list`
- REST API: `/api/v1/purchase-orders` CRUD + `/receive` action

### #8 · Cost of goods (COGS) tracking
Accurate unit economics per SKU.

- Weighted-average or FIFO costing method (configurable per product)
- `cost_price` field on products (separate from `unit_price` / sell price)
- Gross margin calculated on each fulfilled sale order
- New report: `GET /api/v1/reports/margin` — revenue, COGS, margin % per product

### #9 · Expiry date tracking
Batch-level expiry for perishables and time-sensitive inventory.

- `batches` table: product_id, quantity, expiry_date, received_at
- FEFO (first-expired, first-out) pick strategy on sale fulfillment
- Alert type: approaching expiry (configurable days-ahead threshold)
- CLI: `inv batch add/list`, expiry filter on stock list

### #10 · Return / refund orders
Reverse a sale without manually adjusting stock.

- New `OrderType.RETURN` that links to an original sale order
- Fulfilling a return adds stock back and records the reversal
- CLI: `inv order return <original_order_id> --quantity <n>`
- REST API: `POST /api/v1/orders/return`

---

## P2 — API & integration improvements

### #11 · API authentication
The `auth` module exists but no endpoints are protected.

- Wire session-token or JWT auth onto all `/api/v1` routes
- `POST /api/v1/auth/login` → returns token
- `POST /api/v1/auth/logout`
- Middleware that rejects unauthenticated requests with 401
- Token stored in `auth_tokens` table (already partially modelled)

### #12 · Pagination & cursor
Unbounded list endpoints become a problem at scale.

- Add `?page=` + `?per_page=` (default 50) to `/orders`, `/products`, `/alerts`
- Response envelope: `{"data": [...], "total": N, "page": P, "pages": T}`
- Or cursor-based: `?cursor=<opaque>` + `"next_cursor"` in response

### #13 · Webhook / event bus
Fire HTTP callbacks on key inventory events.

- `webhooks` table: url, event types, secret for HMAC signing
- Events: `stock.low`, `order.fulfilled`, `order.cancelled`, `alert.created`
- CLI: `inv webhook add/list/delete`
- REST API: `/api/v1/webhooks` CRUD
- Background delivery with retry (simple in-process queue to start)

### #14 · OpenAPI spec
Auto-generated API documentation.

- Integrate `flask-openapi3` or `apispec`
- All existing endpoints annotated with request/response schemas
- Served at `GET /api/v1/openapi.json` and `/api/v1/docs` (Swagger UI)

### #15 · Rate limiting
Basic protection for the REST API.

- Integrate `Flask-Limiter` with an in-memory or Redis backend
- Default: 200 requests/minute per IP
- Configurable via env var `RATE_LIMIT`
- Returns 429 with `Retry-After` header on breach

---

## P3 — Reporting & observability

### #16 · Sales velocity report
Foundation for reorder suggestions.

- Units sold per day / week / month per SKU, computed from fulfilled sale orders
- REST API: `GET /api/v1/reports/velocity?product_id=&period=7d`
- CLI: `inv report velocity [--days 30]`

### #17 · Stock valuation report
Financial snapshot of inventory.

- Total inventory value at cost price vs. potential revenue at sell price
- Broken down by product and (optionally) supplier
- REST API: `GET /api/v1/reports/valuation`
- CLI: `inv report valuation`

### #18 · Turnover / dead-stock report
Surface products with no movement.

- Products with zero fulfilled orders in the last N days
- Configurable look-back window (default 90 days)
- REST API: `GET /api/v1/reports/dead-stock?days=90`
- CLI: `inv report dead-stock [--days 90]`

### #19 · Structured logging
Replace ad-hoc output with machine-readable logs.

- Integrate `structlog`
- Every service operation emits a structured event with `product_id`, `order_id`, `action`, `result`
- Log level configurable via `LOG_LEVEL` env var
- JSON output mode for production (`LOG_FORMAT=json`)

### #20 · Health & metrics endpoints
Operational visibility.

- `GET /health` — liveness probe: returns `{"status": "ok"}` + DB connectivity check
- `GET /metrics` — Prometheus-compatible counters:
  - `inventory_orders_total{type, status}`
  - `inventory_stock_adjustments_total`
  - `inventory_alerts_total{acknowledged}`

---

## P4 — Web UI improvements

### #21 · Dashboard charts
Visual stock and sales trends.

- Stock level bar chart per product (Chart.js, no build step)
- Sales-over-time line chart (last 30 days)
- Low-stock products highlighted in red on the stock table

### #22 · Inline stock edit
Edit reorder threshold without navigating away.

- Editable cell on the stock list page (AJAX PATCH on blur)
- Validation feedback inline

### #23 · Order detail page
Drill into a single order.

- Shows product info, status history, linked alerts
- Cancel / fulfill action buttons directly on the page

### #24 · Alert badge in nav
Live unacknowledged alert count.

- Badge on the "Alerts" nav link updated on each page load
- Highlight red when count > 0

### #25 · Dark mode
Theme toggle.

- CSS custom properties for all colours
- Toggle button in header; preference persisted in `localStorage`

---

## P5 — Developer / ops quality

### #26 · Alembic migrations
Replace hand-rolled `_migrate()` with proper versioned migrations.

- `alembic init` + migration scripts for all schema changes to date
- `inv db upgrade` CLI command wrapping `alembic upgrade head`
- Downgrade scripts for each migration

### #27 · Docker / Compose
One-command local stack.

- `Dockerfile` (slim Python image, non-root user)
- `docker-compose.yml` — app + optional volume-mounted SQLite
- `.env.example` with all supported env vars documented

### #28 · Seed data command
Realistic demo data for development and testing.

- `inv seed` CLI command — creates ~20 products across 5 suppliers, varied stock levels, 100+ historical orders
- Idempotent (safe to re-run; skips existing SKUs)

### #29 · Coverage enforcement
Gate on test coverage in CI.

- Add `pytest-cov` to dev dependencies
- `--cov-fail-under=80` in `pyproject.toml` or `pytest.ini`
- Coverage report uploaded as CI artifact

### #30 · Property-based tests
Fuzz core business logic with `hypothesis`.

- `adjust_stock` — arbitrary positive/negative deltas, verify quantity never goes negative
- `fulfill_order` / `cancel_order` — random sequences of operations, verify stock consistency
- `import_products_csv` — malformed CSV inputs, verify no unhandled exceptions
