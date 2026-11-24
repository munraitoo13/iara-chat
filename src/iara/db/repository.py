"""Database engine setup and repository operations for intake sessions."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, SQLModel, create_engine, delete, select

from iara.conversation.state import AgentReply, ConversationState, ConversationStatus
from iara.db.models import IntakeSessionRecord, TranscriptMessageRecord
from iara.models.patient import CoverageType, PatientInfo, TranscriptItem


@dataclass(slots=True)
class LoadedSession:
    """Hydrated session state and message metadata loaded from persistence."""

    session_id: str
    clinic_name: str
    state: ConversationState
    message_llm_flags: list[bool]


def create_db_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy/SQLModel engine ready for SQLite or PostgreSQL."""

    url = make_url(database_url)
    connect_args: dict[str, object] = {}

    if url.drivername.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    return create_engine(database_url, connect_args=connect_args)


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure SQLite naive datetimes are converted back to UTC-aware datetimes."""

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class SessionRepository:
    """Persistence adapter for clinical intake sessions and transcripts."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def init_db(self) -> None:
        """Create database tables if they do not exist yet."""

        SQLModel.metadata.create_all(self.engine)

    def create_session(self, clinic_name: str, greeting_message: str) -> LoadedSession:
        """Persist a new intake session along with its initial greeting."""

        now = datetime.now(UTC)
        session_record = IntakeSessionRecord(
            clinic_name=clinic_name,
            status=ConversationStatus.COLLECTING.value,
            turn_count=0,
            created_at=now,
            updated_at=now,
        )
        greeting_item = TranscriptItem(actor="assistant", message=greeting_message, timestamp=now)
        greeting_record = TranscriptMessageRecord(
            session_id=session_record.id,
            actor=greeting_item.actor,
            message=greeting_item.message,
            used_llm=False,
            created_at=now,
        )

        with Session(self.engine) as db:
            db.add(session_record)
            db.add(greeting_record)
            db.commit()
            db.refresh(session_record)

        state = ConversationState(
            status=ConversationStatus.COLLECTING,
            patient_info=PatientInfo(),
            transcript=[greeting_item],
            turn_count=0,
        )
        return LoadedSession(
            session_id=session_record.id,
            clinic_name=session_record.clinic_name,
            state=state,
            message_llm_flags=[False],
        )

    def load_session(self, session_id: str) -> LoadedSession | None:
        """Hydrate ConversationState and transcript metadata for a session ID."""

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return None

            stmt = (
                select(TranscriptMessageRecord)
                .where(TranscriptMessageRecord.session_id == session_id)
                .order_by(TranscriptMessageRecord.id)
            )
            msg_records = list(db.exec(stmt).all())

        patient_info = PatientInfo(
            full_name=record.full_name,
            date_of_birth=record.date_of_birth,
            address=record.address,
            coverage_type=CoverageType(record.coverage_type) if record.coverage_type else None,
            insurance_payer=record.insurance_payer,
            insurance_id=record.insurance_id,
            chief_complaint=record.chief_complaint,
        )
        transcript = [
            TranscriptItem(
                actor=msg.actor,  # type: ignore[arg-type]
                message=msg.message,
                timestamp=_ensure_utc(msg.created_at),
            )
            for msg in msg_records
        ]
        state = ConversationState(
            status=ConversationStatus(record.status),
            patient_info=patient_info,
            transcript=transcript,
            turn_count=record.turn_count,
        )
        return LoadedSession(
            session_id=record.id,
            clinic_name=record.clinic_name,
            state=state,
            message_llm_flags=[msg.used_llm for msg in msg_records],
        )

    def save_turn(
        self,
        session_id: str,
        state: ConversationState,
        previous_message_count: int,
        reply: AgentReply,
    ) -> None:
        """Persist updated patient fields, status, and newly appended transcript messages."""

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return

            patient = state.patient_info
            record.status = state.status.value
            record.turn_count = state.turn_count
            record.full_name = patient.full_name
            record.date_of_birth = patient.date_of_birth
            record.address = patient.address
            record.coverage_type = patient.coverage_type.value if patient.coverage_type else None
            record.insurance_payer = patient.insurance_payer
            record.insurance_id = patient.insurance_id
            record.chief_complaint = patient.chief_complaint
            record.updated_at = datetime.now(UTC)
            db.add(record)

            new_items = state.transcript[previous_message_count:]
            for idx, item in enumerate(new_items):
                is_final_reply = idx == len(new_items) - 1 and item.actor != "user"
                db.add(
                    TranscriptMessageRecord(
                        session_id=session_id,
                        actor=item.actor,
                        message=item.message,
                        used_llm=reply.used_llm if is_final_reply else False,
                        created_at=item.timestamp,
                    )
                )
            db.commit()

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all of its transcript messages (LGPD erasure)."""

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return False

            db.exec(
                delete(TranscriptMessageRecord).where(
                    TranscriptMessageRecord.session_id == session_id
                )
            )
            db.delete(record)
            db.commit()
            return True
