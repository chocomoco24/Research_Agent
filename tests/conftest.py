"""Test setup. Env vars are set BEFORE the app is imported, so the app uses a
throwaway SQLite database and a fake API key. No Postgres or real key needed."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["GEMINI_API_KEY"] = "test-key"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)  # fresh tables for every test
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
