"""FastAPI application factory, REST endpoints, scheduling, and staff handoff routes."""

import hmac
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from iara.api.schemas import (
    CreateMessageRequest,
    HealthResponse,
    MessageDTO,
    SessionResponse,
    StaffCloseRequest,
    StaffMessageRequest,
    StaffSessionSummary,
    StaffTakeoverRequest,
    TurnResponse,
    build_quick_replies,
)
from iara.config import Settings, get_settings
from iara.conversation.engine import IntakeAgent
from iara.conversation.state import ConversationStatus
from iara.db.repository import LoadedSession, SessionRepository, create_db_engine
from iara.models.scheduling import AppointmentSlotDTO, BookedAppointmentDTO, Specialty
from iara.services.llm import LLMClient, create_llm_client

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _slots_for_state(
    repo: SessionRepository,
    conv_status: ConversationStatus,
    preferred_specialty: Specialty | None,
) -> list[AppointmentSlotDTO]:
    if conv_status != ConversationStatus.SCHEDULING:
        return []
    slots = repo.list_available_slots(specialty=preferred_specialty, limit=4)
    if not slots and preferred_specialty is not None:
        slots = repo.list_available_slots(specialty=None, limit=4)
    return slots


def _build_session_response(
    loaded: LoadedSession,
    repo: SessionRepository,
    *,
    after_message_id: int | None = None,
) -> SessionResponse:
    state = loaded.state
    missing = state.patient_info.missing_fields()
    available_slots = _slots_for_state(repo, state.status, state.patient_info.preferred_specialty)
    is_finished = state.status in (
        ConversationStatus.COMPLETED,
        ConversationStatus.EMERGENCY,
    )

    messages: list[MessageDTO] = []
    for msg_id, item, used_llm in zip(
        loaded.message_ids, state.transcript, loaded.message_llm_flags, strict=True
    ):
        if after_message_id is not None and msg_id <= after_message_id:
            continue
        messages.append(
            MessageDTO(
                id=msg_id,
                actor=item.actor,
                message=item.message,
                used_llm=used_llm,
                timestamp=item.timestamp,
            )
        )

    return SessionResponse(
        session_id=loaded.session_id,
        clinic_name=loaded.clinic_name,
        status=state.status,
        patient_info=state.patient_info,
        missing_fields=missing,
        available_slots=available_slots,
        booked_appointment=state.booked_appointment,
        handoff_reason=state.handoff_reason,
        assigned_staff_name=state.assigned_staff_name,
        quick_replies=build_quick_replies(state.status, missing, available_slots),
        messages=messages,
        is_finished=is_finished,
    )


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
        version="0.3.0",
        description=(
            "API de triagem, agendamento e handoff humanizado em PT-BR com arquitetura híbrida."
        ),
        lifespan=lifespan,
    )

    cors_origins = active_settings.parsed_cors_origins()
    cors_origin_regex = active_settings.configured_cors_origin_regex()
    if cors_origins or cors_origin_regex:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_origin_regex=cors_origin_regex,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def require_staff_key(x_staff_key: str | None = Header(default=None)) -> None:
        expected = active_settings.configured_staff_api_key()
        if expected is None:
            return
        if not x_staff_key or not hmac.compare_digest(x_staff_key, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Chave de acesso da equipe inválida ou ausente.",
            )

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            provider=active_settings.llm_provider,
            model=active_settings.active_model_name(),
            clinic_name=active_settings.clinic_name,
        )

    @app.get("/api/v1/slots", response_model=list[AppointmentSlotDTO], tags=["scheduling"])
    def list_slots(
        specialty: Specialty | None = None,
        limit: int = Query(default=10, ge=1, le=50),
    ) -> list[AppointmentSlotDTO]:
        return active_repo.list_available_slots(specialty=specialty, limit=limit)

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
        return _build_session_response(loaded, active_repo)

    @app.get("/api/v1/sessions/{session_id}", response_model=SessionResponse, tags=["sessions"])
    def get_session(
        session_id: str,
        after_message_id: int | None = Query(default=None, ge=0),
    ) -> SessionResponse:
        loaded = active_repo.load_session(session_id)
        if loaded is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sessão de atendimento não encontrada.",
            )
        return _build_session_response(loaded, active_repo, after_message_id=after_message_id)

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
            scheduler=active_repo,
            session_id=session_id,
        )
        reply = agent.handle_message(payload.message)
        active_repo.save_turn(session_id, agent.state, previous_count, reply)

        missing = agent.state.patient_info.missing_fields()
        available_slots = _slots_for_state(
            active_repo,
            reply.status,
            agent.state.patient_info.preferred_specialty,
        )
        return TurnResponse(
            session_id=session_id,
            reply=reply,
            missing_fields=missing,
            available_slots=available_slots,
            quick_replies=build_quick_replies(reply.status, missing, available_slots),
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

    @app.get(
        "/api/v1/staff/sessions",
        response_model=list[StaffSessionSummary],
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_list_sessions(
        status_filter: Annotated[ConversationStatus | None, Query(alias="status")] = None,
    ) -> list[StaffSessionSummary]:
        rows = active_repo.list_sessions(
            status_filter=status_filter.value if status_filter else None
        )
        return [
            StaffSessionSummary(
                session_id=row.session_id,
                clinic_name=row.clinic_name,
                status=row.status,
                patient_name=row.patient_name,
                chief_complaint=row.chief_complaint,
                preferred_specialty=row.preferred_specialty,
                handoff_reason=row.handoff_reason,
                assigned_staff_name=row.assigned_staff_name,
                turn_count=row.turn_count,
                fast_turns=row.fast_turns,
                llm_turns=row.llm_turns,
                booked_appointment=row.booked_appointment,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    @app.get(
        "/api/v1/staff/appointments",
        response_model=list[BookedAppointmentDTO],
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_list_appointments() -> list[BookedAppointmentDTO]:
        return active_repo.list_appointments()

    @app.post(
        "/api/v1/staff/sessions/{session_id}/takeover",
        response_model=SessionResponse,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_takeover(session_id: str, payload: StaffTakeoverRequest) -> SessionResponse:
        try:
            loaded = active_repo.staff_takeover(session_id, payload.staff_name)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
        if loaded is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sessão de atendimento não encontrada.",
            )
        return _build_session_response(loaded, active_repo)

    @app.post(
        "/api/v1/staff/sessions/{session_id}/messages",
        response_model=SessionResponse,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_send_message(session_id: str, payload: StaffMessageRequest) -> SessionResponse:
        try:
            loaded = active_repo.staff_send_message(
                session_id,
                payload.staff_name,
                payload.message,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
        if loaded is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sessão de atendimento não encontrada.",
            )
        return _build_session_response(loaded, active_repo)

    @app.post(
        "/api/v1/staff/sessions/{session_id}/release",
        response_model=SessionResponse,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_release(session_id: str) -> SessionResponse:
        try:
            loaded = active_repo.staff_release_to_ai(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
        if loaded is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sessão de atendimento não encontrada.",
            )
        return _build_session_response(loaded, active_repo)

    @app.post(
        "/api/v1/staff/sessions/{session_id}/close",
        response_model=SessionResponse,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_close(
        session_id: str,
        payload: StaffCloseRequest | None = None,
    ) -> SessionResponse:
        note = payload.closing_note if payload else None
        loaded = active_repo.staff_close_session(session_id, closing_note=note)
        if loaded is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sessão de atendimento não encontrada.",
            )
        return _build_session_response(loaded, active_repo)

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
