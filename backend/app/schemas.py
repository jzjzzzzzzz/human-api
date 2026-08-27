from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ErrorDetail(BaseModel):
    message: str
    type: str
    param: str | None = None
    code: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class TextPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"]
    text: str


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["developer", "system", "user", "assistant"]
    content: str | list[TextPart]

    def normalized(self) -> str:
        return (
            self.content if isinstance(self.content, str) else "".join(p.text for p in self.content)
        )


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    n: int = 1
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1)
    max_completion_tokens: int | None = Field(default=None, ge=1)
    stop: str | list[str] | None = None
    user: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] | None = None
    tools: list[Any] | None = None
    tool_choice: Any | None = None
    functions: list[Any] | None = None
    function_call: Any | None = None
    response_format: dict[str, Any] | None = None

    @model_validator(mode="after")
    def supported_features(self) -> CompletionRequest:
        if not self.messages:
            raise ValueError("messages_empty")
        if self.stream:
            raise ValueError("stream_not_supported")
        if self.n != 1:
            raise ValueError("n_not_supported")
        if self.tools is not None or self.tool_choice is not None:
            raise ValueError("tools_not_supported")
        if self.functions is not None or self.function_call is not None:
            raise ValueError("functions_not_supported")
        if self.response_format not in (None, {"type": "text"}):
            raise ValueError("response_format_not_supported")
        return self


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=256)


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    rate_limit_per_minute: int = Field(default=10, ge=1, le=1000)


class ApiKeyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=1000)


class CompletionMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class CompletionChoice(BaseModel):
    index: Literal[0] = 0
    message: CompletionMessage
    finish_reason: Literal["stop"] = "stop"


class CompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[CompletionChoice]
