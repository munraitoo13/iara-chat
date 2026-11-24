"""Pydantic request/response schemas for the versioned FastAPI layer."""

from datetime import datetime

from pydantic import BaseModel, Field

from iara.conversation.state import AgentReply, ConversationStatus
from iara.models.patient import PatientInfo
from iara.models.scheduling import AppointmentSlotDTO, BookedAppointmentDTO, Specialty


class QuickReplyOption(BaseModel):
    """Actionable quick-reply chip rendered in the UI for 0-token turns."""

    label: str
    value: str


class MessageDTO(BaseModel):
    """Serialized conversation message returned by the API."""

    id: int | None = None
    actor: str
    message: str
    used_llm: bool
    timestamp: datetime


class CreateMessageRequest(BaseModel):
    """Incoming user message payload."""

    message: str = Field(min_length=1, max_length=2000)


class SessionResponse(BaseModel):
    """Full intake session state returned on session creation or restoration."""

    session_id: str
    clinic_name: str
    status: ConversationStatus
    patient_info: PatientInfo
    missing_fields: list[str]
    available_slots: list[AppointmentSlotDTO] = Field(default_factory=list)
    booked_appointment: BookedAppointmentDTO | None = None
    handoff_reason: str | None = None
    assigned_staff_name: str | None = None
    quick_replies: list[QuickReplyOption]
    messages: list[MessageDTO]
    is_finished: bool


class TurnResponse(BaseModel):
    """Response returned after processing a single user message."""

    session_id: str
    reply: AgentReply
    missing_fields: list[str]
    available_slots: list[AppointmentSlotDTO] = Field(default_factory=list)
    quick_replies: list[QuickReplyOption]


class HealthResponse(BaseModel):
    """Service health and active provider metadata."""

    status: str
    provider: str
    model: str
    clinic_name: str


class StaffSessionSummary(BaseModel):
    """Operational session view for the clinic staff queue."""

    session_id: str
    clinic_name: str
    status: ConversationStatus
    patient_name: str | None
    chief_complaint: str | None
    preferred_specialty: Specialty | None
    handoff_reason: str | None
    assigned_staff_name: str | None
    turn_count: int
    fast_turns: int
    llm_turns: int
    booked_appointment: BookedAppointmentDTO | None = None
    updated_at: datetime


class StaffTakeoverRequest(BaseModel):
    """Request payload to assume a live patient conversation."""

    staff_name: str = Field(min_length=2, max_length=100)


class StaffMessageRequest(BaseModel):
    """Request payload for a human attendant reply during takeover."""

    staff_name: str = Field(min_length=2, max_length=100)
    message: str = Field(min_length=1, max_length=2000)


class StaffCloseRequest(BaseModel):
    """Optional closing note when staff marks a session completed."""

    closing_note: str | None = Field(default=None, max_length=1000)


def build_quick_replies(
    status: ConversationStatus,
    missing_fields: list[str],
    available_slots: list[AppointmentSlotDTO] | None = None,
) -> list[QuickReplyOption]:
    """Return contextual quick-reply chips that map directly to 0-token fast-paths."""

    if status == ConversationStatus.CONFIRMING:
        return [
            QuickReplyOption(label="Sim, confirmar", value="sim"),
            QuickReplyOption(label="Corrigir dados", value="não"),
        ]
    if status == ConversationStatus.SCHEDULING and available_slots:
        return [
            QuickReplyOption(label=slot.display_label, value=f"slot:{slot.slot_id}")
            for slot in available_slots
        ]
    if status == ConversationStatus.COLLECTING and missing_fields:
        if missing_fields[0] == "coverage_type":
            return [
                QuickReplyOption(label="Convênio", value="convênio"),
                QuickReplyOption(label="Particular", value="particular"),
            ]
    return []
