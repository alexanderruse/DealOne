"""Vultr provisioning and short-lived credential setup. Never print API responses containing secrets."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.vultr.com/v2"
REGISTRY_ID = os.getenv("VULTR_REGISTRY_ID", "369a6c53-fd50-4fe2-93d6-a151ec00c845")


def request(path, method="GET", data=None):
    key = os.environ.get("VULTR_API_KEY")
    if not key:
        raise RuntimeError("VULTR_API_KEY is required")
    req = Request(API + path, method=method, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                  data=json.dumps(data).encode() if data is not None else None)
    try:
        with urlopen(req, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        # Do not echo bodies: credentials and provider response details can be sensitive.
        raise RuntimeError(f"Vultr {method} {path.split('?')[0]} returned HTTP {exc.code}") from exc


def all_pages(path, field):
    result = []
    cursor = None
    while True:
        suffix = {"per_page": 100}
        if cursor:
            suffix["cursor"] = cursor
        body = request(path + ("&" if "?" in path else "?") + urlencode(suffix))
        result.extend(body.get(field, []))
        cursor = body.get("meta", {}).get("links", {}).get("next")
        if not cursor:
            return result


def select_plan(plans, available):
    candidates = [p for p in plans if p["id"] in available and
                  (p.get("type", "").startswith("voc") or p["id"].startswith("voc-")) and
                  p["vcpu_count"] >= 2 and p["ram"] >= 4096]
    if not candidates:
        raise RuntimeError("No supported Optimized Cloud plan with >=2 vCPU / 4 GB is available in ewr")
    return min(candidates, key=lambda p: (p["monthly_cost"], p["ram"], p["vcpu_count"]))


def provision(apply=False, selected_plan=None, budget=None):
    region = os.getenv("VULTR_REGION", "ewr")
    if region != "ewr":
        raise RuntimeError("This deployment is intentionally restricted to New York/New Jersey (ewr)")
    clusters = all_pages("/kubernetes/clusters", "vke_clusters")
    existing = [c for c in clusters if c["label"] == "dealone"]
    if len(existing) > 1:
        raise RuntimeError("Multiple dealone clusters exist; select VULTR_CLUSTER_ID explicitly")
    if existing:
        cluster = existing[0]
        if (cluster["region"] != region or sum(p["node_quantity"] for p in cluster["node_pools"]) != 2
                or not all(p["plan"].startswith("voc-") for p in cluster["node_pools"])):
            raise RuntimeError("Existing dealone cluster does not match region / two-node configuration")
        print(json.dumps({"existing_cluster_id": cluster["id"], "region": region}))
        output("cluster_id", cluster["id"])
        return
    plans = all_pages("/plans", "plans")
    available = request(f"/regions/{region}/availability").get("available_plans", [])
    plan = select_plan(plans, set(available))
    versions = request("/kubernetes/versions")["versions"]
    version = max(versions, key=lambda v: tuple(int(n) for n in re.findall(r"\d+", v)))
    quote = {"region": region, "plan": plan["id"], "nodes": 2, "vcpu_per_node": plan["vcpu_count"],
             "ram_mb_per_node": plan["ram"], "worker_monthly_usd": 2 * plan["monthly_cost"],
             "additional_costs": "One Vultr load balancer; existing registry subscription; Tiger Cloud", "version": version}
    print(json.dumps(quote, indent=2))
    if not apply:
        print("Read-only quote. Re-run with --apply --plan PLAN --max-worker-monthly-usd AMOUNT after reviewing costs.")
        return
    if selected_plan != plan["id"] or budget is None or quote["worker_monthly_usd"] > budget:
        raise RuntimeError("Explicit plan and sufficient worker cost ceiling are required; obtain a fresh quote first")
    body = {"label": "dealone", "region": region, "version": version, "enable_firewall": True,
            "node_pools": [{"label": "dealone-optimized", "plan": plan["id"], "node_quantity": 2, "auto_scaler": False}]}
    cluster = request("/kubernetes/clusters", "POST", body)["vke_cluster"]
    print("Created cluster:", cluster["id"])
    output("cluster_id", cluster["id"])


def output(name, value):
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write(f"{name}={value}\n")


def write_private(path, content):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    target.chmod(0o600)


def kubeconfig():
    cluster_id = os.environ["VULTR_CLUSTER_ID"]
    for attempt in range(60):
        cluster = request(f"/kubernetes/clusters/{cluster_id}")["vke_cluster"]
        if cluster["status"] == "active":
            break
        if attempt == 59:
            raise RuntimeError("Cluster not ready within 20 minutes")
        print("Waiting for cluster readiness…", flush=True)
        time.sleep(20)
    config = request(f"/kubernetes/clusters/{cluster_id}/config")["kube_config"]
    write_private(os.getenv("KUBECONFIG", "infra/generated/kubeconfig"), base64.b64decode(config).decode())
    print("Cluster credentials written to a private file")


def registry(write_access=False):
    info = request(f"/registry/{REGISTRY_ID}")
    info = info.get("registry", info)
    region = info["metadata"]["region"]
    base = region.get("base_url") or region["urn"]
    host = base.removeprefix("https://").removeprefix("http://").rstrip("/")
    # Use the provider-returned endpoint, never infer the registry region from the cluster.
    endpoint = os.getenv("VULTR_REGISTRY_ENDPOINT") or f"{host}/{info['name']}"
    endpoint = endpoint.removeprefix("https://").rstrip("/")
    credentials = request(f"/registry/{REGISTRY_ID}/docker-credentials?" + urlencode({
        "expiry_seconds": 3600 if write_access else 2592000, "read_write": str(write_access).lower()}), "OPTIONS")
    if isinstance(credentials, str):
        credentials = json.loads(credentials)
    if "auths" not in credentials:
        raise RuntimeError("Unexpected Docker credentials response; enable registry API credentials in Vultr")
    if write_access:
        write_private(Path(os.getenv("DOCKER_CONFIG", "infra/generated/docker")) / "config.json", json.dumps(credentials))
    else:
        import subprocess
        secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "vultr-cr-credentials", "namespace": "dealone"},
                  "type": "kubernetes.io/dockerconfigjson", "data": {".dockerconfigjson": base64.b64encode(json.dumps(credentials).encode()).decode()}}
        subprocess.run(["kubectl", "apply", "--server-side", "--field-manager=dealone-deploy", "-f", "-"], input=json.dumps(secret), text=True, check=True)
    output("endpoint", endpoint)
    print("Registry endpoint:", endpoint)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["quote", "provision", "kubeconfig", "registry-push", "registry-pull"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--plan")
    parser.add_argument("--max-worker-monthly-usd", type=float)
    args = parser.parse_args()
    try:
        if args.command in ("quote", "provision"):
            provision(args.command == "provision" and args.apply, args.plan, args.max_worker_monthly_usd)
        elif args.command == "kubeconfig":
            kubeconfig()
        else:
            registry(args.command == "registry-push")
    except (RuntimeError, KeyError) as exc:
        sys.exit(str(exc))
