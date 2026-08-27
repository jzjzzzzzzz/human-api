# Operations

## Production checklist

- Use PostgreSQL and run `alembic upgrade head` before starting API workers.
- Inject two different random peppers through a secret manager.
- Set `SESSION_COOKIE_SECURE=true` behind HTTPS.
- Put request body, connection, and per-IP rate limits at the edge.
- Keep the proxy read timeout above `HUMAN_RESPONSE_TIMEOUT_SECONDS`; the included Nginx timeout is 210 seconds for a 180-second deadline.
- Back up PostgreSQL, test restores, and define an answer/audit retention policy.
- Do not log request bodies, authorization headers, cookies, passwords, or raw keys.
- Monitor `/health`, `/ready`, timeout rates, queue depth, and active responder count.

## Responders

Create accounts only for authorized people:

```bash
docker compose exec api python -m scripts.create_user responder@example.test --role responder
```

Disabling a user in the database invalidates access on the next request. Rotate credentials when access changes.

## No active responder

When `HUMAN_API_REQUIRE_ACTIVE_RESPONDER=true`, a recent authenticated heartbeat is required before new work is accepted. Open the queue and confirm the heartbeat succeeds.
