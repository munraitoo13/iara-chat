# Iara Chat

> Produto white-label de triagem clínica, agendamento inteligente e atendimento híbrido (IA + Handoff Humano) em português (PT-BR), construído com **FastAPI**, **SQLModel**, **Vercel AI Gateway** e **Next.js 16**.

## Visão Geral

A **Iara** separa claramente a experiência acolhedora do paciente da operação clínica:

- **Interface do Paciente (`/` — Next.js):** chat minimalista e acolhedor sem métricas técnicas, com coleta de telefone/WhatsApp e e-mail opcional, chips de resposta rápida, menu discreto (`Falar com atendente`, `Nova triagem`, `Apagar meus dados (LGPD)`), cartão de confirmação com protocolo `#XXXXXXXX` (para cancelar/remarcar via chat) e sincronização automática quando um atendente assume.
- **Dashboard da Clínica (`/dashboard` — Next.js):** fila de atendimentos em tempo real, Copiloto silencioso (*Preencher com IA*), intervenção humana, gestão completa da agenda (gerar horários em lote, remover horários livres, cancelar e remarcar consultas), painel de **Lembretes Programados (24h e 2h)** e métricas de economia.
- **Backend Híbrido (`src/iara/`):** *fast-paths* determinísticos de 0 tokens (incluindo telefone BR e comandos `#PROTOCOLO`) + 1 única chamada estruturada (`TurnAnalysis`) via Vercel AI Gateway, agendamento/remarcação atômica em SQLModel, dispatcher de lembretes (Mock + Resend) e API `/api/v1`.

### Arquitetura Atual (Fase 6)

```text
├── src/iara/                    # Backend Python (FastAPI + SQLModel + Engine Híbrida)
│   ├── config.py                # Configuração lazy (Gateway/Gemini, DB, CORS, STAFF_API_KEY, Resend)
│   ├── models/                  # PatientInfo (+ telefone/e-mail), Specialty, Slot/Appointment/Reminder DTOs
│   ├── services/                # VercelGatewayClient, GeminiClient e ReminderSender (Mock + Resend)
│   ├── conversation/            # IntakeAgent, guardrails 0-token (#PROTOCOLO + BR phone), prompt PT-BR
│   ├── db/                      # SQLModel (sessions, messages, slots, appointments, appointment_reminders)
│   ├── api/                     # FastAPI REST (/api/v1, /api/v1/staff/*) + loop de lembretes em lifespan
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

Preencha `AI_GATEWAY_API_KEY` e `CLINIC_NAME` (ex.: `Clínica Modelo`) no `.env` da raiz. Opcionalmente ajuste `MIN_CANCEL_NOTICE_HOURS=2` e configure `RESEND_API_KEY` + `RESEND_FROM_EMAIL` para envio real de e-mails de lembrete (`Mock` ativo por padrão).

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
- [x] **Fase 6 — Ciclo Completo da Consulta:** Telefone/WhatsApp + e-mail na triagem, CRUD/lote de horários, cancelamento/remarcação no dashboard e via `#PROTOCOLO` no chat (regra `2h`), e lembretes automáticos `24h` + `2h` (Mock / Resend).
