"""Deploy an immutable image revision. Secrets travel via stdin and are never printed."""
import argparse
import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def apply(resource):
    run("kubectl", "apply", "--server-side", "--field-manager=dealone-deploy", "-f", "-", input=json.dumps(resource))


def resource(kind, name, data):
    return {"apiVersion": "v1", "kind": kind, "metadata": {"name": name, "namespace": "dealone"}, **data}


def runtime_config():
    required = ["DATABASE_URL", "SESSION_SECRET", "DEMO_ADMIN_TOKEN", "XAI_API_KEY"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing secrets: " + ", ".join(missing))
    if len(os.environ["SESSION_SECRET"]) < 32 or len(os.environ["DEMO_ADMIN_TOKEN"]) < 24:
        raise RuntimeError("SESSION_SECRET must be >=32 chars; DEMO_ADMIN_TOKEN >=24 chars")
    db_url = os.environ["DATABASE_URL"]
    if not db_url.startswith("postgresql+psycopg://"):
        raise RuntimeError("DATABASE_URL must start with postgresql+psycopg:// for SQLAlchemy/Psycopg 3")
    if not any(f"sslmode={mode}" in db_url for mode in ("require", "verify-ca", "verify-full")):
        raise RuntimeError("DATABASE_URL must require TLS")
    if not os.getenv("XAI_MODEL"):
        raise RuntimeError("Set XAI_MODEL to the sponsor-enabled model identifier")
    secrets = {k: os.getenv(k, "") for k in required + ["RESEND_API_KEY", "RESEND_FROM_EMAIL", "NESSIE_API_KEY"]}
    # Nessie sandbox payments switch on only when both the key and the card account are supplied.
    nessie = bool(os.getenv("NESSIE_API_KEY") and os.getenv("NESSIE_CARD_ACCOUNT_ID"))
    config = {"APP_ENV": "production", "APP_DOMAIN": os.getenv("APP_DOMAIN", "dealone.work"),
              "XAI_MODEL": os.environ["XAI_MODEL"], "PAYMENTS_MODE": "nessie" if nessie else "simulation",
              "NESSIE_CARD_ACCOUNT_ID": os.getenv("NESSIE_CARD_ACCOUNT_ID", ""),
              "NESSIE_MERCHANT_ID": os.getenv("NESSIE_MERCHANT_ID", ""),
              "STOREFRONT_URL": "http://api:8000", "WORKER_INTERVAL_SECONDS": "10"}
    return secrets, config


def deploy(endpoint, tag):
    if not endpoint or not tag or any(c.isspace() for c in endpoint + tag):
        raise RuntimeError("Supply the registry endpoint and immutable image tag")
    secret_data, config_data = runtime_config()
    run("kubectl", "apply", "-f", str(ROOT / "infra/k8s/namespace.yaml"))
    apply(resource("Secret", "dealone-runtime", {"type": "Opaque", "data": {
        name: base64.b64encode(value.encode()).decode() for name, value in secret_data.items()}}))
    apply(resource("ConfigMap", "dealone-config", {"data": config_data}))
    # Image-pull credentials must already exist (see vultr.py registry-pull).
    image = lambda component: f"{endpoint.rstrip('/')}/dealone-{component}:{tag}"
    job_name = "migrate-" + hashlib.sha256(tag.encode()).hexdigest()[:12]
    job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": job_name, "namespace": "dealone"},
           "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 300, "ttlSecondsAfterFinished": 86400,
                    "template": {"spec": {"restartPolicy": "Never", "automountServiceAccountToken": False,
                                           "imagePullSecrets": [{"name": "vultr-cr-credentials"}],
                                           "containers": [{"name": "migrate", "image": image("api"),
                                                           "command": ["sh", "-c", "alembic upgrade head && python -m app.seed"],
                                                           "envFrom": [{"configMapRef": {"name": "dealone-config"}},
                                                                       {"secretRef": {"name": "dealone-runtime"}}]}]}}}}
    # Re-running the same revision can retry a failed migration job safely.
    run("kubectl", "delete", "job", job_name, "-n", "dealone", "--ignore-not-found=true")
    apply(job)
    try:
        run("kubectl", "wait", "--for=condition=complete", f"job/{job_name}", "-n", "dealone", "--timeout=310s")
    except subprocess.CalledProcessError:
        run("kubectl", "logs", f"job/{job_name}", "-n", "dealone")
        raise
    yaml = run("kubectl", "kustomize", str(ROOT / "infra/k8s"), capture_output=True).stdout
    for component in ("api", "web", "worker"):
        yaml = yaml.replace(f"dealone-{component}:placeholder", image(component))
    yaml = yaml.replace("dealone.work", config_data["APP_DOMAIN"])
    run("kubectl", "apply", "-f", "-", input=yaml)
    # Restart existing pods even when only credentials/config changed.
    run("kubectl", "rollout", "restart", "deployment/api", "deployment/web", "deployment/worker", "-n", "dealone")
    for component in ("api", "web", "worker"):
        run("kubectl", "rollout", "status", f"deployment/{component}", "-n", "dealone", "--timeout=300s")
    print("Application rolled out. Verify DNS and certificate readiness before running the public smoke check.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    deploy(args.endpoint, args.tag)
