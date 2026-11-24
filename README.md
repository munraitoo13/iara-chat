# Iara Chat

> Produto white-label de triagem clínica, agendamento inteligente e atendimento híbrido (IA + Handoff Humano) em português (PT-BR), construído com **FastAPI**, **SQLModel**, **Vercel AI Gateway** e **Next.js 16**.

## Visão Geral

A **Iara** separa claramente a experiência acolhedora do paciente da operação clínica:

- **Interface do Paciente (`/` — Next.js):** chat minimalista e acolhedor sem métricas técnicas, com chips de resposta rápida, menu discreto (`Falar com atendente`, `Nova triagem`, `Apagar meus dados (LGPD)`), cartão de confirmação de consulta e sincronização automática quando um atendente assume.
- **Dashboard da Clínica (`/dashboard` — Next.js):** fila de atendimentos em tempo real, visualização de histórico e telemetria híbrida (`0 tokens` vs `LLM`), intervenção humana (**Assumir atendimento** / responder / **Devolver para Iara** / **Encerrar**), agenda de consultas e métricas de economia.
- **Backend Híbrido (`src/iara/`):** *fast-paths* determinísticos de 0 tokens + 1 única chamada estruturada (`TurnAnalysis`) via Vercel AI Gateway, agendamento atômico em SQLModel e API `/api/v1`.

### Arquitetura Atual (Fase 5)

```text
├── src/iara/                    # Backend Python (FastAPI + SQLModel + Engine Híbrida)
│   ├── config.py                # Configuração lazy (Gateway/Gemini, DB, CORS, STAFF_API_KEY)
│   ├── models/                  # PatientInfo, Specialty, Slot/Appointment DTOs, TurnAnalysis
│   ├── services/llm.py          # VercelGatewayClient + GeminiClient
│   ├── conversation/            # IntakeAgent, guardrails 0-token, prompt unificado PT-BR
│   ├── db/                      # SQLModel (sessions, messages, appointment_slots, appointments)
│   ├── api/                     # FastAPI REST (/api/v1 e /api/v1/staff)
│   └── cli.py                   # CLI de desenvolvimento (`uv run iara`)
└── frontend/                    # Next.js 16 + React 19 + Tailwind CSS 4 + TypeScript
    └── src/
        ├── app/page.tsx         # Chat limpo do paciente (/)
        ├── app/dashboard/       # Painel operacional da clínica (/dashboard)
        ├── components/          # PatientChat e StaffDashboard
        └── lib/                 # Cliente tipado de API e contratos TypeScript
```

## Requisitos e Instalação

- Python `>=3.14` + [`uv`](https://docs.astral.sh/uv/)
- Node `>=22` + [`pnpm`](https://pnpm.io/)
- Chave do **Vercel AI Gateway** (`AI_GATEWAY_API_KEY`) — ou `GEMINI_API_KEY` caso defina `LLM_PROVIDER=gemini`

```bash
git clone https://github.com/munraitoo13/iara-chat.git
cd iara-chat

# Backend
uv sync
cp .env.example .env

# Frontend
cd frontend
pnpm install
cp .env.example .env.local
```

Preencha `AI_GATEWAY_API_KEY` e `CLINIC_NAME` (ex.: `Clínica Modelo`) no `.env` da raiz.

## Como Executar

Abra dois terminais:

```bash
# Terminal 1 — Backend FastAPI (http://127.0.0.1:8000 e /docs)
uv run iara-api

# Terminal 2 — Frontend Next.js (http://localhost:3000 e /dashboard)
cd frontend
pnpm dev
```

- **Chat do Paciente:** `http://localhost:3000`
- **Dashboard da Clínica:** `http://localhost:3000/dashboard`
- **Swagger UI da API:** `http://127.0.0.1:8000/docs`

*(Modo CLI de desenvolvimento também segue disponível via `uv run iara`.)*

## Testes e Qualidade

```bash
# Backend
uv run ruff check .
uv run ruff format --check .
uv run pytest -v

# Frontend
cd frontend
pnpm typecheck
pnpm build
```

## Roadmap

- [x] **Core v2 (Híbrido):** Empacotamento `src/`, Vercel AI Gateway + 0-token fast-path + 1-call unified schema, confirmação/edição, guardrails clínicos.
- [x] **Fase 3 — API & SQLModel:** FastAPI (`/api/v1`), persistência SQLModel em SQLite e exclusão LGPD.
- [x] **Fase 4 — Agendamento & Handoff:** Sugestão de especialidade, agenda SQLModel com trava contra duplo agendamento e API `/api/v1/staff`.
- [x] **Fase 5 — Separação de UIs (Next.js):** Chat limpo do paciente (`/`) e Dashboard da clínica (`/dashboard`) com fila, agenda, métricas da IA e intervenção humana.
