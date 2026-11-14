"""Synchronize only dealone.work's apex website records with the VKE ingress IPv4."""
import argparse
import ipaddress
import json
import os
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DOMAIN = "dealone.work"
API = "https://api.porkbun.com/api/json/v3"
WEBSITE_TYPES = {"A", "AAAA", "ALIAS", "CNAME"}


def ipv4(value):
    address = ipaddress.ip_address(value)
    if address.version != 4 or not address.is_global:
        raise ValueError("A public load-balancer IPv4 address is required")
    return str(address)


def load_balancer_ip(timeout=600):
    deadline = time.monotonic() + timeout
    while True:
        result = subprocess.run(
            ["kubectl", "-n", "traefik", "get", "service", "traefik", "-o", "json", "--request-timeout=20s"],
            capture_output=True, text=True, check=True,
        )
        ingress = json.loads(result.stdout).get("status", {}).get("loadBalancer", {}).get("ingress", [])
        for entry in ingress:
            if entry.get("ip"):
                try:
                    return ipv4(entry["ip"])
                except ValueError:
                    continue
        if time.monotonic() >= deadline:
            raise RuntimeError("Traefik did not receive a public IPv4 address within the timeout")
        print("Waiting for Traefik load-balancer IPv4…", flush=True)
        time.sleep(10)


def check_nameservers():
    answer = subprocess.run(["dig", "+short", "NS", DOMAIN], capture_output=True, text=True, check=True)
    names = [name.lower().rstrip(".") for name in answer.stdout.split()]
    if not names or not all(name.endswith(".porkbun.com") for name in names):
        raise RuntimeError("dealone.work is not delegated to Porkbun; no DNS changes were made")


def request(path, body=None):
    headers = {"Content-Type": "application/json", "X-API-Key": os.environ["PORKBUN_API_KEY"],
               "X-Secret-API-Key": os.environ["PORKBUN_SECRET_API_KEY"]}
    req = Request(API + path, method="POST", headers=headers, data=json.dumps(body or {}).encode())
    try:
        with urlopen(req, timeout=30) as response:
            data = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Porkbun API returned HTTP {exc.code}; check domain access and permissions") from None
    except URLError:
        raise RuntimeError("Porkbun API connection failed; re-run synchronization to reconcile DNS") from None
    if data.get("status") != "SUCCESS":
        # Never echo arbitrary provider errors or request bodies that could contain credentials.
        raise RuntimeError("Porkbun rejected the request; check DNS permissions and per-domain API access")
    if data.get("warnings"):
        raise RuntimeError("Porkbun reported a DNS warning; verify authoritative nameservers before continuing")
    return data


def plan(records, address):
    address = ipv4(address)
    roots = [r for r in records if r.get("name", "").lower().rstrip(".") == DOMAIN
             and r.get("type") in WEBSITE_TYPES]
    # Convert an existing apex ALIAS/CNAME in place, avoiding delete-before-create downtime.
    candidates = sorted((r for r in roots if r["type"] in {"A", "ALIAS", "CNAME"}),
                        key=lambda r: (r["type"] != "A", str(r["id"])))
    keeper = candidates[0] if candidates else None
    target = {"name": "", "type": "A", "content": address, "ttl": 600}
    changes = []
    if not keeper:
        changes.append((f"/dns/create/{DOMAIN}", target))
    elif keeper["type"] != "A" or keeper.get("content") != address:
        changes.append((f"/dns/edit/{DOMAIN}/{keeper['id']}", target))
    for record in roots:
        if keeper and str(record["id"]) == str(keeper["id"]):
            continue
        changes.append((f"/dns/delete/{DOMAIN}/{record['id']}", {}))
    return changes


def synchronize(address, apply=False):
    address = ipv4(address)
    key = bool(os.getenv("PORKBUN_API_KEY"))
    secret = bool(os.getenv("PORKBUN_SECRET_API_KEY"))
    if key != secret:
        raise RuntimeError("Provide both PORKBUN_API_KEY and PORKBUN_SECRET_API_KEY")
    if not key:
        message = f"Porkbun credentials absent: manually set {DOMAIN} root A record to {address}."
        print(message)
        summary(message)
        return
    check_nameservers()
    records = request(f"/dns/retrieve/{DOMAIN}")["records"]
    changes = plan(records, address)
    print(json.dumps({"domain": DOMAIN, "target_ipv4": address, "apply": apply,
                      "changes": [{"endpoint": endpoint, "fields": fields} for endpoint, fields in changes]}, indent=2))
    if not apply:
        print("Read-only DNS plan. Use --apply to perform these changes.")
        return
    for endpoint, fields in changes:
        request(endpoint, fields)
    after = request(f"/dns/retrieve/{DOMAIN}")["records"]
    if plan(after, address):
        raise RuntimeError("Porkbun records do not yet match the target; re-run DNS synchronization")
    summary(f"Porkbun DNS synchronized: {DOMAIN} → {address}. Public resolvers may still cache the previous address.")
    print(f"Porkbun authoritative record verified: {DOMAIN} → {address}")


def summary(message):
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
            stream.write(message + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", help="Use this public IPv4 instead of querying Traefik")
    parser.add_argument("--apply", action="store_true", help="Apply the displayed root-record changes")
    args = parser.parse_args()
    try:
        synchronize(args.ip or load_balancer_ip(), args.apply)
    except (RuntimeError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
