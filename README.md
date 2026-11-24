# Iara Chat

> Produto white-label de triagem clínica, agendamento inteligente e atendimento híbrido (IA + Handoff Humano) em português (PT-BR), construído com Python moderno, FastAPI, SQLModel e **Vercel AI Gateway**.

## Visão Geral

A **Iara** conduz o acolhimento inicial e o agendamento para clínicas configuráveis (`CLINIC_NAME`) utilizando uma **Arquitetura Híbrida (0-Token Fast-Path + 1-Call Unified LLM)** com suporte nativo a **Human-in-the-Loop**:

- **0 tokens em turnos determinísticos:** saudação LGPD, escolhas fechadas (`particular` / `convênio`), datas exatas (`DD/MM/AAAA`), confirmações (`sim` / `não`), seleção de horários (`slot:<id>` ou número), troca de especialidade, pedidos de atendente humano e *red flags* clínicos não consomem chamadas de LLM.
- **1 única chamada estruturada em turnos naturais:** extração multi-campo, segurança e próxima resposta empática acontecem juntas via `TurnAnalysis` (`Strict JSON Schema`).
- **Agendamento & Triagem de Especialidade:** sugere especialidade administrativa com base na queixa (`Clínica Geral`, `Ortopedia`, `Dermatologia`, `Cardiologia`), lista horários disponíveis no SQLModel e reserva de forma atômica contra duplo agendamento.
- **Handoff Humanizado (`/api/v1/staff`):** escalonamento por pedido do paciente, após falhas consecutivas de LLM/limite de turnos ou por **intervenção manual** da recepção (`takeover` → `human_agent` → `release` ou `close`), com suporte a polling incremental (`?after_message_id=`).

### Arquitetura Atual (Fase 4)

```text
src/iara/
├── config.py                # Configuração lazy (Gateway/Gemini, DB, CORS, STAFF_API_KEY)
├── models/
│   ├── patient.py           # PatientInfo, CoverageType, TranscriptItem
│   ├── scheduling.py        # Specialty, heurística de triagem, Slot/Appointment DTOs
│   └── extraction.py        # Schema unificado TurnAnalysis
├── services/
│   └── llm.py               # Protocol LLMClient + VercelGatewayClient + GeminiClient
├── conversation/
│   ├── state.py             # ConversationStatus (incl. scheduling, waiting_human, human_takeover)
│   ├── guardrails.py        # Fast-paths 0-token, red flags clínicos e detecção de handoff
│   ├── prompts.py           # Prompt unificado PT-BR e payloads JSON seguros
│   └── engine.py            # IntakeAgent: triagem, agendamento e pausa sob takeover humano
├── db/
│   ├── models.py            # SQLModel (sessions, messages, appointment_slots, appointments)
│   └── repository.py        # SessionRepository com seed de horários, booking atômico e staff ops
├── api/
│   ├── schemas.py           # DTOs REST (/api/v1 e /api/v1/staff)
│   └── app.py               # FastAPI + CORS + Swagger + Web Chat estático
├── web/                     # Web Chat estático (será substituído na Fase 5 por UIs separadas)
└── cli.py                   # CLI de desenvolvimento (`uv run iara`)
```

## Requisitos e Instalação

- Python `>=3.14`
- [`uv`](https://docs.astral.sh/uv/)
- Chave do **Vercel AI Gateway** (`AI_GATEWAY_API_KEY`) — ou `GEMINI_API_KEY` caso defina `LLM_PROVIDER=gemini`

```bash
git clone https://github.com/munraitoo13/iara-chat.git
cd iara-chat
uv sync
cp .env.example .env
```

Edite `.env` e preencha `AI_GATEWAY_API_KEY` e `CLINIC_NAME` (ex.: `Clínica Modelo`). Opcionalmente defina `STAFF_API_KEY` para proteger as rotas `/api/v1/staff/*`.

## Como Executar

```bash
# API FastAPI + Web Chat (http://127.0.0.1:8000 e /docs)
uv run iara-api

# Modo CLI de desenvolvimento
uv run iara
```

## Endpoints Principais (`/api/v1`)

### Fluxo do Paciente & Agenda
- `GET /api/v1/health` — status, provedor, modelo e clínica configurada.
- `GET /api/v1/slots` — horários disponíveis (filtro opcional `?specialty=`).
- `POST /api/v1/sessions` — inicia sessão de triagem com saudação LGPD.
- `GET /api/v1/sessions/{session_id}` — restaura sessão (suporta polling `?after_message_id=`).
- `POST /api/v1/sessions/{session_id}/messages` — processa turno via fast-path ou LLM.
- `DELETE /api/v1/sessions/{session_id}` — apaga sessão/mensagens e libera horário reservado (LGPD).

### Dashboard & Handoff Humano (`/api/v1/staff`)
*(Protegido por header `X-Staff-Key` quando `STAFF_API_KEY` está configurado.)*
- `GET /api/v1/staff/sessions` — fila de atendimentos com status, motivo de handoff e eficiência de tokens.
- `GET /api/v1/staff/appointments` — agenda de consultas confirmadas.
- `POST /api/v1/staff/sessions/{session_id}/takeover` — pausa a Iara e assume o atendimento.
- `POST /api/v1/staff/sessions/{session_id}/messages` — envia resposta como `human_agent`.
- `POST /api/v1/staff/sessions/{session_id}/release` — devolve o atendimento para a Iara.
- `POST /api/v1/staff/sessions/{session_id}/close` — encerra o atendimento manualmente.

## Testes e Qualidade

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
```

## Roadmap

- [x] **Core v2 (Híbrido):** Empacotamento `src/`, Vercel AI Gateway + 0-token fast-path + 1-call unified schema, confirmação/edição, guardrails clínicos.
- [x] **Fase 3 — API & SQLModel:** FastAPI (`/api/v1`), persistência SQLModel em SQLite, exclusão LGPD e Web Chat estático inicial.
- [x] **Fase 4 — Agendamento & Handoff:** Sugestão de especialidade, agenda SQLModel com trava contra duplo agendamento e API completa de intervenção humana (`/api/v1/staff`).
- [ ] **Fase 5 — Separação de UIs (Paciente + Dashboard):** Frontend dedicado (ex.: Next.js) separando chat limpo do paciente, painel de agenda/métricas da clínica e tela de atendimento humanizado.
