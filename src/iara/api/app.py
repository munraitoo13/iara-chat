"""FastAPI application factory, REST endpoints, scheduling, and staff handoff routes."""

import asyncio
import hmac
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
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
    DispatchRemindersResponse,
    HealthResponse,
    MessageDTO,
    SessionResponse,
    StaffBatchSlotsRequest,
    StaffBookAppointmentRequest,
    StaffCancelAppointmentRequest,
    StaffCloseRequest,
    StaffCreateSlotRequest,
    StaffExtractResponse,
    StaffMessageRequest,
    StaffOfferSlotsRequest,
    StaffRescheduleAppointmentRequest,
    StaffSessionSummary,
    StaffTakeoverRequest,
    StaffUpdatePatientRequest,
    TurnResponse,
    build_quick_replies,
)
from iara.config import Settings, get_settings
from iara.conversation.engine import IntakeAgent
from iara.conversation.guardrails import validate_and_normalize_fields
from iara.conversation.prompts import COPILOT_EXTRACTION_SYSTEM_PROMPT, build_analysis_payload
from iara.conversation.state import ConversationStatus
from iara.db.repository import LoadedSession, SessionRepository, create_db_engine
from iara.models.patient import CoverageType
from iara.models.scheduling import (
    AppointmentSlotDTO,
    BookedAppointmentDTO,
    NoticeWindowError,
    ReminderDTO,
    Specialty,
    suggest_specialty_from_complaint,
)
from iara.services.llm import LLMClient, LLMError, create_llm_client
from iara.services.reminders import ReminderSender, create_reminder_sender

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
        ConversationStatus.EXPIRED,
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
    reminder_sender: ReminderSender | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application with injected or lazy dependencies."""

    active_settings = settings or get_settings()
    active_llm = llm or create_llm_client(active_settings)
    active_repo = repository or SessionRepository(
        create_db_engine(active_settings.database_url),
        session_timeout_minutes=active_settings.session_timeout_minutes,
        min_cancel_notice_hours=active_settings.min_cancel_notice_hours,
    )
    default_channel, default_sender = create_reminder_sender(active_settings)
    active_sender = reminder_sender or default_sender
    active_channel = default_channel if reminder_sender is None else "mock"

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        active_repo.init_db()
        active_repo.dispatch_due_reminders(active_sender, active_channel)

        async def _reminder_loop() -> None:
            while True:
                await asyncio.sleep(60)
                active_repo.dispatch_due_reminders(active_sender, active_channel)

        task = asyncio.create_task(_reminder_loop())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

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
            min_cancel_notice_hours=active_settings.min_cancel_notice_hours,
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

    @app.patch(
        "/api/v1/staff/sessions/{session_id}/patient",
        response_model=SessionResponse,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_update_patient(
        session_id: str,
        payload: StaffUpdatePatientRequest,
    ) -> SessionResponse:
        try:
            loaded = active_repo.staff_update_patient(
                session_id,
                payload.model_dump(exclude_unset=True),
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
        "/api/v1/staff/sessions/{session_id}/book",
        response_model=SessionResponse,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_book_appointment(
        session_id: str,
        payload: StaffBookAppointmentRequest,
    ) -> SessionResponse:
        try:
            loaded = active_repo.staff_book_appointment(
                session_id,
                payload.slot_id,
                payload.staff_name,
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
        "/api/v1/staff/sessions/{session_id}/offer-slots",
        response_model=SessionResponse,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_offer_slots(
        session_id: str,
        payload: StaffOfferSlotsRequest | None = None,
    ) -> SessionResponse:
        specialty = payload.specialty if payload else None
        try:
            loaded = active_repo.staff_offer_slots_in_chat(session_id, specialty=specialty)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
        if loaded is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sessão de atendimento não encontrada.",
            )
        return _build_session_response(loaded, active_repo)

    @app.post(
        "/api/v1/staff/sessions/{session_id}/extract",
        response_model=StaffExtractResponse,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_extract_patient(session_id: str) -> StaffExtractResponse:
        loaded = active_repo.load_session(session_id)
        if loaded is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sessão de atendimento não encontrada.",
            )

        recent = [
            {"actor": item.actor, "message": item.message} for item in loaded.state.transcript[-16:]
        ]
        payload = build_analysis_payload(
            status=loaded.state.status,
            patient_info=loaded.state.patient_info,
            recent_messages=recent,
            user_input="Extrair e consolidar dados do paciente da conversa recente.",
        )

        try:
            analysis = active_llm.analyze_turn(COPILOT_EXTRACTION_SYSTEM_PROMPT, payload)
        except LLMError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Falha ao extrair ficha com IA: {exc}",
            ) from None

        updates, warnings = validate_and_normalize_fields(analysis.extracted)
        candidate = loaded.state.patient_info.model_copy(deep=True)
        for field_name, value in updates.items():
            setattr(candidate, field_name, value)

        if candidate.coverage_type == CoverageType.PRIVATE:
            candidate.insurance_payer = None
            candidate.insurance_id = None
        elif (
            "insurance_payer" in updates or "insurance_id" in updates
        ) and candidate.coverage_type is None:
            candidate.coverage_type = CoverageType.INSURANCE

        suggested = (
            Specialty(analysis.requested_specialty)
            if analysis.requested_specialty
            else (
                candidate.preferred_specialty
                or suggest_specialty_from_complaint(candidate.chief_complaint)
            )
        )
        candidate.preferred_specialty = suggested

        return StaffExtractResponse(
            extracted_patient=candidate,
            suggested_specialty=suggested,
            warnings=warnings,
        )

    @app.post(
        "/api/v1/staff/slots",
        response_model=AppointmentSlotDTO,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_create_slot(payload: StaffCreateSlotRequest) -> AppointmentSlotDTO:
        return active_repo.create_slot(
            specialty=payload.specialty,
            doctor_name=payload.doctor_name,
            starts_at=payload.starts_at,
        )

    @app.post(
        "/api/v1/staff/slots/batch",
        response_model=list[AppointmentSlotDTO],
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_create_slots_batch(payload: StaffBatchSlotsRequest) -> list[AppointmentSlotDTO]:
        try:
            return active_repo.create_slots_batch(
                specialty=payload.specialty,
                doctor_name=payload.doctor_name,
                target_date=payload.target_date,
                start_hour=payload.start_hour,
                end_hour=payload.end_hour,
                interval_minutes=payload.interval_minutes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    @app.delete(
        "/api/v1/staff/slots/{slot_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_delete_slot(slot_id: int) -> Response:
        try:
            deleted = active_repo.delete_slot(slot_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Horário não encontrado.",
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/api/v1/staff/appointments/{appointment_id}/cancel",
        response_model=BookedAppointmentDTO,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_cancel_appointment(
        appointment_id: str,
        payload: StaffCancelAppointmentRequest | None = None,
    ) -> BookedAppointmentDTO:
        reason = payload.reason if payload else None
        try:
            cancelled = active_repo.cancel_appointment(
                appointment_id,
                reason=reason,
                enforce_notice_window=False,
            )
        except (NoticeWindowError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
        if cancelled is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Consulta não encontrada.",
            )
        return cancelled

    @app.post(
        "/api/v1/staff/appointments/{appointment_id}/reschedule",
        response_model=BookedAppointmentDTO,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_reschedule_appointment(
        appointment_id: str,
        payload: StaffRescheduleAppointmentRequest,
    ) -> BookedAppointmentDTO:
        try:
            updated = active_repo.reschedule_appointment(
                appointment_id,
                payload.new_slot_id,
                enforce_notice_window=False,
            )
        except (NoticeWindowError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Consulta não encontrada.",
            )
        return updated

    @app.get(
        "/api/v1/staff/reminders",
        response_model=list[ReminderDTO],
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_list_reminders(
        status_filter: Annotated[str | None, Query(alias="status")] = None,
    ) -> list[ReminderDTO]:
        return active_repo.list_reminders(status_filter=status_filter)

    @app.post(
        "/api/v1/staff/reminders/{reminder_id}/send-now",
        response_model=ReminderDTO,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_send_reminder_now(reminder_id: int) -> ReminderDTO:
        try:
            dispatched = active_repo.dispatch_reminder_by_id(
                reminder_id,
                active_sender,
                active_channel,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
        if dispatched is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lembrete não encontrado.",
            )
        return dispatched

    @app.post(
        "/api/v1/staff/reminders/dispatch-due",
        response_model=DispatchRemindersResponse,
        dependencies=[Depends(require_staff_key)],
        tags=["staff"],
    )
    def staff_dispatch_due_reminders() -> DispatchRemindersResponse:
        count = active_repo.dispatch_due_reminders(active_sender, active_channel)
        return DispatchRemindersResponse(dispatched=count, channel=active_channel)

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
