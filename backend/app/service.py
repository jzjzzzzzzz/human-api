from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from .config import Settings, get_settings
from .db import SessionLocal
from .errors import HumanApiError
from .models import ApiKey, Question, QuestionEvent, QuestionMessage, User
from .schemas import CompletionChoice, CompletionMessage, CompletionRequest, CompletionResponse
from .security import digest


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class HumanService:
    def __init__(
        self,
        settings: Settings | None = None,
        factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.factory = factory or SessionLocal

    async def authenticate_key(self, session: AsyncSession, authorization: str | None) -> ApiKey:
        if not authorization or not authorization.startswith("Bearer "):
            raise HumanApiError(
                401,
                "A Bearer API key is required.",
                "invalid_api_key",
                error_type="authentication_error",
            )
        raw = authorization[7:]
        if not raw or " " in raw or not raw.startswith("human_sk_"):
            raise HumanApiError(
                401, "The API key is invalid.", "invalid_api_key", error_type="authentication_error"
            )
        key = await session.scalar(
            select(ApiKey).where(
                ApiKey.key_digest == digest(raw, self.settings.human_api_key_pepper)
            )
        )
        if key is None:
            raise HumanApiError(
                401, "The API key is invalid.", "invalid_api_key", error_type="authentication_error"
            )
        if key.revoked_at is not None:
            raise HumanApiError(
                403,
                "The API key has been revoked.",
                "api_key_revoked",
                error_type="authentication_error",
            )
        if not key.enabled:
            raise HumanApiError(
                403,
                "The API key is disabled.",
                "api_key_disabled",
                error_type="authentication_error",
            )
        key.last_used_at = now()
        await session.commit()
        return key

    async def accepting(self, session: AsyncSession) -> bool:
        cutoff = now() - timedelta(seconds=self.settings.human_api_active_responder_seconds)
        active = int(
            await session.scalar(
                select(func.count(User.id)).where(
                    User.enabled.is_(True),
                    User.role.in_(["responder", "admin"]),
                    User.last_seen_at >= cutoff,
                )
            )
            or 0
        )
        if not self.settings.human_api_enabled:
            raise HumanApiError(
                503, "Human API is disabled.", "human_api_disabled", error_type="server_error"
            )
        if self.settings.human_api_require_active_responder and active == 0:
            raise HumanApiError(
                503,
                "No human responder is currently active.",
                "no_active_responder",
                error_type="server_error",
            )
        return True

    def validate_request(self, payload: CompletionRequest) -> list[tuple[str, str]]:
        if payload.model != self.settings.human_api_model_id:
            raise HumanApiError(
                404, f"Model '{payload.model}' does not exist.", "model_not_found", param="model"
            )
        if len(payload.messages) > self.settings.human_api_max_messages:
            raise HumanApiError(400, "Too many messages.", "too_many_messages", param="messages")
        normalized = [(message.role, message.normalized()) for message in payload.messages]
        if any(not content.strip() for _, content in normalized):
            raise HumanApiError(
                400, "Message content cannot be empty.", "invalid_message", param="messages"
            )
        if sum(len(content) for _, content in normalized) > self.settings.human_api_max_input_chars:
            raise HumanApiError(
                400,
                "Message content exceeds the configured limit.",
                "input_too_large",
                param="messages",
            )
        return normalized

    async def create_question(
        self, key: ApiKey, payload: CompletionRequest, idempotency_key: str | None
    ) -> Question:
        normalized = self.validate_request(payload)
        if idempotency_key is not None:
            idempotency_key = idempotency_key.strip()
            if not idempotency_key or len(idempotency_key) > 200:
                raise HumanApiError(
                    400,
                    "Invalid Idempotency-Key.",
                    "invalid_idempotency_key",
                    param="Idempotency-Key",
                )
        async with self.factory() as session:
            await self.accepting(session)
            if idempotency_key:
                existing = await session.scalar(
                    select(Question).where(
                        Question.api_key_id == key.id, Question.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    return existing
            minute_ago = now() - timedelta(minutes=1)
            count = int(
                await session.scalar(
                    select(func.count(Question.id)).where(
                        Question.api_key_id == key.id, Question.created_at >= minute_ago
                    )
                )
                or 0
            )
            if count >= key.rate_limit_per_minute:
                raise HumanApiError(
                    429,
                    "Rate limit exceeded.",
                    "rate_limit_exceeded",
                    error_type="rate_limit_error",
                )
            created = now()
            question = Question(
                id=str(uuid.uuid4()),
                completion_id=f"chatcmpl_{secrets.token_urlsafe(18)}",
                api_key_id=key.id,
                model=payload.model,
                status="pending",
                request_payload={
                    "model": payload.model,
                    "messages": [
                        {"role": role, "content": content} for role, content in normalized
                    ],
                },
                created_at=created,
                expires_at=created
                + timedelta(seconds=self.settings.human_response_timeout_seconds),
                idempotency_key=idempotency_key,
            )
            question.messages = [
                QuestionMessage(id=str(uuid.uuid4()), position=index, role=role, content=content)
                for index, (role, content) in enumerate(normalized)
            ]
            session.add(question)
            session.add(
                QuestionEvent(
                    id=str(uuid.uuid4()),
                    question_id=question.id,
                    event_type="created",
                    metadata_json={"completion_id": question.completion_id},
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if idempotency_key:
                    original = await session.scalar(
                        select(Question).where(
                            Question.api_key_id == key.id,
                            Question.idempotency_key == idempotency_key,
                        )
                    )
                    if original is not None:
                        return original
                raise
            return question

    async def wait_for_answer(
        self, question_id: str, request: Request | None = None
    ) -> CompletionResponse:
        while True:
            async with self.factory() as session:
                row = await session.get(Question, question_id)
                if row is None:
                    raise HumanApiError(
                        500,
                        "Question state was lost.",
                        "question_state_lost",
                        error_type="server_error",
                    )
                if row.status == "answered" and row.answer_content is not None:
                    return CompletionResponse(
                        id=row.completion_id,
                        created=int(aware(row.created_at).timestamp()),
                        model=row.model,
                        choices=[
                            CompletionChoice(message=CompletionMessage(content=row.answer_content))
                        ],
                    )
                if row.status == "cancelled":
                    raise HumanApiError(
                        503,
                        "Human response was cancelled.",
                        "human_response_cancelled",
                        error_type="server_error",
                    )
                if row.status == "expired" or aware(row.expires_at) <= now():
                    await session.execute(
                        update(Question)
                        .where(Question.id == row.id, Question.status.in_(["pending", "claimed"]))
                        .values(
                            status="expired",
                            error_code="human_response_timeout",
                            version=Question.version + 1,
                        )
                    )
                    await session.commit()
                    raise HumanApiError(
                        504,
                        "Human response timed out.",
                        "human_response_timeout",
                        error_type="server_error",
                    )
            if request is not None and await request.is_disconnected():
                async with self.factory() as session:
                    await session.execute(
                        update(Question)
                        .where(
                            Question.id == question_id, Question.status.in_(["pending", "claimed"])
                        )
                        .values(
                            status="cancelled",
                            cancelled_at=now(),
                            error_code="client_disconnected",
                            version=Question.version + 1,
                        )
                    )
                    await session.commit()
                raise asyncio.CancelledError
            await asyncio.sleep(0.5)

    async def reconcile(self, session: AsyncSession) -> None:
        current = now()
        await session.execute(
            update(Question)
            .where(
                Question.status == "claimed",
                Question.claim_expires_at <= current,
                Question.expires_at > current,
            )
            .values(
                status="pending",
                claimed_at=None,
                claimed_by_user_id=None,
                claim_expires_at=None,
                version=Question.version + 1,
            )
        )
        await session.execute(
            update(Question)
            .where(Question.status.in_(["pending", "claimed"]), Question.expires_at <= current)
            .values(
                status="expired", error_code="human_response_timeout", version=Question.version + 1
            )
        )
        await session.commit()

    async def question(self, session: AsyncSession, question_id: str) -> Question | None:
        return await session.scalar(
            select(Question)
            .options(selectinload(Question.messages))
            .where(Question.id == question_id)
        )
