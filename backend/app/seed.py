"""Populate only real merchant products. Fictional fixtures live exclusively in tests."""
from .catalog import refresh_catalog

if __name__ == "__main__":
    refresh_catalog()
