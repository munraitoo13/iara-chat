# Iara Chat

> Assistente virtual de triagem e pré-cadastro de pacientes em português (PT-BR), construído como case de Engenharia de IA com Python moderno, Pydantic e **Vercel AI Gateway** (com suporte opcional ao SDK direto do Google GenAI).

## Visão Geral

A **Iara** conduz o acolhimento inicial de pacientes antes da consulta utilizando uma **Arquitetura Híbrida (0-Token Fast-Path + 1-Call Unified LLM)**:

- **0 tokens em turnos determinísticos:** saudação LGPD, escolhas fechadas (`particular` / `convênio`), datas exatas (`DD/MM/AAAA`), confirmação/rejeição (`sim` / `não`) e detecção de emergências clínicas não consomem chamadas de LLM.
- **1 única chamada estruturada em turnos naturais:** extração multi-campo, detecção de *red flags* / *prompt injection* e geração da próxima resposta empática acontecem juntos via `TurnAnalysis` (`Strict JSON Schema`), cortando latência e consumo de tokens pela metade.

### Arquitetura Atual (Core v2)

```text
src/iara/
├── config.py                # Configuração lazy via pydantic-settings (Vercel AI Gateway ou Gemini)
├── models/
│   ├── patient.py           # Modelos de domínio (PatientInfo, CoverageType, TranscriptItem)
│   └── extraction.py        # Schema unificado TurnAnalysis (extração + segurança + resposta)
├── services/
│   └── llm.py               # Protocol LLMClient + VercelGatewayClient (OpenAI SDK) + GeminiClient
├── conversation/
│   ├── state.py             # ConversationStatus, ConversationState e AgentReply
│   ├── guardrails.py        # Fast-paths 0-token, validação determinística e red flags clínicos
│   ├── prompts.py           # Prompt unificado PT-BR e payloads JSON seguros contra format injection
│   └── engine.py            # IntakeAgent: motor híbrido com confirmação e edição reais
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

Edite `.env` e preencha `AI_GATEWAY_API_KEY` (padrão: `google/gemini-3.5-flash-lite` via `https://ai-gateway.vercel.sh/v1`).

## Como Executar

```bash
# Modo CLI de desenvolvimento (exibe [0 tokens · fast-path] vs [1 chamada · LLM])
uv run iara

# Ou via módulo Python
uv run python -m iara
```

## Testes e Qualidade

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
```

## Roadmap

- [x] **Core v2 (Híbrido):** Empacotamento `src/`, Vercel AI Gateway + 0-token fast-path + 1-call unified schema, confirmação/edição, guardrails clínicos e testes offline.
- [ ] **Fase 3 — Interface Web & Persistência:** API com FastAPI, histórico em SQLite/PostgreSQL e Web Chat com botões de resposta rápida.
- [ ] **Fase 4 — Agendamento:** Consulta de disponibilidade e reserva de horários em agenda.

## Aviso

Projeto demonstrativo de engenharia de IA. Não realiza diagnóstico médico, prescrição nem substitui atendimento de urgência ou validação clínica humana.
