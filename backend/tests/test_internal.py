import asyncio
import uuid

import httpx
import pytest
from conftest import TEST_KEY, TEST_PASSWORD
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Question, User

AUTH = {"Authorization": f"Bearer {TEST_KEY}"}


@pytest.mark.anyio
async def test_queue_requires_authenticated_responder(client: httpx.AsyncClient):
    assert (await client.get("/api/human/questions")).status_code == 401


@pytest.mark.anyio
async def test_two_responders_cannot_claim_same_question(client: httpx.AsyncClient, responder):
    first_client, first_csrf = responder
    request_task = asyncio.create_task(
        first_client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "human-1", "messages": [{"role": "user", "content": "race"}]},
        )
    )
    question = None
    for _ in range(30):
        async with SessionLocal() as session:
            question = await session.scalar(select(Question))
            if question:
                break
        await asyncio.sleep(0.05)
    assert question
    async with SessionLocal() as session:
        second = User(
            id=str(uuid.uuid4()),
            email="second@example.test",
            password_hash=__import__("app.security", fromlist=["password_hash"]).password_hash(
                TEST_PASSWORD
            ),
            role="responder",
            enabled=True,
        )
        session.add(second)
        await session.commit()
    transport = httpx.ASGITransport(app=__import__("app.main", fromlist=["app"]).app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as other:
        login = await other.post(
            "/api/auth/login", json={"email": "second@example.test", "password": TEST_PASSWORD}
        )
        csrf2 = login.json()["csrf_token"]
        results = await asyncio.gather(
            first_client.post(
                f"/api/human/questions/{question.id}/claim", headers={"X-CSRF-Token": first_csrf}
            ),
            other.post(
                f"/api/human/questions/{question.id}/claim", headers={"X-CSRF-Token": csrf2}
            ),
        )
        assert sorted(item.status_code for item in results) == [200, 409]
        winner = first_client if results[0].status_code == 200 else other
        token = first_csrf if results[0].status_code == 200 else csrf2
        accepted = await winner.post(
            f"/api/human/questions/{question.id}/answer",
            headers={"X-CSRF-Token": token},
            json={"content": "winner"},
        )
        assert accepted.status_code == 200
    response = await request_task
    assert response.json()["choices"][0]["message"]["content"] == "winner"


@pytest.mark.anyio
async def test_answer_body_rejects_protocol_fields(responder):
    client, csrf = responder
    for field in ("id", "model", "finish_reason"):
        response = await client.post(
            "/api/human/questions/anything/answer",
            headers={"X-CSRF-Token": csrf},
            json={"content": "answer", field: "attacker"},
        )
        assert response.status_code == 422
