"""Read-only Vultr API check. Load only VULTR_API_KEY; never execute .env or print keys."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def api_key(env_file):
    key = os.environ.get("VULTR_API_KEY", "").strip()
    if key:
        return key
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, sep, value = line.partition("=")
        if sep and name.strip() == "VULTR_API_KEY":
            parts = shlex.split(value, comments=True)
            if len(parts) != 1 or not parts[0].strip():
                raise ValueError("VULTR_API_KEY must contain one nonempty value")
            return parts[0].strip()
    raise ValueError("VULTR_API_KEY is missing or empty")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    args = parser.parse_args()
    try:
        key = api_key(args.env_file)
        request = Request("https://api.vultr.com/v2/kubernetes/clusters?per_page=100",
                          headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        with urlopen(request, timeout=30) as response:
            data = json.load(response)
            print(f"PASS: Vultr cluster API returned HTTP {response.status}")
        for cluster in data.get("vke_clusters", []):
            print(json.dumps({field: cluster.get(field) for field in ("id", "label", "region", "status")}))
        print("No resources were created or changed. This tests this machine's access, not the GitHub runner's.")
        return 0
    except HTTPError as exc:
        # Classify a provider error without echoing its potentially sensitive body.
        body = exc.read(65536).decode("utf-8", errors="replace").lower()
        print(f"FAIL: Vultr returned HTTP {exc.code}", file=sys.stderr)
        if "unauthorized ip" in body or "ip address is not authorized" in body:
            print("Vultr reports that the source IP is not authorized; check the API allowlist.", file=sys.stderr)
        elif exc.code == 401:
            print("Authentication rejected: check the API key, API enablement, and source-IP allowlist.", file=sys.stderr)
        return 1
    except URLError:
        print("FAIL: Could not connect to Vultr (DNS, network, or TLS error). No API authentication result.", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("FAIL: Could not load the API key or parse the response. Check the .env file and key format.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
