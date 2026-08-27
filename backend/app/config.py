from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./human_api.db"
    human_api_key_pepper: str = "development-key-pepper-change-me"
    session_token_pepper: str = "development-session-pepper-change-me"
    human_api_model_id: str = "human-1"
    human_api_enabled: bool = True
    human_response_timeout_seconds: int = Field(default=180, ge=5, le=3600)
    human_api_claim_lease_seconds: int = Field(default=180, ge=15, le=3600)
    human_api_active_responder_seconds: int = Field(default=45, ge=10, le=600)
    human_api_require_active_responder: bool = True
    human_api_rate_limit_per_minute: int = Field(default=10, ge=1, le=1000)
    human_api_max_input_chars: int = Field(default=50_000, ge=1, le=1_000_000)
    human_api_max_answer_chars: int = Field(default=50_000, ge=1, le=1_000_000)
    human_api_max_messages: int = Field(default=100, ge=1, le=1000)
    session_cookie_secure: bool = False

    @model_validator(mode="after")
    def secure_secrets_in_production(self) -> "Settings":
        if self.session_cookie_secure:
            if len(self.human_api_key_pepper) < 32 or "change-me" in self.human_api_key_pepper:
                raise ValueError("HUMAN_API_KEY_PEPPER must be a strong production secret")
            if len(self.session_token_pepper) < 32 or "change-me" in self.session_token_pepper:
                raise ValueError("SESSION_TOKEN_PEPPER must be a strong production secret")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
