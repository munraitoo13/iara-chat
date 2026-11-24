# Iara Chat — Repository Agent Guidelines

## Purpose & Scope

`iara-chat` is an AI-driven patient intake and clinical triage assistant designed as a production-grade portfolio and AI engineering case study.

- **Vault pair:** Strategic roadmap, audit history, and cross-session decisions live in `/home/munraitoo13/Obsidian/01. Projects/Iara Chat/`.
- **This file governs:** Local code architecture, language conventions, tooling, tests, and security hygiene inside this repository.

## Architecture

- Source code follows a `src/` layout under `src/iara/`:
  - `config.py`: Lazy `pydantic-settings` configuration (`get_settings()`). Supports `LLM_PROVIDER=vercel_gateway` (default) and `LLM_PROVIDER=gemini`, plus DB/CORS/API host settings. Never instantiate `Settings()` at import time.
  - `models/`: Pydantic domain models (`PatientInfo`, `TranscriptItem`) and unified single-call structured LLM schema (`TurnAnalysis`, `ExtractedPatientFields`).
  - `services/llm.py`: `LLMClient` protocol, `VercelGatewayClient` (OpenAI-compatible `https://ai-gateway.vercel.sh/v1` with `chat.completions.parse`), stateless `GeminiClient`, and `create_llm_client()`.
  - `conversation/`: State management (`state.py`), deterministic guardrails and 0-token fast-paths (`guardrails.py`), JSON-safe unified prompt builder (`prompts.py`), and stateless-hydratable `IntakeAgent` (`engine.py`).
  - `db/`: SQLModel persistence (`models.py`) and `SessionRepository` (`repository.py`) targeting local SQLite by default and ready for PostgreSQL.
  - `api/`: Versioned FastAPI REST layer under `/api/v1` (`app.py`, `schemas.py`) with CORS and static Web Chat mounting.
  - `web/`: Static Web Chat UI (`index.html`, `styles.css`, `app.js`) designed cleanly around `/api/v1` so a future Next.js/React frontend can replace it without backend changes.
  - `cli.py` / `__main__.py`: Development CLI entrypoint (`uv run iara` / `uv run python -m iara`).

## Language Conventions

- **Code, identifiers, docstrings, comments, `AGENTS.md`, and Git commit messages:** English.
- **User-facing clinical copy, prompts, and fallback messages:** Brazilian Portuguese (PT-BR), aligned with Brazilian formats (`DD/MM/AAAA`, CEP/address, `Convênio` or `Particular`, LGPD notice, SAMU 192 / CVV 188 emergency guidance).

## Tooling & Verification

Use `uv` for environment and dependency management:

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
uv run iara-api
uv run iara
```

Always run `ruff check .`, `ruff format --check .`, and `pytest -v` before committing changes.

## Security & LGPD Hygiene

- Never commit `.env` files, API keys (`AI_GATEWAY_API_KEY`, `GEMINI_API_KEY`), local `.db`/`.sqlite` files, or credentials. Only `.env.example` belongs in Git.
- Never interpolate raw `user_input` using Python `str.format()` — serialize inputs safely via JSON payloads.
- Avoid XSS in static frontend code (`app.js`): always render user/assistant text via `textContent`, never `innerHTML`.
- Treat clinical and patient intake data (name, DOB, address, insurance ID, chief complaint) as sensitive under LGPD: avoid logging raw PHI/PII or secrets.
- Always ask the user explicitly before running `git push` or any destructive Git operation.
