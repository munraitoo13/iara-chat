"""SQLModel persistence tables for intake sessions and conversation transcripts."""

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel

from iara.conversation.state import ConversationStatus


class IntakeSessionRecord(SQLModel, table=True):
    """Persistent clinical intake session and structured patient fields."""

    __tablename__ = "intake_sessions"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    clinic_name: str
    status: str = Field(default=ConversationStatus.COLLECTING.value)
    turn_count: int = 0

    full_name: str | None = None
    date_of_birth: date | None = None
    address: str | None = None
    coverage_type: str | None = None
    insurance_payer: str | None = None
    insurance_id: str | None = None
    chief_complaint: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TranscriptMessageRecord(SQLModel, table=True):
    """Persistent message log linked to an intake session."""

    __tablename__ = "transcript_messages"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="intake_sessions.id", index=True)
    actor: str
    message: str
    used_llm: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
