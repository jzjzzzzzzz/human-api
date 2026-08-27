import asyncio

import httpx
import pytest
from conftest import TEST_KEY
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Question

AUTH = {"Authorization": f"Bearer {TEST_KEY}"}


@pytest.mark.anyio
async def test_models_require_key_and_advertise_only_human_model(client: httpx.AsyncClient):
    assert (await client.get("/v1/models")).status_code == 401
    response = await client.get("/v1/models", headers=AUTH)
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == ["human-1"]
    assert (await client.get("/v1/models/missing", headers=AUTH)).status_code == 404


@pytest.mark.anyio
async def test_unsupported_features_are_structured_errors(client: httpx.AsyncClient):
    for extra, code in [
        ({"stream": True}, "stream_not_supported"),
        ({"n": 2}, "n_not_supported"),
        ({"tools": []}, "tools_not_supported"),
    ]:
        response = await client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"model": "human-1", "messages": [{"role": "user", "content": "hello"}], **extra},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == code


@pytest.mark.anyio
async def test_complete_human_round_trip_and_server_owned_id(responder):
    client, csrf = responder
    headers = {**AUTH, "Idempotency-Key": "round-trip"}
    task = asyncio.create_task(
        client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "human-1",
                "messages": [
                    {"role": "system", "content": "Be concise."},
                    {"role": "user", "content": "Human integration test"},
                ],
            },
        )
    )
    question = None
    for _ in range(30):
        async with SessionLocal() as session:
            question = await session.scalar(
                select(Question).where(Question.idempotency_key == "round-trip")
            )
            if question:
                break
        await asyncio.sleep(0.05)
    assert question is not None
    visible_id = question.completion_id
    claim = await client.post(
        f"/api/human/questions/{question.id}/claim", headers={"X-CSRF-Token": csrf}
    )
    assert claim.status_code == 200
    assert claim.json()["completion_id"] == visible_id
    invalid = await client.post(
        f"/api/human/questions/{question.id}/answer",
        headers={"X-CSRF-Token": csrf},
        json={"id": "chatcmpl_attacker", "content": "invalid"},
    )
    assert invalid.status_code == 422
    accepted = await client.post(
        f"/api/human/questions/{question.id}/answer",
        headers={"X-CSRF-Token": csrf},
        json={"content": "HUMAN_E2E_RESPONSE_OK"},
    )
    assert accepted.status_code == 200
    response = await asyncio.wait_for(task, 5)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == visible_id
    assert body["object"] == "chat.completion"
    assert body["model"] == "human-1"
    assert body["choices"][0]["message"] == {
        "role": "assistant",
        "content": "HUMAN_E2E_RESPONSE_OK",
    }
    assert body["choices"][0]["finish_reason"] == "stop"
    async with SessionLocal() as session:
        stored = await session.get(Question, question.id)
        assert stored.status == "answered"
        assert stored.answer_payload == {"content": "HUMAN_E2E_RESPONSE_OK"}


@pytest.mark.anyio
async def test_idempotency_key_creates_one_question(responder):
    client, csrf = responder
    payload = {"model": "human-1", "messages": [{"role": "user", "content": "same request"}]}
    first = asyncio.create_task(
        client.post(
            "/v1/chat/completions", headers={**AUTH, "Idempotency-Key": "same"}, json=payload
        )
    )
    second = asyncio.create_task(
        client.post(
            "/v1/chat/completions", headers={**AUTH, "Idempotency-Key": "same"}, json=payload
        )
    )
    question = None
    for _ in range(40):
        async with SessionLocal() as session:
            rows = list(
                (
                    await session.scalars(
                        select(Question).where(Question.idempotency_key == "same")
                    )
                ).all()
            )
            if rows:
                question = rows[0]
                break
        await asyncio.sleep(0.05)
    assert question
    claim = await client.post(
        f"/api/human/questions/{question.id}/claim", headers={"X-CSRF-Token": csrf}
    )
    assert claim.status_code == 200
    answer = await client.post(
        f"/api/human/questions/{question.id}/answer",
        headers={"X-CSRF-Token": csrf},
        json={"content": "one"},
    )
    assert answer.status_code == 200
    r1, r2 = await asyncio.gather(first, second)
    assert r1.json()["id"] == r2.json()["id"] == question.completion_id
    async with SessionLocal() as session:
        assert (
            len(
                list(
                    (
                        await session.scalars(
                            select(Question).where(Question.idempotency_key == "same")
                        )
                    ).all()
                )
            )
            == 1
        )
