"""Conversation state models and public response contract."""

from enum import StrEnum

from pydantic import BaseModel, Field

from iara.models.patient import PatientInfo, TranscriptItem


class ConversationStatus(StrEnum):
    """High-level lifecycle states for patient intake."""

    COLLECTING = "collecting"
    CONFIRMING = "confirming"
    EMERGENCY = "emergency"
    COMPLETED = "completed"


class ConversationState(BaseModel):
    """Mutable session state tracked across intake turns."""

    status: ConversationStatus = ConversationStatus.COLLECTING
    patient_info: PatientInfo = Field(default_factory=PatientInfo)
    transcript: list[TranscriptItem] = Field(default_factory=list)
    turn_count: int = 0


class AgentReply(BaseModel):
    """Structured response returned by IntakeAgent after processing one user message."""

    message: str
    status: ConversationStatus
    patient_info: PatientInfo
    is_finished: bool
    used_llm: bool = False
    error: str | None = None
