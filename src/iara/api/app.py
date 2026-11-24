"""FastAPI application factory, REST endpoints, and static Web Chat mounting."""

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from iara.api.schemas import (
    CreateMessageRequest,
    HealthResponse,
    MessageDTO,
    SessionResponse,
    TurnResponse,
    build_quick_replies,
)
from iara.config import Settings, get_settings
from iara.conversation.engine import IntakeAgent
from iara.conversation.state import ConversationStatus
from iara.db.repository import SessionRepository, create_db_engine
from iara.services.llm import LLMClient, create_llm_client

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(
    *,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    repository: SessionRepository | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application with injected or lazy dependencies."""

    active_settings = settings or get_settings()
    active_llm = llm or create_llm_client(active_settings)
    active_repo = repository or SessionRepository(create_db_engine(active_settings.database_url))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        active_repo.init_db()
        yield

    app = FastAPI(
        title="Iara Chat API",
        version="0.2.0",
        description="API de triagem e pré-cadastro clínico em PT-BR com arquitetura híbrida.",
        lifespan=lifespan,
    )

    cors_origins = active_settings.parsed_cors_origins()
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            provider=active_settings.llm_provider,
            model=active_settings.active_model_name(),
            clinic_name=active_settings.clinic_name,
        )

    @app.post(
        "/api/v1/sessions",
        response_model=SessionResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["sessions"],
    )
    def create_session() -> SessionResponse:
        agent = IntakeAgent(
            llm=active_llm,
            clinic_name=active_settings.clinic_name,
            max_input_chars=active_settings.max_input_chars,
            max_turns=active_settings.max_turns,
        )
        greeting = agent.greeting()
        loaded = active_repo.create_session(active_settings.clinic_name, greeting)
        missing = loaded.state.patient_info.missing_fields()
        return SessionResponse(
            session_id=loaded.session_id,
            clinic_name=loaded.clinic_name,
            status=loaded.state.status,
            patient_info=loaded.state.patient_info,
            missing_fields=missing,
            quick_replies=build_quick_replies(loaded.state.status, missing),
            messages=[
                MessageDTO(
                    actor=item.actor,
                    message=item.message,
                    used_llm=used_llm,
                    timestamp=item.timestamp,
                )
                for item, used_llm in zip(
                    loaded.state.transcript, loaded.message_llm_flags, strict=True
                )
            ],
            is_finished=False,
        )

    @app.get("/api/v1/sessions/{session_id}", response_model=SessionResponse, tags=["sessions"])
    def get_session(session_id: str) -> SessionResponse:
        loaded = active_repo.load_session(session_id)
        if loaded is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sessão de atendimento não encontrada.",
            )

        missing = loaded.state.patient_info.missing_fields()
        is_finished = loaded.state.status in (
            ConversationStatus.COMPLETED,
            ConversationStatus.EMERGENCY,
        )
        return SessionResponse(
            session_id=loaded.session_id,
            clinic_name=loaded.clinic_name,
            status=loaded.state.status,
            patient_info=loaded.state.patient_info,
            missing_fields=missing,
            quick_replies=build_quick_replies(loaded.state.status, missing),
            messages=[
                MessageDTO(
                    actor=item.actor,
                    message=item.message,
                    used_llm=used_llm,
                    timestamp=item.timestamp,
                )
                for item, used_llm in zip(
                    loaded.state.transcript, loaded.message_llm_flags, strict=True
                )
            ],
            is_finished=is_finished,
        )

    @app.post(
        "/api/v1/sessions/{session_id}/messages",
        response_model=TurnResponse,
        tags=["sessions"],
    )
    def post_message(session_id: str, payload: CreateMessageRequest) -> TurnResponse:
        loaded = active_repo.load_session(session_id)
        if loaded is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sessão de atendimento não encontrada.",
            )

        previous_count = len(loaded.state.transcript)
        agent = IntakeAgent(
            llm=active_llm,
            clinic_name=loaded.clinic_name,
            max_input_chars=active_settings.max_input_chars,
            max_turns=active_settings.max_turns,
            state=loaded.state,
        )
        reply = agent.handle_message(payload.message)
        active_repo.save_turn(session_id, agent.state, previous_count, reply)

        missing = agent.state.patient_info.missing_fields()
        return TurnResponse(
            session_id=session_id,
            reply=reply,
            missing_fields=missing,
            quick_replies=build_quick_replies(reply.status, missing),
        )

    @app.delete(
        "/api/v1/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["sessions"],
    )
    def delete_session(session_id: str) -> Response:
        if not active_repo.delete_session(session_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sessão de atendimento não encontrada.",
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if _WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=_WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(_WEB_DIR / "index.html")

    return app


def run() -> None:
    """Start the FastAPI development server via `uv run iara-api`."""

    try:
        settings = get_settings()
    except ValidationError as exc:
        print(
            "Erro de configuração: copie .env.example para .env e defina "
            "AI_GATEWAY_API_KEY (ou GEMINI_API_KEY se LLM_PROVIDER=gemini).\n"
            f"Detalhe: {exc.errors()[0]['msg']}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    app = create_app(settings=settings)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
