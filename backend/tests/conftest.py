import os
import uuid
from datetime import UTC, datetime

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:////tmp/human-api-public-test.db"
os.environ["HUMAN_API_KEY_PEPPER"] = "test-key-pepper-with-at-least-32-characters"
os.environ["SESSION_TOKEN_PEPPER"] = "test-session-pepper-with-at-least-32-characters"
os.environ["HUMAN_RESPONSE_TIMEOUT_SECONDS"] = "10"
os.environ["HUMAN_API_CLAIM_LEASE_SECONDS"] = "30"

import httpx
import pytest
import pytest_asyncio

from app.db import SessionLocal, engine
from app.main import app
from app.models import ApiKey, Base, User
from app.security import digest, password_hash

TEST_KEY = "human_sk_public_test_key_not_a_secret"
TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def database():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        admin = User(
            id=str(uuid.uuid4()),
            email="admin@example.test",
            password_hash=password_hash(TEST_PASSWORD),
            role="admin",
            enabled=True,
            last_seen_at=datetime.now(UTC),
        )
        responder = User(
            id=str(uuid.uuid4()),
            email="responder@example.test",
            password_hash=password_hash(TEST_PASSWORD),
            role="responder",
            enabled=True,
            last_seen_at=datetime.now(UTC),
        )
        session.add_all([admin, responder])
        await session.flush()
        session.add(
            ApiKey(
                id=str(uuid.uuid4()),
                name="Test key",
                key_prefix=TEST_KEY[:20],
                key_digest=digest(TEST_KEY, os.environ["HUMAN_API_KEY_PEPPER"]),
                enabled=True,
                created_by_user_id=admin.id,
                rate_limit_per_minute=20,
            )
        )
        await session.commit()
    yield


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


@pytest_asyncio.fixture
async def responder(client: httpx.AsyncClient):
    response = await client.post(
        "/api/auth/login", json={"email": "responder@example.test", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200
    return client, response.json()["csrf_token"]
