"""Conversation state models and public response contract."""

from enum import StrEnum

from pydantic import BaseModel, Field

from iara.models.patient import PatientInfo, TranscriptItem
from iara.models.scheduling import BookedAppointmentDTO


class ConversationStatus(StrEnum):
    """High-level lifecycle states for patient intake, scheduling, and human handoff."""

    COLLECTING = "collecting"
    CONFIRMING = "confirming"
    SCHEDULING = "scheduling"
    WAITING_HUMAN = "waiting_human"
    HUMAN_TAKEOVER = "human_takeover"
    EMERGENCY = "emergency"
    COMPLETED = "completed"


class ConversationState(BaseModel):
    """Mutable session state tracked across intake, scheduling, and handoff turns."""

    status: ConversationStatus = ConversationStatus.COLLECTING
    patient_info: PatientInfo = Field(default_factory=PatientInfo)
    transcript: list[TranscriptItem] = Field(default_factory=list)
    turn_count: int = 0
    consecutive_llm_errors: int = 0
    handoff_reason: str | None = None
    assigned_staff_name: str | None = None
    booked_appointment: BookedAppointmentDTO | None = None


class AgentReply(BaseModel):
    """Structured response returned by IntakeAgent after processing one user message."""

    message: str
    status: ConversationStatus
    patient_info: PatientInfo
    is_finished: bool
    used_llm: bool = False
    booked_appointment: BookedAppointmentDTO | None = None
    error: str | None = None
