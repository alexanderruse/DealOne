from .db import SessionLocal
from .services import seed

if __name__ == "__main__":
    with SessionLocal.begin() as db:
        seed(db)

    from .catalog import refresh_catalog
    refresh_catalog()
