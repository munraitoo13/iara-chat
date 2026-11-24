"""Application settings loaded lazily via pydantic-settings."""

from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Iara intake assistant and API."""

    llm_provider: Literal["vercel_gateway", "gemini"] = "vercel_gateway"

    ai_gateway_api_key: SecretStr | None = None
    ai_gateway_base_url: str = "https://ai-gateway.vercel.sh/v1"
    ai_gateway_model: str = "google/gemini-3.5-flash-lite"
    ai_gateway_fallback_models: str = ""

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"

    clinic_name: str = "Clínica Modelo"
    max_input_chars: int = 1000
    max_turns: int = 30

    database_url: str = "sqlite:///./data/iara.db"
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000"
    )
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    staff_api_key: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def _validate_provider_credentials(self) -> Settings:
        if self.llm_provider == "vercel_gateway":
            if (
                self.ai_gateway_api_key is None
                or not self.ai_gateway_api_key.get_secret_value().strip()
            ):
                raise ValueError("AI_GATEWAY_API_KEY is required when LLM_PROVIDER=vercel_gateway.")
        elif self.llm_provider == "gemini":
            if self.gemini_api_key is None or not self.gemini_api_key.get_secret_value().strip():
                raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini.")
        return self

    def parsed_fallback_models(self) -> list[str]:
        """Return comma-separated fallback model IDs configured for Vercel AI Gateway."""

        return [item.strip() for item in self.ai_gateway_fallback_models.split(",") if item.strip()]

    def parsed_cors_origins(self) -> list[str]:
        """Return comma-separated CORS origins allowed to call the API."""

        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def active_model_name(self) -> str:
        """Return the model identifier for the active LLM provider."""

        if self.llm_provider == "vercel_gateway":
            return self.ai_gateway_model
        return self.gemini_model

    def configured_staff_api_key(self) -> str | None:
        """Return non-empty staff API key when configured, else None."""

        if self.staff_api_key is None:
            return None
        val = self.staff_api_key.get_secret_value().strip()
        return val or None


def get_settings() -> Settings:
    """Load and return validated runtime settings on demand."""

    return Settings()
