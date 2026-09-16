import pytest
from docflow_core.config import get_settings
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def database_available() -> bool:
    return bool(get_settings().database_url)


requires_database = pytest.mark.skipif(
    not database_available(),
    reason=(
        "DATABASE_URL is not set -- these tests need the real docflow-staging Postgres "
        "connection to prove RLS/tenant-isolation behavior for real, not a mock. "
        "See SETUP.md Step 1. Per CLAUDE.md Section 0 rule 5, we never fake this to keep moving."
    ),
)
