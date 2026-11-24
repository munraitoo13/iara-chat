"""LLM service abstractions, Vercel AI Gateway adapter, and direct Gemini client."""

from collections.abc import Sequence
from typing import Protocol

from google import genai
from google.genai import types
from openai import OpenAI

from iara.config import Settings
from iara.models.extraction import TurnAnalysis


class LLMError(Exception):
    """Raised when the upstream LLM provider fails or returns an invalid payload."""


class LLMClient(Protocol):
    """Protocol decoupling conversational engine logic from specific LLM SDKs or gateways."""

    def analyze_turn(self, system_instruction: str, user_payload: str) -> TurnAnalysis:
        """Extract structured fields, intent, safety signals, and optional reply in one call."""
        ...


class VercelGatewayClient:
    """OpenAI-compatible client targeting Vercel AI Gateway with structured outputs."""

    def __init__(
        self,
        api_key: str,
        model: str = "google/gemini-3.5-flash-lite",
        base_url: str = "https://ai-gateway.vercel.sh/v1",
        fallback_models: Sequence[str] = (),
    ) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.fallback_models = list(fallback_models)

    def analyze_turn(self, system_instruction: str, user_payload: str) -> TurnAnalysis:
        """Run unified extraction + reply generation in a single stateless Gateway call."""

        extra_body: dict[str, object] | None = None
        if self.fallback_models:
            extra_body = {
                "providerOptions": {
                    "gateway": {
                        "models": self.fallback_models,
                    }
                }
            }

        try:
            completion = self.client.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": user_payload},
                ],
                temperature=0.2,
                response_format=TurnAnalysis,
                extra_body=extra_body,
            )
            message = completion.choices[0].message
            parsed = getattr(message, "parsed", None)
            if isinstance(parsed, TurnAnalysis):
                return parsed
            raw_content = (message.content or "").strip()
            if not raw_content:
                raise LLMError("Empty structured response returned by Vercel AI Gateway.")
            return TurnAnalysis.model_validate_json(raw_content)
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"Vercel AI Gateway request failed: {exc}") from exc


class GeminiClient:
    """Direct Google GenAI SDK adapter using stateless requests."""

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash-lite") -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def analyze_turn(self, system_instruction: str, user_payload: str) -> TurnAnalysis:
        """Run unified extraction + reply generation in a single stateless Gemini call."""

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=user_payload,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                    response_mime_type="application/json",
                    response_schema=TurnAnalysis,
                ),
            )
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, TurnAnalysis):
                return parsed
            raw_text = (response.text or "").strip()
            if not raw_text:
                raise LLMError("Empty structured response returned by Gemini.")
            return TurnAnalysis.model_validate_json(raw_text)
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"Gemini turn analysis failed: {exc}") from exc


def create_llm_client(settings: Settings) -> LLMClient:
    """Instantiate the configured LLM provider client from validated settings."""

    if settings.llm_provider == "vercel_gateway":
        assert settings.ai_gateway_api_key is not None
        return VercelGatewayClient(
            api_key=settings.ai_gateway_api_key.get_secret_value(),
            model=settings.ai_gateway_model,
            base_url=settings.ai_gateway_base_url,
            fallback_models=settings.parsed_fallback_models(),
        )

    assert settings.gemini_api_key is not None
    return GeminiClient(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_model,
    )
