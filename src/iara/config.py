"""Application settings loaded lazily via pydantic-settings."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Iara intake assistant."""

    gemini_api_key: SecretStr
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


def get_settings() -> Settings:
    """Load and return validated runtime settings on demand."""

    return Settings()
