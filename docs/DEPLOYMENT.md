# Deployment to dealone.work

## 1. Add GitHub configuration

Open [repository Actions secrets](https://github.com/alexanderruse/DealOne/settings/secrets/actions). Keep these values out of chat, commits, and command-line arguments.

| Secret | Required value |
| --- | --- |
| `VULTR_API_KEY` | Current Vultr API key with access to the existing VKE cluster and container registry |
| `DATABASE_URL` | Tiger Cloud SQLAlchemy URL: `postgresql+psycopg://USER:PASSWORD@HOST:PORT/tsdb?sslmode=require` |
| `OPENROUTER_API_KEY` | OpenRouter API key for the shopping assistant |
| `SESSION_SECRET` | A unique random value of at least 32 characters |
| `DEMO_ADMIN_TOKEN` | A different unique random value of at least 24 characters |
| `RESEND_API_KEY` | Optional; reserved for future email opt-in support |
| `NESSIE_API_KEY` | Optional: Capital One Nessie v2 sandbox API key; enables `PAYMENTS_MODE=nessie` when a customer or card account is also set |
| `PORKBUN_API_KEY` | Optional: Porkbun API key scoped to DNS management for `dealone.work` |
| `PORKBUN_SECRET_API_KEY` | Optional: matching Porkbun secret API key; provide both to enable DNS automation |

URL-encode special characters in database usernames/passwords. Use a dedicated database/schema and a migration-capable Tiger Cloud user; the initial migration creates tables and enables TimescaleDB if needed. The app refuses non-TLS production database connections. Use `sslmode=verify-full` with your provider's CA configuration when available.

Generate independent secrets using `openssl rand -hex 32`. Store each generated value directly in GitHub; do not use the local `.env.example` defaults in production. Keep `SESSION_SECRET` stable between deploys so sessions and worker authentication continue to work.

Create these repository **Variables**:

| Variable | Value |
| --- | --- |
| `ACME_EMAIL` | Team email for Let's Encrypt registration; required when deployment configures HTTPS |
| `OPENROUTER_MODEL` | Exact enabled OpenRouter model ID; must support structured outputs and function calling (local example: `google/gemini-3.8-flash`) |
| `AI_BUDGET_USD` | Rolling AI spend ceiling; deployment defaults to `20` when unset |
| `AI_BUDGET_WINDOW_SECONDS` | Rolling budget window in seconds; deployment defaults to `10800` (3 hours) when unset |
| `VULTR_CLUSTER_ID` | UUID of your existing Vultr Kubernetes cluster (`dealone-cluster`); use the cluster ID from its console URL or API, not its label or IP address |
| `VULTR_REGISTRY_ENDPOINT` | Optional override; otherwise discovered from Vultr metadata, including the registry namespace |
| `RESEND_FROM_EMAIL` | Optional; reserved for future email delivery |
| `NESSIE_CUSTOMER_ID` | Optional but preferred for Nessie: sandbox customer used to mint one credit card per DealOne permission |
| `NESSIE_CARD_ACCOUNT_ID` | Optional fallback shared Nessie card when per-permission minting is unavailable |
| `NESSIE_MERCHANT_ID` | Required whenever Nessie is enabled in production; DealOne Demo Store merchant id |

When `NESSIE_API_KEY` is set with `NESSIE_CUSTOMER_ID` or `NESSIE_CARD_ACCOUNT_ID`, deployment configures `PAYMENTS_MODE=nessie`. Production also requires `NESSIE_MERCHANT_ID`. The deploy workflow must expose those names into the job environment (secret + vars). Use Demo controls → **Seed sandbox** locally to create merchant/customer ids, then store them as secrets/variables. Each activated watch gets its own Nessie credit card capped at the watch budget; after the cancel window the worker settles pending charges.

Production sets `REAL_CATALOG_ENABLED=true` and exposes real Keychron products only. Disabling feed polling never restores fictional products; stale availability cannot accept requests.

Domain `dealone.work`, region `ewr`, and registry UUID `369a6c53-fd50-4fe2-93d6-a151ec00c845` are already set in deployment code. `GITHUB_TOKEN` is supplied by GitHub. Kubeconfig, registry username, and registry password are obtained by the workflow; separate repository secrets are not needed.

Vultr API access rules must allow the machine executing the workflow. If your API key is restricted to one fixed IP, use an appropriately secured self-hosted runner or configure an allowed runner egress path. Do not silently disable your account's access controls.

## 2. Use your existing Kubernetes cluster

The cluster is created and managed in the Vultr console. There is no cluster provisioning workflow. Set `VULTR_CLUSTER_ID` to the UUID of your existing `dealone-cluster`, not its label, node-pool ID, or public IP address. Wait for the cluster to become Active before the first deployment.

The deployment workflow retrieves kubeconfig through the Vultr API, waits for nodes to become Ready, and runs `scripts/bootstrap.py` against that cluster. This installs or reconciles pinned Traefik and cert-manager Helm releases and the Let's Encrypt issuer. Repeated deploys reuse the releases and do not create another cluster or worker nodes. Traefik's LoadBalancer service requests a Vultr load balancer, which has a separate recurring charge.

No downloaded kubeconfig, cluster password, or SSH key is needed in GitHub. `VULTR_API_KEY` must work from the GitHub runner: manually creating a cluster does not bypass API authentication or source-IP restrictions. An HTTP 401 while obtaining kubeconfig or registry credentials means the key or API access settings still need correction.

### Automatic Porkbun DNS

Create a key at [Porkbun API Access](https://porkbun.com/account/api), restrict it to `dealone.work` and DNS read/write access (no purchase permissions needed), and enable **API Access** in the domain's Details screen. Add both Porkbun secrets listed above to GitHub.

Provisioning now waits for Traefik's public IPv4, checks that Porkbun is authoritative, and synchronizes the root A record automatically. Existing root parking ALIAS/CNAME records are converted in place; duplicate root A records and stale root AAAA records are removed after the new IPv4 is configured. MX, TXT, CAA, and subdomain records are preserved. Repeating synchronization with the same IP performs no writes. The keys are used only by GitHub Actions, never placed in application pods.

Every application deployment repeats synchronization, so an ingress load balancer replaced between deployments gets its new address published. **Actions → Synchronize Porkbun DNS** can also refresh the existing cluster's address without rebuilding the app. This is workflow-driven synchronization, not continuous monitoring of manual cloud changes.

If both keys are absent, the workflow prints manual instructions instead. If only one is present, credentials are rejected, or Porkbun is not authoritative, it fails instead of reporting a successful DNS update. Read-only preview: `python3 scripts/dns.py --ip YOUR_LOAD_BALANCER_IPV4`; add `--apply` to apply the displayed plan. Without `--ip`, the script discovers Traefik's address through your current kubeconfig.

Public DNS resolvers may cache the old address until its previous TTL expires. An update verified through Porkbun does not mean every public resolver has refreshed immediately; re-run deployment after propagation if the HTTPS smoke check times out.

For manual setup, the load balancer appears under namespace `traefik`, service `traefik`. Set your registrar's **root A record** (blank Host at Porkbun, `@` at GoDaddy) to its external IP. Replace conflicting root website/parking records, preserving email records. Keep ports 80 and 443 reachable; cert-manager uses HTTP-01 validation. No `www` hostname is configured. GoDaddy API synchronization is not implemented: `dealone.work` currently uses Porkbun nameservers.

For local bootstrap instead of Actions, export `VULTR_API_KEY`, `VULTR_CLUSTER_ID`, `KUBECONFIG`, and `ACME_EMAIL` securely, run `python3 scripts/vultr.py kubeconfig`, then `python3 scripts/bootstrap.py`. Requires `kubectl` and Helm 3.

## 3. Deploy application

Push or merge to `main` to deploy automatically, including the first deployment. `Checks` runs first; a successful push check starts **Deploy DealOne**. No manual workflow run is required. Deployment validates runtime configuration; connects to your existing cluster; installs or reconciles ingress and HTTPS components; discovers the existing registry; gets expiring push credentials; builds Linux AMD64 API, worker, and web images tagged with the commit SHA; refreshes read-only pull credentials; synchronizes DNS; runs migrations and seed data as a Job; and rolls out the application.

After deployment succeeds, **Synchronize Porkbun DNS** runs automatically, followed on success by **Refresh registry pull credentials**. These maintenance workflows repeat deployment's DNS and credential reconciliation and can also be run independently for recovery. Pull-request checks do not deploy. Manual deployment remains available with a blank image tag for redeployment or a previous image tag for rollback. Deployment, DNS synchronization, and registry credential refresh share a concurrency group, preventing overlapping production mutations. Workflows never print registry passwords, API keys, connection strings, or generated kubeconfig. Credentials are removed from the runner at completion.

Vultr's registry credential endpoint used here is the documented registry API `OPTIONS /v2/registry/{id}/docker-credentials` with read/write and expiry parameters. Your API identity must have access to this registry. The pull token expires after 30 days and is refreshed by a weekly workflow, every application deployment, or a manual **Refresh registry pull credentials** run. Monitor failed scheduled runs; private-repo Actions minutes and provider access still apply.

The migration job must succeed before the workload rollout. New deployment configuration uses Kubernetes Secrets and a ConfigMap. Use additive migrations for future releases: reverting images does not revert schema or credentials.

Migration `0007` stores masked checkout screenshot PNGs in Tiger Data Postgres so every API replica can serve the same evidence after restarts. Image bytes are fetched only by the screenshot endpoint; gallery metadata checks do not load the images. Legacy local files remain a fallback while they exist. New captures are needed for historical files already lost with their containers. At larger screenshot volumes, migrate images to object storage and retain their metadata in Postgres.

## 4. Verify and operate

For local diagnostics, first connect to the existing cluster. With `VULTR_API_KEY` and `VULTR_CLUSTER_ID` securely exported, run:

```sh
export KUBECONFIG="$(mktemp -t dealone-kubeconfig)"
python3 scripts/vultr.py kubeconfig
```

If credentials have already been downloaded into this repository's ignored `.kube/production.kubeconfig`, connect from the repository root with `export KUBECONFIG="$PWD/.kube/production.kubeconfig"`. Then run the diagnostics below. Keep this credential file private and out of Git.

```sh
kubectl -n dealone get pods,ingress,certificate
kubectl -n dealone logs -l app=api --all-containers=true --prefix=true --tail=100 --since=30m
kubectl -n dealone logs -l app=worker --all-containers=true --prefix=true --tail=100 --since=30m
kubectl -n dealone rollout status deployment/api
curl --fail https://dealone.work/api/health/ready
curl --fail https://dealone.work/api/health/worker
```

For a crashed container, use `kubectl -n dealone logs POD_NAME --previous --tail=100`. Add `-f` to the selector-based log commands to follow live output.

Check the effective AI settings in each API replica without printing credentials:

```sh
for pod in $(kubectl -n dealone get pods -l app=api -o name); do
  echo "$pod"
  kubectl -n dealone exec "$pod" -- python -c 'from app.config import settings; s = settings(); print({"OPENROUTER_API_KEY_set": bool(s.openrouter_api_key), "OPENROUTER_MODEL": s.openrouter_model, "AI_BUDGET_USD": s.ai_budget_usd, "AI_BUDGET_WINDOW_SECONDS": s.ai_budget_window_seconds})'
done
```

Production does not generate or read the local development `.env` for deployment. GitHub Actions passes the API key to the `dealone-runtime` Kubernetes Secret and the model/budget values to the `dealone-config` ConfigMap. Both API and worker pods import these resources through `envFrom`. Set the GitHub secret and variables, then redeploy; changing GitHub settings alone does not update running pods. Deployment requires the OpenRouter key/model and restarts workloads after updating configuration. Legacy `XAI_API_KEY`/`XAI_MODEL` repository settings do not configure OpenRouter.

If you created a temporary kubeconfig with `mktemp`, remove it when finished: `rm -f "$KUBECONFIG"; unset KUBECONFIG`.

Once DNS propagates and the certificate is Ready, all three health routes and the dashboard should work over HTTPS. If certificate issuance is pending, check DNS and cert-manager's Certificate/Order/Challenge resources before retrying. The deploy workflow fails if public HTTPS or worker health does not pass.

Run the README demo on the deployed site using the secret `DEMO_ADMIN_TOKEN`. Check the exact item, all-in maximum, expiration, and quantity on the review screen before activation. In-app notifications and receipts are per browser session.

### Rollback

Run **Deploy DealOne** manually with a previous successful **full 40-character commit SHA** in `image_tag`. Images are reused, the current additive migration runs, and all application workloads roll back together. Ensure that old code remains compatible with the current database schema. A failed rollout keeps earlier deployment revisions available; inspect status before trying another revision.

### Restart and persistence check

Record a completed receipt, restart `deployment/worker` and `deployment/api`, then reload the same browser workspace. The receipt must remain and no second merchant order may appear. Tiger Cloud stores permissions, merchant records, price history, notifications, and worker heartbeat outside the cluster.

### Resource lifecycle

Two Optimized Cloud worker nodes and a load balancer are billable resources. The existing registry retains all commit-tagged images until you configure retention. The application does not create persistent Kubernetes storage volumes. After the event, remove unused infrastructure through Vultr; deleting workloads alone does not delete the VKE worker pool. Retain the Tiger Cloud database if you need receipts and price history.

## References

- [VKE provisioning](https://docs.vultr.com/products/compute/kubernetes/provisioning)
- [Vultr registry API credentials](https://docs.vultr.com/products/orchestration/container-registry/management/configurations/generate-docker-config)
- [Tiger Cloud connection details](https://www.tigerdata.com/docs/integrate/find-connection-details)
- [Grok structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs)

## Real-only catalog and Nessie evidence

The deployment now passes `NESSIE_API_KEY` from Actions secrets and runs `scripts/nessie_setup.py` before runtime configuration. With a valid v2 key, the script resolves a dedicated sandbox customer and bookkeeping merchant and passes their IDs through `GITHUB_ENV`. Optional repository variables `NESSIE_CUSTOMER_ID` and `NESSIE_MERCHANT_ID` select existing resources. There is no need to paste any credential into the website.

The catalog, Grok matches, and product picker contain real Keychron listings only. Sold-out or unverified variants cannot be requested. Normal watches hand off to the real merchant. The separate Nessie demonstration authorizes a clearly labeled sandbox total on the same real item and never claims a retailer order. Verify the bank account in Watchlist, then inspect the bank transaction reference and live ledger in Activity.

The 0003 migration repairs prerequisites for databases that previously deployed the original merchant-only 0002 revision. The 0004 migration adds availability and sandbox-mode fields and pauses retired fictional permissions without deleting receipts.
