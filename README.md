# Iara Chat

> Assistente virtual de triagem e pré-cadastro de pacientes em português (PT-BR), construído como case de Engenharia de IA com Python moderno, FastAPI, SQLModel e **Vercel AI Gateway** (com suporte opcional ao SDK direto do Google GenAI).

## Visão Geral

A **Iara** conduz o acolhimento inicial de pacientes utilizando uma **Arquitetura Híbrida (0-Token Fast-Path + 1-Call Unified LLM)**:

- **0 tokens em turnos determinísticos:** saudação LGPD, escolhas fechadas (`particular` / `convênio`), botões rápidos da UI, datas exatas (`DD/MM/AAAA`), confirmação/rejeição (`sim` / `não`) e detecção de emergências clínicas não consomem chamadas de LLM.
- **1 única chamada estruturada em turnos naturais:** extração multi-campo, detecção de *red flags* / *prompt injection* e geração da próxima resposta empática acontecem juntos via `TurnAnalysis` (`Strict JSON Schema`).
- **Persistência e API desacopladas:** sessões e transcrições salvas em SQLite via **SQLModel** (pronto para migrar para PostgreSQL) e API REST versionada `/api/v1/` pronta para evoluir para frontend separado em Next.js/React.

### Arquitetura Atual (Fase 3)

```text
src/iara/
├── config.py                # Configuração lazy via pydantic-settings (Gateway/Gemini + DB/CORS)
├── models/
│   ├── patient.py           # Modelos de domínio (PatientInfo, CoverageType, TranscriptItem)
│   └── extraction.py        # Schema unificado TurnAnalysis (extração + segurança + resposta)
├── services/
│   └── llm.py               # Protocol LLMClient + VercelGatewayClient + GeminiClient
├── conversation/
│   ├── state.py             # ConversationStatus, ConversationState e AgentReply
│   ├── guardrails.py        # Fast-paths 0-token, validação determinística e red flags clínicos
│   ├── prompts.py           # Prompt unificado PT-BR e payloads JSON seguros
│   └── engine.py            # IntakeAgent: motor híbrido stateless-friendly
├── db/
│   ├── models.py            # Tabelas SQLModel (intake_sessions, transcript_messages)
│   └── repository.py        # SessionRepository (SQLite local ou PostgreSQL)
├── api/
│   ├── schemas.py           # DTOs REST (/api/v1) e quick_replies contextuais
│   └── app.py               # FastAPI + CORS + endpoints + montagem do Web Chat estático
├── web/                     # Web Chat estático (HTML/CSS/JS) com ficha em tempo real
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

Edite `.env` e preencha `AI_GATEWAY_API_KEY`.

## Como Executar

### Interface Web + API FastAPI (Recomendado)

```bash
uv run iara-api
```

- **Web Chat:** `http://127.0.0.1:8000`
- **Documentação interativa (Swagger):** `http://127.0.0.1:8000/docs`

### Modo CLI de Desenvolvimento

```bash
uv run iara
```

## Endpoints Principais (`/api/v1`)

- `GET /api/v1/health` — status do serviço, provedor e modelo ativo.
- `POST /api/v1/sessions` — inicia uma nova sessão de triagem com saudação LGPD.
- `GET /api/v1/sessions/{session_id}` — restaura estado, histórico e ficha estruturada.
- `POST /api/v1/sessions/{session_id}/messages` — processa turno via fast-path ou LLM.
- `DELETE /api/v1/sessions/{session_id}` — apaga sessão e mensagens (exclusão LGPD).

## Testes e Qualidade

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
```

## Roadmap

- [x] **Core v2 (Híbrido):** Empacotamento `src/`, Vercel AI Gateway + 0-token fast-path + 1-call unified schema, confirmação/edição, guardrails clínicos e testes offline.
- [x] **Fase 3 — API, Persistência & Web Chat:** FastAPI (`/api/v1`), persistência SQLModel em SQLite, exclusão LGPD e Web Chat estático com ficha em tempo real e chips de resposta rápida.
- [ ] **Fase 4 — Agendamento & Evolução:** Consulta de disponibilidade/agenda e migração opcional da UI para Next.js/React.

## Aviso

Projeto demonstrativo de engenharia de IA. Não realiza diagnóstico médico, prescrição nem substitui atendimento de urgência ou validação clínica humana.
