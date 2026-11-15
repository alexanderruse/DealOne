# DealOne

**Set your price. Stay in control.** DealOne discovers and watches real products at approved stores, turns requests into reviewed permissions with Grok, and demonstrates spending controls through Capital One's Nessie sandbox.

## Real products, two experiences

The approved stores are **Keychron** (`keychron.com`) and **Epomaker** (`epomaker.com`). Grok and the dashboard can search each store's public Shopify catalog or inspect a pasted product link. Discovery is not restricted to a fixed keyboard and mouse: keycaps, switches, accessories, and new model names use the same adapter.

Search returns up to six real listing previews with merchant images and **from** prices. Exact variants must be fetched before a watch is allowed; product-level availability is not treated as variant stock. The first two matches are automatically queued, and **Check variants & stock** prioritizes another match. Links are restricted to HTTPS product paths on approved hosts; redirects, arbitrary URLs, and private store APIs are not accessed. There is no fictional store in customer choices.

- **Merchant price watch:** monitor the actual item price and receive alerts. Complete checkout at the approved merchant after confirming taxes, shipping, and delivery. DealOne does not yet submit real retailer orders.
- **Nessie banking demonstration:** use the same real item with an explicitly reviewed **sandbox final total**. This is a test scenario, not a merchant offer. The worker executes controlled browser checkout, authorizes a sandbox charge, and records its bank reference. Every receipt states that no merchant order was placed.

Sold-out variants cannot be requested. The backend checks the exact variant's latest verified stock snapshot when creating or activating a permission and before sandbox payment. Snapshots older than three minutes fail closed. A database-backed queue deduplicates requests across users and pods, allows one network request per store every 65 seconds, honors longer `Retry-After` delays, caches search results for ten minutes, and stops after three failed attempts until retried. Pending checks are visible and polled by the UI. Public storefronts can still throttle or be unavailable; no unlimited or instantaneous stock guarantee is made.

Active watches get refresh priority (target 130 seconds when capacity permits). Unwatched products are checked less often, preserving request capacity for user searches; **Check current availability** requests a fresh exact-variant check. Many simultaneous products can exceed public-feed capacity, in which case stale stock stays blocked. Partner-authorized Storefront APIs or merchant feeds are the next step for higher throughput.

Price history contains only actual DealOne observations for each merchant variant. It begins when the product is imported; there is no invented earlier history. The dashboard displays observation counts, low/high prices and dates, with one batched history request rather than a request per product. Prices are USD item prices before merchant tax and shipping; sandbox totals never overwrite merchant prices or inventory.

Grok uses `discover_products`, `inspect_product`, `find_product`, `get_price_history`, `get_activity`, and reviewed purchase tools. Commands update the live dashboard and leave an Activity entry; a search never creates a bank charge. Grok can draft rules with an explicit budget, but the user must activate them. Playwright remains confined to the controlled sandbox checkout. The interface uses permanent red/black theme tokens with dark text, larger type, and mobile layout checks.

## Run locally

```sh
cp .env.example .env
docker compose up -d --build --wait
```

Open **http://localhost:3001**. Docker Compose provides React/Caddy, FastAPI, a dedicated Chromium worker, and TimescaleDB. Production uses Tiger Cloud and the existing Vultr Kubernetes cluster. Data persists in the Docker database volume.

Set `XAI_API_KEY` and `XAI_MODEL` for Grok. Without credentials, manual watch creation remains available. Real feeds must confirm an available variant before a new watch can be requested. An unavailable merchant produces an honest empty/unverified state rather than invented products.

## Demonstration walkthrough

1. Search for a product at an approved store or paste its product URL. Wait for exact-variant verification; sold-out and unverified variants cannot be selected.
2. Click **Try Nessie sandbox**, or choose **Nessie banking demonstration** in the watch dialog.
3. Set an all-in spending maximum and a lower sandbox test total. Review both, along with the exact variant and expiration.
4. Keep **Ask me first**, the default, and activate. A dedicated card is limited to this permission's maximum.
5. In Watchlist, use **Verify Nessie card** to display the limit and available credit returned directly by the bank API.
6. Click **Approve purchase**. Activity shows a `SANDBOX-` receipt, the transaction reference, and the live Nessie ledger. No merchant order was placed.
7. Cancel while the bank transaction is pending. The application offers a 50-second window; the bank can refuse cancellation after settlement.

Automatic sandbox submission requires opting in on that permission. Grok cannot activate a permission, raise its limits, bypass ask-first approval, or request a sold-out item. The controlled browser never visits arbitrary merchant pages.

## Nessie integration

`PAYMENTS_MODE=simulation` uses a local simulator, clearly labeled. `PAYMENTS_MODE=nessie` uses the official Nessie v2 sandbox API:

- A credit-card account per permission, with `credit_limit_cents` equal to the approved cap.
- A purchase posted to that account and a durable order-to-transaction mapping.
- Bank declines surfaced in the watchlist and receipt.
- Direct account verification showing limit, pending debits and available credit.
- Pending-charge cancellation and a live transaction ledger.

For local Nessie use, configure `NESSIE_API_KEY`, `NESSIE_CUSTOMER_ID`, and `NESSIE_MERCHANT_ID`. These identify sandbox resources, not a real retailer payment relationship. No production virtual-card issuance or real funds are involved.

GitHub deployment reads the existing `NESSIE_API_KEY` secret. `scripts/nessie_setup.py` authenticates with v2 and resolves or creates a dedicated DealOne sandbox customer and bookkeeping merchant. IDs may instead be supplied as `NESSIE_CUSTOMER_ID` and `NESSIE_MERCHANT_ID` repository variables. Invalid credentials stop deployment with a clear error rather than falsely reporting a bank connection. Credentials never enter frontend bundles or Grok context.

Official API contract: [Nessie v2 OpenAPI](https://v2-prod-api.nessieisreal.com/openapi.json).

## Hosting and delivery

React, TypeScript, Vite and Tailwind form the frontend. FastAPI, Pydantic, SQLAlchemy, Alembic and Psycopg form the backend. The official xAI SDK supplies structured rules and bounded tools. Tiger Cloud stores application records and a price-observation hypertable.

Two web replicas, two API replicas and one Playwright Chromium worker run in the existing Vultr cluster. Traefik and cert-manager provide HTTPS for **dealone.work**. GitHub Actions checks changes, builds immutable images, runs migrations and waits for readiness. Deployment never creates additional Kubernetes clusters. See [deployment setup](docs/DEPLOYMENT.md) for secrets, DNS and rollback.

Porkbun DNS automation uses `PORKBUN_API_KEY` and `PORKBUN_SECRET_API_KEY`, updates only website records and preserves email/subdomains. The Vultr API key remains in deployment tooling.

## Verification

Backend tests use isolated database schemas and mock external banking. Fictional boundary-test fixtures never appear in the customer catalog. Browser acceptance uses deterministic real-SKU fixtures in an explicitly named test environment and exercises the actual controlled browser worker.

```sh
docker compose exec -T api python -m pytest -q tests
python3 -m unittest discover -s scripts/tests -q
kubectl kustomize infra/k8s
```

CI sets `APP_ENV=test`, disables merchant polling, and runs `python -m tests.seed_real_catalog` before Chromium acceptance tests. That fixture script refuses non-test environments.

Health endpoints are `/api/health/live`, `/api/health/ready`, and `/api/health/worker`. Alerts are in-app. Email, a second merchant, real retailer auto-checkout and production card issuance remain separate integrations.
