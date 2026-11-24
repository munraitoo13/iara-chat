# Iara Chat

> Assistente virtual de triagem e pré-cadastro de pacientes em português (PT-BR), construído como case de Engenharia de IA com Python moderno, Pydantic e Google GenAI SDK.

## Visão Geral

A **Iara** conduz o acolhimento inicial de pacientes antes da consulta, coletando dados cadastrais e queixa clínica de forma conversacional, com extração estruturada (*Structured Outputs*), validação determinística, suporte a **Convênio ou Particular** e guardrails clínicos para situações de emergência.

### Arquitetura Atual (Core v2)

```text
src/iara/
├── config.py                # Configuração lazy via pydantic-settings (GEMINI_MODEL configurável)
├── models/
│   ├── patient.py           # Modelos de domínio (PatientInfo, CoverageType, TranscriptItem)
│   └── extraction.py        # Schemas estruturados enviados ao Gemini (TurnAnalysis)
├── services/
│   └── llm.py               # Protocol LLMClient + GeminiClient stateless (google-genai 3.x)
├── conversation/
│   ├── state.py             # ConversationStatus, ConversationState e AgentReply
│   ├── guardrails.py        # Normalização, validação determinística e red flags clínicos
│   ├── prompts.py           # Instruções PT-BR e payloads JSON seguros contra format injection
│   └── engine.py            # IntakeAgent: extração multi-campo, confirmação e edição real
└── cli.py                   # CLI de desenvolvimento (`uv run iara`)
```

### Principais Melhorias Implementadas

- **Chamadas stateless isoladas:** extração estruturada (`temperature=0.0`, `response_schema=TurnAnalysis`) separada da geração de resposta conversacional, evitando poluição de histórico e desperdício de tokens.
- **Extração multi-campo e fluxo flexível:** reconhece múltiplos dados informados na mesma mensagem e adapta perguntas conforme modalidade (`convênio` vs. `particular`).
- **Confirmação e edição reais:** permite revisar o resumo em `DD/MM/AAAA`, corrigir campos específicos ou alternar modalidade antes de concluir.
- **Segurança & Guardrails clínicos:** detecção determinística e via LLM de sinais de alerta (orientando SAMU 192 / Bombeiros 193 / CVV 188), proteção contra *prompt injection* e payloads JSON seguros.
- **Testabilidade:** núcleo desacoplado via protocolo `LLMClient`, coberto por testes automatizados offline com `pytest`.

## Requisitos e Instalação

- Python `>=3.14`
- [`uv`](https://docs.astral.sh/uv/)
- Chave da API do Gemini (`GEMINI_API_KEY`)

```bash
git clone https://github.com/munraitoo13/iara-chat.git
cd iara-chat
uv sync
cp .env.example .env
```

Edite `.env` e informe sua `GEMINI_API_KEY`.

## Como Executar

```bash
# Modo CLI de desenvolvimento
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

- [x] **Core v2:** Empacotamento `src/`, upgrade da stack (`google-genai 3.x`, `pydantic 2.14+`), extração estruturada multi-campo, confirmação/edição, guardrails clínicos e testes offline.
- [ ] **Fase 3 — Interface Web & Persistência:** API com FastAPI, histórico em SQLite/PostgreSQL e Web Chat interativo.
- [ ] **Fase 4 — Agendamento:** Consulta de disponibilidade e reserva de horários em agenda.

## Aviso

Projeto demonstrativo de engenharia de IA. Não realiza diagnóstico médico, prescrição nem substitui atendimento de urgência ou validação clínica humana.
