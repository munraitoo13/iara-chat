# Iara Chat — Repository Agent Guidelines

## Purpose & Scope

`iara-chat` is a white-label AI-driven patient intake, scheduling, and human-handoff platform designed as a production-grade portfolio and AI engineering case study. **Iara** is the AI product name; `CLINIC_NAME` configures the client clinic.

- **Vault pair:** Strategic roadmap, audit history, and cross-session decisions live in `/home/munraitoo13/Obsidian/01. Projects/Iara Chat/`.
- **This file governs:** Local code architecture, language conventions, tooling, tests, and security hygiene inside this repository.

## Architecture

- Source code follows a `src/` layout under `src/iara/`:
  - `config.py`: Lazy `pydantic-settings` configuration (`get_settings()`). Supports `LLM_PROVIDER=vercel_gateway` (default) and `LLM_PROVIDER=gemini`, DB/CORS/API host settings, and optional `STAFF_API_KEY`. Never instantiate `Settings()` at import time.
  - `models/`: Domain models (`PatientInfo`, `TranscriptItem`, `Specialty`, `AppointmentSlotDTO`, `BookedAppointmentDTO`) and unified single-call structured LLM schema (`TurnAnalysis`, `ExtractedPatientFields`).
  - `services/llm.py`: `LLMClient` protocol, `VercelGatewayClient` (OpenAI-compatible `https://ai-gateway.vercel.sh/v1`), stateless `GeminiClient`, and `create_llm_client()`.
  - `conversation/`: `ConversationStatus` (including `scheduling`, `waiting_human`, `human_takeover`), deterministic guardrails and 0-token fast-paths (`guardrails.py`), JSON-safe unified prompt builder (`prompts.py`), and `IntakeAgent` (`engine.py`).
  - `db/`: SQLModel tables (`intake_sessions`, `transcript_messages`, `appointment_slots`, `appointments`) and `SessionRepository` (`repository.py`) with idempotent slot seeding, atomic booking, and staff handoff operations.
  - `api/`: Versioned FastAPI REST layer under `/api/v1` and `/api/v1/staff` (`app.py`, `schemas.py`), supporting incremental polling (`?after_message_id=`).
  - `web/`: Interim static Web Chat UI (`index.html`, `styles.css`, `app.js`) kept intentionally decoupled from `/api/v1` ahead of separated Patient vs. Staff Dashboard UIs.
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

- Never commit `.env` files, API keys (`AI_GATEWAY_API_KEY`, `GEMINI_API_KEY`, `STAFF_API_KEY`), local `.db`/`.sqlite` files, or credentials. Only `.env.example` belongs in Git.
- Never interpolate raw `user_input` using Python `str.format()` — serialize inputs safely via JSON payloads.
- Avoid XSS in static frontend code (`app.js`): always render user/assistant text via `textContent`, never `innerHTML`.
- Treat clinical and patient intake data (name, DOB, address, insurance ID, chief complaint) as sensitive under LGPD: avoid logging raw PHI/PII or secrets.
- Always ask the user explicitly before running `git push` or any destructive Git operation.
