"""Install cluster ingress and certificate infrastructure; run once after provisioning."""
import json
import os
import subprocess


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


if __name__ == "__main__":
    email = os.environ["ACME_EMAIL"]
    run("helm", "repo", "add", "traefik", "https://traefik.github.io/charts")
    run("helm", "repo", "add", "jetstack", "https://charts.jetstack.io")
    run("helm", "repo", "update")
    run("helm", "upgrade", "--install", "traefik", "traefik/traefik", "--namespace", "traefik", "--create-namespace",
        "--version", "37.4.0", "--set", "deployment.replicas=2", "--set", "service.type=LoadBalancer",
        "--set", "ports.web.redirections.entryPoint.to=websecure", "--set", "ports.web.redirections.entryPoint.scheme=https",
        "--set", "ports.web.redirections.entryPoint.priority=1", "--wait", "--timeout", "10m")
    # Low-priority redirect lets HTTP-01 challenge routes take precedence.
    run("helm", "upgrade", "--install", "cert-manager", "jetstack/cert-manager", "--namespace", "cert-manager",
        "--create-namespace", "--version", "v1.19.1", "--set", "crds.enabled=true", "--wait", "--timeout", "10m")
    issuer = {"apiVersion": "cert-manager.io/v1", "kind": "ClusterIssuer", "metadata": {"name": "letsencrypt-prod"},
              "spec": {"acme": {"email": email, "server": "https://acme-v02.api.letsencrypt.org/directory",
                                "privateKeySecretRef": {"name": "letsencrypt-prod-account"},
                                "solvers": [{"http01": {"ingress": {"ingressClassName": "traefik"}}}]}}}
    run("kubectl", "apply", "-f", "-", input=json.dumps(issuer))
    run("kubectl", "get", "service", "traefik", "-n", "traefik")
    print("Point the dealone.work DNS A record to the EXTERNAL-IP shown above. Remove conflicting AAAA records.")
