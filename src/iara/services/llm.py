"""LLM service abstractions and Google GenAI client implementation."""

from typing import Protocol

from google import genai
from google.genai import types

from iara.models.extraction import TurnAnalysis


class LLMError(Exception):
    """Raised when the upstream LLM provider fails or returns an invalid payload."""


class LLMClient(Protocol):
    """Protocol decoupling conversational engine logic from specific LLM SDKs."""

    def analyze_turn(self, system_instruction: str, user_payload: str) -> TurnAnalysis:
        """Extract structured fields, user intent, and safety signals for one turn."""
        ...

    def generate_reply(self, system_instruction: str, prompt: str) -> str:
        """Generate a natural-language conversational reply."""
        ...


class GeminiClient:
    """Stateless Google GenAI SDK adapter using isolated requests per task."""

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash-lite") -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def analyze_turn(self, system_instruction: str, user_payload: str) -> TurnAnalysis:
        """Run deterministic JSON-schema extraction without polluting chat history."""

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=user_payload,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0,
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

    def generate_reply(self, system_instruction: str, prompt: str) -> str:
        """Generate a concise Brazilian Portuguese reply from structured turn context."""

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.6,
                ),
            )
            text = (response.text or "").strip()
            if not text:
                raise LLMError("Empty reply returned by Gemini.")
            return text
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"Gemini reply generation failed: {exc}") from exc
