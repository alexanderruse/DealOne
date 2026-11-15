"""Resolve sandbox resources using the existing Actions secret. Never prints credentials."""
import json
import os
import urllib.request
import urllib.error

BASE = "https://v2-prod-api.nessieisreal.com"


def setup():
    key = os.getenv("NESSIE_API_KEY", "")
    if not key:
        print("Nessie key absent: local simulation remains clearly labeled.")
        return

    def request(path, body=None):
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Nessie sandbox returned HTTP {exc.code}; verify NESSIE_API_KEY for the v2 API") from None

    def items(value):
        return value if isinstance(value, list) else value.get("items", [])

    customer = os.getenv("NESSIE_CUSTOMER_ID", "")
    if not customer:
        rows = items(request("/customers?limit=100"))
        own = next((v for v in rows if v.get("first_name") == "DealOne"), None)
        if own is None:
            own = request("/customers", {"first_name":"DealOne", "last_name":"Sandbox", "address":{
                "street_number":"1", "street_name":"Hackathon Way", "city":"Amherst", "state":"MA", "zip":"01002"}})
        customer = str(own["id"])
    else:
        request("/customers/" + customer)
    merchant = os.getenv("NESSIE_MERCHANT_ID", "")
    if not merchant:
        rows = items(request("/merchants?limit=100"))
        own = next((v for v in rows if v.get("name") == "DealOne Sandbox Checkout"), None)
        if own is None:
            own = request("/merchants", {"name":"DealOne Sandbox Checkout", "category":"electronics"})
        merchant = str(own["id"])
    else:
        request("/merchants/" + merchant)
    env_file = os.getenv("GITHUB_ENV")
    if env_file:
        with open(env_file, "a") as output:
            output.write(f"NESSIE_CUSTOMER_ID={customer}\nNESSIE_MERCHANT_ID={merchant}\n")
    print("Nessie v2 authenticated; dedicated sandbox customer and merchant are ready.")


if __name__ == "__main__":
    setup()
