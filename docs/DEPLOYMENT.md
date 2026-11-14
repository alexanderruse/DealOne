# Deployment to dealone.work

## 1. Add GitHub configuration

Open [repository Actions secrets](https://github.com/alexanderruse/DealOne/settings/secrets/actions). Keep these values out of chat, commits, and command-line arguments.

| Secret | Required value |
| --- | --- |
| `VULTR_API_KEY` | Vultr API credential authorized for VKE, plan/region discovery, and existing registry credentials |
| `DATABASE_URL` | Tiger Cloud SQLAlchemy URL: `postgresql+psycopg://USER:PASSWORD@HOST:PORT/tsdb?sslmode=require` |
| `XAI_API_KEY` | Sponsor-provided Grok API key |
| `SESSION_SECRET` | A unique random value of at least 32 characters |
| `DEMO_ADMIN_TOKEN` | A different unique random value of at least 24 characters |
| `RESEND_API_KEY` | Optional; reserved for future email opt-in support |

URL-encode special characters in database usernames/passwords. Use a dedicated database/schema and a migration-capable Tiger Cloud user; the initial migration creates tables and enables TimescaleDB if needed. The app refuses non-TLS production database connections. Use `sslmode=verify-full` with your provider's CA configuration when available.

Generate independent secrets using `openssl rand -hex 32`. Store each generated value directly in GitHub; do not use the local `.env.example` defaults in production. Keep `SESSION_SECRET` stable between deploys so sessions and worker authentication continue to work.

Create these repository **Variables**:

| Variable | Value |
| --- | --- |
| `ACME_EMAIL` | Team email for Let's Encrypt registration |
| `XAI_MODEL` | Exact enabled model ID from your sponsor account; must support structured outputs and function calling |
| `VULTR_CLUSTER_ID` | Add the UUID printed by the provisioning workflow after cluster creation |
| `VULTR_REGISTRY_ENDPOINT` | Optional override; otherwise discovered from Vultr metadata, including the registry namespace |
| `RESEND_FROM_EMAIL` | Optional; reserved for future email delivery |

Domain `dealone.work`, region `ewr`, and registry UUID `369a6c53-fd50-4fe2-93d6-a151ec00c845` are already set in deployment code. `GITHUB_TOKEN` is supplied by GitHub. Kubeconfig, registry username, and registry password are obtained by the workflow; separate repository secrets are not needed.

Vultr API access rules must allow the machine executing the workflow. If your API key is restricted to one fixed IP, use an appropriately secured self-hosted runner or configure an allowed runner egress path. Do not silently disable your account's access controls.

## 2. Quote and provision infrastructure

In **Actions → Provision Vultr infrastructure**, run with operation **quote**. It is read-only and selects the least expensive available Optimized Cloud plan in `ewr` with at least 2 vCPUs and 4 GB RAM per node. It prints the plan, version, and combined monthly worker cost. Load balancer, registry, and Tiger Cloud charges are additional.

After reviewing the quote, run again with operation **provision**, the exact plan ID, and a worker monthly cost ceiling. The script refuses to create a cluster if the plan or cost does not match. It reuses a uniquely named `dealone` cluster instead of creating duplicates. If that region/type is unavailable, it stops rather than changing your requested infrastructure.

The public catalog checked on November 14, 2026 lists `voc-c-2c-4gb-50s-amd` as available in `ewr` at **$40/month per node**, or **$80/month for two workers**, excluding other services. The workflow always rechecks availability and price; this recorded quote does not reserve capacity.

The workflow waits for the cluster, installs pinned Traefik and cert-manager charts, and prints the cluster ID and load-balancer service. Set `VULTR_CLUSTER_ID` in repository Variables. If creation succeeded but ingress installation failed, rerun provisioning to reuse the cluster and finish setup.

The load balancer appears under namespace `traefik`, service `traefik`. Set your registrar's **A record for `@` / `dealone.work`** to its external IP. Remove stale A/AAAA records pointing elsewhere. Keep ports 80 and 443 reachable; cert-manager uses HTTP-01 validation. No `www` hostname is configured.

For local tooling instead of Actions:

```sh
python3 scripts/vultr.py quote
python3 scripts/vultr.py provision --apply --plan YOUR_REVIEWED_PLAN --max-worker-monthly-usd YOUR_CEILING
```

Export `VULTR_API_KEY` securely first. For bootstrap set `VULTR_CLUSTER_ID`, `KUBECONFIG`, and `ACME_EMAIL`, run `python3 scripts/vultr.py kubeconfig`, then `python3 scripts/bootstrap.py`. Requires `kubectl` and Helm 3.

## 3. Deploy application

Run **Actions → Deploy DealOne** with a blank image tag. It validates runtime configuration; discovers the existing registry; gets expiring push credentials; builds Linux AMD64 API, worker, and web images tagged with the commit SHA; retrieves kubeconfig; refreshes read-only pull credentials; runs migrations and seed data as a Job; and rolls out the application.

Subsequent successful main-branch push checks deploy automatically. Provisioning and application deployment share a concurrency group, preventing overlapping production mutations. Workflows never print registry passwords, API keys, connection strings, or generated kubeconfig. Credentials are removed from the runner at completion.

Vultr's registry credential endpoint used here is the documented registry API `OPTIONS /v2/registry/{id}/docker-credentials` with read/write and expiry parameters. Your API identity must have access to this registry. The pull token expires after 30 days and is refreshed by a weekly workflow, every application deployment, or a manual **Refresh registry pull credentials** run. Monitor failed scheduled runs; private-repo Actions minutes and provider access still apply.

The migration job must succeed before the workload rollout. New deployment configuration uses Kubernetes Secrets and a ConfigMap. Use additive migrations for future releases: reverting images does not revert schema or credentials.

## 4. Verify and operate

```sh
kubectl -n dealone get pods,ingress,certificate
kubectl -n dealone logs deployment/worker --tail=50
kubectl -n dealone rollout status deployment/api
curl --fail https://dealone.work/api/health/ready
curl --fail https://dealone.work/api/health/worker
```

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
