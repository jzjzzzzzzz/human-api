from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import Settings, get_settings
from .db import SessionLocal
from .errors import HumanApiError
from .models import ApiKey, BrowserSession, Question, QuestionEvent, User
from .schemas import AnswerRequest, ApiKeyCreate, ApiKeyUpdate, CompletionRequest, LoginRequest
from .security import (
    admin_session,
    current_session,
    digest,
    new_browser_session,
    password_valid,
    session_scope,
    state_session,
)
from .service import HumanService, now

app = FastAPI(title="Human API", version="1.0.0")


@app.exception_handler(HumanApiError)
async def human_error_handler(_request: Request, exc: HumanApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.body())


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path.startswith("/v1/"):
        messages = " ".join(
            str(item.get("ctx", {}).get("error", item.get("msg", "Invalid request")))
            for item in exc.errors()
        )
        known = next(
            (
                code
                for code in (
                    "stream_not_supported",
                    "n_not_supported",
                    "tools_not_supported",
                    "functions_not_supported",
                    "response_format_not_supported",
                    "messages_empty",
                )
                if code in messages
            ),
            "invalid_request",
        )
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": messages or "Invalid request.",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": known,
                }
            },
        )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/ready")
async def ready(db: AsyncSession = Depends(session_scope)) -> dict[str, str]:
    await db.execute(select(1))
    return {"status": "ready"}


async def external_key(authorization: str | None = Header(default=None)) -> ApiKey:
    async with SessionLocal() as session:
        return await HumanService().authenticate_key(session, authorization)


@app.get("/v1/models")
async def models(
    _key: ApiKey = Depends(external_key), settings: Settings = Depends(get_settings)
) -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": settings.human_api_model_id,
                "object": "model",
                "created": 1780000000,
                "owned_by": "human",
            }
        ],
    }


@app.get("/v1/models/{model_id}")
async def model(
    model_id: str, _key: ApiKey = Depends(external_key), settings: Settings = Depends(get_settings)
) -> dict:
    if model_id != settings.human_api_model_id:
        raise HumanApiError(
            404, f"Model '{model_id}' does not exist.", "model_not_found", param="model"
        )
    return {"id": model_id, "object": "model", "created": 1780000000, "owned_by": "human"}


@app.post("/v1/chat/completions")
async def completions(
    payload: CompletionRequest,
    request: Request,
    key: ApiKey = Depends(external_key),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    service = HumanService()
    question = await service.create_question(key, payload, idempotency_key)
    response = await service.wait_for_answer(question.id, request)
    return JSONResponse(
        content=response.model_dump(),
        headers={"X-Answer-Origin": "human", "Cache-Control": "no-store"},
    )


@app.post("/api/auth/login")
async def login(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> dict:
    user = await db.scalar(select(User).where(User.email == payload.email.strip().lower()))
    if user is None or not user.enabled or not password_valid(user.password_hash, payload.password):
        raise HumanApiError(401, "Email or password is incorrect.", "invalid_credentials")
    record, raw = new_browser_session(user.id, settings)
    db.add(record)
    await db.commit()
    response.set_cookie(
        "human_session",
        raw,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        max_age=43200,
        path="/",
    )
    return {
        "user": {"id": user.id, "email": user.email, "role": user.role},
        "csrf_token": record.csrf_token,
    }


@app.get("/api/auth/me")
async def me(record: BrowserSession = Depends(current_session)) -> dict:
    return {
        "user": {"id": record.user.id, "email": record.user.email, "role": record.user.role},
        "csrf_token": record.csrf_token,
    }


@app.post("/api/auth/logout")
async def logout(
    response: Response,
    record: BrowserSession = Depends(state_session),
    db: AsyncSession = Depends(session_scope),
) -> dict:
    await db.execute(delete(BrowserSession).where(BrowserSession.id == record.id))
    await db.commit()
    response.delete_cookie("human_session", path="/")
    return {"ok": True}


def question_json(row: Question, viewer: User) -> dict:
    return {
        "id": row.id,
        "completion_id": row.completion_id,
        "model": row.model,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "claim_expires_at": row.claim_expires_at.isoformat() if row.claim_expires_at else None,
        "is_mine": row.claimed_by_user_id == viewer.id,
        "messages": [
            {"position": m.position, "role": m.role, "content": m.content} for m in row.messages
        ],
        "answer_content": row.answer_content if row.status == "answered" else None,
    }


@app.get("/api/human/questions")
async def questions(
    scope: str = "available",
    record: BrowserSession = Depends(current_session),
    db: AsyncSession = Depends(session_scope),
) -> dict:
    service = HumanService()
    await service.reconcile(db)
    query = (
        select(Question)
        .options(selectinload(Question.messages))
        .order_by(Question.created_at.desc())
        .limit(100)
    )
    if scope == "available":
        query = query.where(Question.status == "pending")
    elif scope == "mine":
        query = query.where(
            Question.status == "claimed", Question.claimed_by_user_id == record.user.id
        )
    elif scope == "answered":
        query = query.where(Question.status == "answered")
    elif scope == "expired":
        query = query.where(Question.status == "expired")
    elif scope == "all" and record.user.role == "admin":
        pass
    else:
        raise HumanApiError(400, "Invalid queue scope.", "invalid_scope")
    rows = list((await db.scalars(query)).all())
    return {"data": [question_json(row, record.user) for row in rows]}


@app.get("/api/human/questions/{question_id}")
async def question_detail(
    question_id: str,
    record: BrowserSession = Depends(current_session),
    db: AsyncSession = Depends(session_scope),
) -> dict:
    row = await HumanService().question(db, question_id)
    if row is None:
        raise HumanApiError(404, "Question not found.", "question_not_found")
    return question_json(row, record.user)


@app.post("/api/human/questions/{question_id}/claim")
async def claim(
    question_id: str,
    record: BrowserSession = Depends(state_session),
    db: AsyncSession = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> dict:
    await HumanService().reconcile(db)
    current = now()
    result = await db.execute(
        update(Question)
        .where(
            Question.id == question_id, Question.status == "pending", Question.expires_at > current
        )
        .values(
            status="claimed",
            claimed_by_user_id=record.user.id,
            claimed_at=current,
            claim_expires_at=current + timedelta(seconds=settings.human_api_claim_lease_seconds),
            version=Question.version + 1,
        )
        .returning(Question.id)
    )
    if result.scalar_one_or_none() is None:
        await db.rollback()
        raise HumanApiError(409, "Question is no longer available.", "claim_conflict")
    db.add(
        QuestionEvent(
            id=str(uuid.uuid4()),
            question_id=question_id,
            event_type="claimed",
            actor_user_id=record.user.id,
            metadata_json={},
        )
    )
    await db.commit()
    row = await HumanService().question(db, question_id)
    return question_json(row, record.user)


@app.post("/api/human/questions/{question_id}/release")
async def release(
    question_id: str,
    record: BrowserSession = Depends(state_session),
    db: AsyncSession = Depends(session_scope),
) -> dict:
    owner = or_(Question.claimed_by_user_id == record.user.id, record.user.role == "admin")
    result = await db.execute(
        update(Question)
        .where(Question.id == question_id, Question.status == "claimed", owner)
        .values(
            status="pending",
            claimed_by_user_id=None,
            claimed_at=None,
            claim_expires_at=None,
            version=Question.version + 1,
        )
        .returning(Question.id)
    )
    if result.scalar_one_or_none() is None:
        await db.rollback()
        raise HumanApiError(409, "Question cannot be released.", "release_conflict")
    db.add(
        QuestionEvent(
            id=str(uuid.uuid4()),
            question_id=question_id,
            event_type="released",
            actor_user_id=record.user.id,
            metadata_json={},
        )
    )
    await db.commit()
    return {"ok": True}


@app.post("/api/human/questions/{question_id}/answer")
async def answer(
    question_id: str,
    payload: AnswerRequest,
    record: BrowserSession = Depends(state_session),
    db: AsyncSession = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> dict:
    if len(payload.content) > settings.human_api_max_answer_chars:
        raise HumanApiError(
            422, "Answer exceeds configured limit.", "answer_too_large", param="content"
        )
    current = now()
    owner = or_(Question.claimed_by_user_id == record.user.id, record.user.role == "admin")
    result = await db.execute(
        update(Question)
        .where(
            Question.id == question_id,
            Question.status == "claimed",
            Question.answered_at.is_(None),
            Question.expires_at > current,
            Question.claim_expires_at > current,
            owner,
        )
        .values(
            status="answered",
            answer_payload={"content": payload.content},
            answer_content=payload.content,
            answered_at=current,
            version=Question.version + 1,
        )
        .returning(Question.completion_id)
    )
    completion_id = result.scalar_one_or_none()
    if completion_id is None:
        await db.rollback()
        raise HumanApiError(409, "Question cannot be answered.", "answer_conflict")
    db.add(
        QuestionEvent(
            id=str(uuid.uuid4()),
            question_id=question_id,
            event_type="answered",
            actor_user_id=record.user.id,
            metadata_json={"answer_characters": len(payload.content)},
        )
    )
    await db.commit()
    return {"ok": True, "completion_id": completion_id}


@app.post("/api/human/questions/{question_id}/cancel")
async def cancel(
    question_id: str,
    record: BrowserSession = Depends(admin_session),
    db: AsyncSession = Depends(session_scope),
) -> dict:
    result = await db.execute(
        update(Question)
        .where(Question.id == question_id, Question.status.in_(["pending", "claimed"]))
        .values(
            status="cancelled",
            cancelled_at=now(),
            error_code="human_response_cancelled",
            version=Question.version + 1,
        )
        .returning(Question.id)
    )
    if result.scalar_one_or_none() is None:
        await db.rollback()
        raise HumanApiError(409, "Question cannot be cancelled.", "cancel_conflict")
    db.add(
        QuestionEvent(
            id=str(uuid.uuid4()),
            question_id=question_id,
            event_type="cancelled",
            actor_user_id=record.user.id,
            metadata_json={},
        )
    )
    await db.commit()
    return {"ok": True}


@app.post("/api/human/heartbeat")
async def heartbeat(
    record: BrowserSession = Depends(state_session), db: AsyncSession = Depends(session_scope)
) -> dict:
    await db.execute(update(User).where(User.id == record.user.id).values(last_seen_at=now()))
    await db.execute(
        update(Question)
        .where(
            Question.status == "claimed",
            Question.claimed_by_user_id == record.user.id,
            Question.claim_expires_at > now(),
        )
        .values(
            claim_expires_at=now() + timedelta(seconds=get_settings().human_api_claim_lease_seconds)
        )
    )
    await db.commit()
    return {"ok": True, "server_time": now().isoformat()}


@app.get("/api/human/status")
async def status(
    _record: BrowserSession = Depends(current_session),
    db: AsyncSession = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> dict:
    cutoff = now() - timedelta(seconds=settings.human_api_active_responder_seconds)
    active = int(
        await db.scalar(
            select(func.count(User.id)).where(
                User.enabled.is_(True),
                User.role.in_(["responder", "admin"]),
                User.last_seen_at >= cutoff,
            )
        )
        or 0
    )
    pending = int(
        await db.scalar(select(func.count(Question.id)).where(Question.status == "pending")) or 0
    )
    claimed = int(
        await db.scalar(select(func.count(Question.id)).where(Question.status == "claimed")) or 0
    )
    return {
        "enabled": settings.human_api_enabled,
        "accepting_requests": settings.human_api_enabled
        and (active > 0 or not settings.human_api_require_active_responder),
        "active_responder_count": active,
        "pending_count": pending,
        "claimed_count": claimed,
    }


@app.get("/api/admin/api-keys")
async def list_keys(
    _record: BrowserSession = Depends(current_session), db: AsyncSession = Depends(session_scope)
) -> dict:
    if _record.user.role != "admin":
        raise HumanApiError(403, "Administrator permission required.", "admin_required")
    rows = list((await db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc()))).all())
    return {
        "data": [
            {
                "id": r.id,
                "name": r.name,
                "key_prefix": r.key_prefix,
                "enabled": r.enabled,
                "revoked_at": r.revoked_at.isoformat() if r.revoked_at else None,
                "created_at": r.created_at.isoformat(),
                "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
                "rate_limit_per_minute": r.rate_limit_per_minute,
            }
            for r in rows
        ]
    }


@app.post("/api/admin/api-keys")
async def create_key(
    payload: ApiKeyCreate,
    record: BrowserSession = Depends(admin_session),
    db: AsyncSession = Depends(session_scope),
    settings: Settings = Depends(get_settings),
) -> dict:
    raw = f"human_sk_{secrets.token_urlsafe(32)}"
    row = ApiKey(
        id=str(uuid.uuid4()),
        name=payload.name.strip(),
        key_prefix=raw[:20],
        key_digest=digest(raw, settings.human_api_key_pepper),
        enabled=True,
        created_by_user_id=record.user.id,
        rate_limit_per_minute=payload.rate_limit_per_minute,
    )
    db.add(row)
    await db.commit()
    return {"id": row.id, "name": row.name, "key": raw, "key_prefix": row.key_prefix}


@app.patch("/api/admin/api-keys/{key_id}")
async def update_key(
    key_id: str,
    payload: ApiKeyUpdate,
    _record: BrowserSession = Depends(admin_session),
    db: AsyncSession = Depends(session_scope),
) -> dict:
    values = {key: value for key, value in payload.model_dump().items() if value is not None}
    if not values:
        raise HumanApiError(422, "No update supplied.", "empty_update")
    result = await db.execute(
        update(ApiKey)
        .where(ApiKey.id == key_id, ApiKey.revoked_at.is_(None))
        .values(**values)
        .returning(ApiKey.id)
    )
    if result.scalar_one_or_none() is None:
        await db.rollback()
        raise HumanApiError(404, "API key not found.", "api_key_not_found")
    await db.commit()
    return {"ok": True}


@app.delete("/api/admin/api-keys/{key_id}")
async def revoke_key(
    key_id: str,
    _record: BrowserSession = Depends(admin_session),
    db: AsyncSession = Depends(session_scope),
) -> dict:
    result = await db.execute(
        update(ApiKey)
        .where(ApiKey.id == key_id, ApiKey.revoked_at.is_(None))
        .values(enabled=False, revoked_at=now())
        .returning(ApiKey.id)
    )
    if result.scalar_one_or_none() is None:
        await db.rollback()
        raise HumanApiError(404, "API key not found.", "api_key_not_found")
    await db.commit()
    return {"ok": True}
