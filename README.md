# DealOne

**Set your price. Stay in control.** DealOne watches real Keychron products, turns requests into reviewed permissions with Grok, and demonstrates spending controls through Capital One's Nessie sandbox.

## Real products, two experiences

The customer catalog contains only real products from **Keychron**, the first approved store. It reads official product feeds for the C3 Pro keyboard and M3 mouse, displays exact variants and merchant artwork, and links directly to the listing. There is no fictional DealOne store in the catalog, Grok results, or item picker. Historical records remain stored, but retired fictional permissions cannot keep purchasing.

- **Merchant price watch:** monitor the actual item price and receive alerts. Complete checkout at Keychron after confirming taxes, shipping, and delivery. DealOne does not yet submit real retailer orders.
- **Nessie banking demonstration:** use the same real item with an explicitly reviewed **sandbox final total**. This is a test scenario, not a merchant offer. The worker executes controlled browser checkout, authorizes a sandbox charge, and records its bank reference. Every receipt states that no merchant order was placed.

Sold-out variants cannot be requested. A stock check is required when creating or activating a permission and immediately before sandbox payment. Availability that is unknown, stale, or cannot be refreshed fails closed. Price feeds refresh every five minutes. They report availability to order rather than warehouse inventory counts. Prices are USD item prices, excluding merchant shipping and tax. Sandbox totals never overwrite merchant prices, observed history, or inventory.

## Run locally

```sh
cp .env.example .env
docker compose up -d --build --wait
```

Open **http://localhost:3001**. Docker Compose provides React/Caddy, FastAPI, a dedicated Chromium worker, and TimescaleDB. Production uses Tiger Cloud and the existing Vultr Kubernetes cluster. Data persists in the Docker database volume.

Set `XAI_API_KEY` and `XAI_MODEL` for Grok. Without credentials, manual watch creation remains available. Real feeds must confirm an available variant before a new watch can be requested. An unavailable merchant produces an honest empty/unverified state rather than invented products.

## Demonstration walkthrough

1. Choose an available real Keychron variant. A sold-out C3 Pro cannot be selected.
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
