"""Deterministic acceptance fixtures for real SKUs, restricted to APP_ENV=test."""
from app.catalog import parse_product, SOURCES
from app.config import settings
from app.db import SessionLocal
from app.models import Product


def seed():
    if settings().app_env != "test":
        raise RuntimeError("Acceptance fixtures require APP_ENV=test")
    records = [parse_product({"title":"Keychron M3 Wireless Mouse", "variants":[{
        "id":44549379883097,"title":"Black / PixArt 3395 (4K Polling Rate) / No RGB Light", "price":5999,
        "available":True}]}, *SOURCES[1]), parse_product({"title":"Keychron C3 Pro QMK/VIA Wired Mechanical Keyboard", "variants":[{
        "id":40574854037593,"title":"Red Backlight / Keychron Mechanical Red Switch", "price":3499,
        "available":False}]}, *SOURCES[0])]
    with SessionLocal.begin() as db:
        for record in records:
            existing = db.get(Product, record["id"])
            if existing:
                for k,v in record.items(): setattr(existing,k,v)
            else: db.add(Product(**record))


if __name__ == "__main__":
    seed()
