"""Pydantic request/response schemas for the versioned FastAPI layer."""

from datetime import datetime

from pydantic import BaseModel, Field

from iara.conversation.state import AgentReply, ConversationStatus
from iara.models.patient import PatientInfo


class QuickReplyOption(BaseModel):
    """Actionable quick-reply chip rendered in the UI for 0-token turns."""

    label: str
    value: str


class MessageDTO(BaseModel):
    """Serialized conversation message returned by the API."""

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
    quick_replies: list[QuickReplyOption]
    messages: list[MessageDTO]
    is_finished: bool


class TurnResponse(BaseModel):
    """Response returned after processing a single user message."""

    session_id: str
    reply: AgentReply
    missing_fields: list[str]
    quick_replies: list[QuickReplyOption]


class HealthResponse(BaseModel):
    """Service health and active provider metadata."""

    status: str
    provider: str
    model: str
    clinic_name: str


def build_quick_replies(
    status: ConversationStatus,
    missing_fields: list[str],
) -> list[QuickReplyOption]:
    """Return contextual quick-reply chips that map directly to 0-token fast-paths."""

    if status == ConversationStatus.CONFIRMING:
        return [
            QuickReplyOption(label="Sim, confirmar", value="sim"),
            QuickReplyOption(label="Corrigir dados", value="não"),
        ]
    if status == ConversationStatus.COLLECTING and missing_fields:
        if missing_fields[0] == "coverage_type":
            return [
                QuickReplyOption(label="Convênio", value="convênio"),
                QuickReplyOption(label="Particular", value="particular"),
            ]
    return []
