import os
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.db import Base, engine, get_db
from app.main import app
from app.services import seed


@pytest.fixture(scope="session")
def factory():
    schema = "test_" + uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    test_engine = create_engine(str(engine.url.render_as_string(hide_password=False)),
                                connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(test_engine)
    with test_engine.connect() as conn:
        assert conn.scalar(text("SELECT current_schema()")) == schema
        assert conn.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_schema=:schema"),
                           {"schema": schema}) == len(Base.metadata.tables)
    yield sessionmaker(test_engine, expire_on_commit=False)
    test_engine.dispose()
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.fixture(autouse=True)
def clean_database(factory):
    with factory.begin() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        seed(db)


@pytest.fixture(autouse=True)
def simulated_payments(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings(), "app_env", "test")
    """Tests never touch a real bank, whatever PAYMENTS_MODE the container was started with."""
    from app import payments
    monkeypatch.setattr(payments, "provider", payments.SimulatedVirtualCardProvider())


@pytest.fixture
def db(factory):
    with factory() as db:
        yield db


@pytest.fixture
def client(factory):
    def dependency():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as client:
        client.get("/api/session")
        yield client
    app.dependency_overrides.clear()