# OpenAI compatibility

## Endpoints

- `GET /v1/models`
- `GET /v1/models/human-1`
- `POST /v1/chat/completions`

All require `Authorization: Bearer human_sk_...`. The model ID is configurable through `HUMAN_API_MODEL_ID`; client and server configuration must match.

## Stable IDs

A completion ID is generated with cryptographically secure randomness, prefixed `chatcmpl_`, committed before queue visibility, and protected by a unique database constraint. Idempotent retries scoped to one API key reuse the same question and ID.

## Success envelope

The server returns `object: chat.completion`, the original creation timestamp, validated model ID, one assistant message, index zero, and `finish_reason: stop`. Usage is omitted because the service does not fabricate token counts.

## Errors

Errors use `{error: {message, type, param, code}}`. Common codes include `invalid_api_key`, `model_not_found`, `no_active_responder`, `rate_limit_exceeded`, `human_response_timeout`, and `human_response_cancelled`.
