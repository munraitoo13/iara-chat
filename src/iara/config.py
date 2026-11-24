"""Application settings loaded lazily via pydantic-settings."""

from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Iara intake assistant."""

    llm_provider: Literal["vercel_gateway", "gemini"] = "vercel_gateway"

    ai_gateway_api_key: SecretStr | None = None
    ai_gateway_base_url: str = "https://ai-gateway.vercel.sh/v1"
    ai_gateway_model: str = "google/gemini-3.5-flash-lite"
    ai_gateway_fallback_models: str = ""

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"

    clinic_name: str = "Clínica Iara"
    max_input_chars: int = 1000
    max_turns: int = 30

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

    def active_model_name(self) -> str:
        """Return the model identifier for the active LLM provider."""

        if self.llm_provider == "vercel_gateway":
            return self.ai_gateway_model
        return self.gemini_model


def get_settings() -> Settings:
    """Load and return validated runtime settings on demand."""

    return Settings()
