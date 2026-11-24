"""Database engine setup and repository operations for intake, scheduling, and handoff."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, delete, select

from iara.conversation.state import AgentReply, ConversationState, ConversationStatus
from iara.db.models import (
    AppointmentRecord,
    AppointmentSlotRecord,
    IntakeSessionRecord,
    TranscriptMessageRecord,
)
from iara.models.patient import CoverageType, PatientInfo, TranscriptItem
from iara.models.scheduling import (
    SPECIALTY_LABELS_PTBR,
    AppointmentSlotDTO,
    BookedAppointmentDTO,
    Specialty,
)

_CLINIC_TZ = ZoneInfo("America/Sao_Paulo")

_SEED_DOCTORS: tuple[tuple[Specialty, str, tuple[int, int]], ...] = (
    (Specialty.GENERAL_PRACTICE, "Dra. Helena Duarte", (9, 14)),
    (Specialty.ORTHOPEDICS, "Dr. Rafael Monteiro", (10, 15)),
    (Specialty.DERMATOLOGY, "Dra. Camila Vasconcelos", (11, 16)),
    (Specialty.CARDIOLOGY, "Dr. Eduardo Nogueira", (9, 15)),
)


@dataclass(slots=True)
class LoadedSession:
    """Hydrated session state and message metadata loaded from persistence."""

    session_id: str
    clinic_name: str
    state: ConversationState
    message_ids: list[int]
    message_llm_flags: list[bool]


@dataclass(slots=True)
class StaffSessionRow:
    """Operational session summary for clinic staff queue views."""

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
    booked_appointment: BookedAppointmentDTO | None
    updated_at: datetime


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


def _slot_to_dto(record: AppointmentSlotRecord) -> AppointmentSlotDTO:
    specialty = Specialty(record.specialty)
    label = SPECIALTY_LABELS_PTBR[specialty]
    local_starts_at = _ensure_utc(record.starts_at).astimezone(_CLINIC_TZ)
    display_date = local_starts_at.strftime("%d/%m às %H:%M")
    return AppointmentSlotDTO(
        slot_id=record.id or 0,
        specialty=specialty,
        specialty_label=label,
        doctor_name=record.doctor_name,
        starts_at=local_starts_at,
        display_label=f"{display_date} · {record.doctor_name}",
    )


def _appointment_to_dto(record: AppointmentRecord) -> BookedAppointmentDTO:
    specialty = Specialty(record.specialty)
    local_starts_at = _ensure_utc(record.starts_at).astimezone(_CLINIC_TZ)
    return BookedAppointmentDTO(
        appointment_id=record.id,
        slot_id=record.slot_id,
        specialty=specialty,
        specialty_label=SPECIALTY_LABELS_PTBR[specialty],
        doctor_name=record.doctor_name,
        starts_at=local_starts_at,
        status=record.status,
    )


class SessionRepository:
    """Persistence adapter for clinical intake sessions, calendar slots, and staff handoff."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def init_db(self) -> None:
        """Create database tables and seed realistic calendar slots idempotently."""

        SQLModel.metadata.create_all(self.engine)
        self.seed_default_slots()

    def seed_default_slots(self) -> None:
        """Populate initial weekday appointment slots when the calendar table is empty."""

        with Session(self.engine) as db:
            existing = db.exec(select(AppointmentSlotRecord.id)).first()
            if existing is not None:
                return

            weekdays: list[datetime] = []
            cursor = datetime.now(_CLINIC_TZ).replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
            while len(weekdays) < 3:
                if cursor.weekday() < 5:
                    weekdays.append(cursor)
                cursor += timedelta(days=1)

            for day in weekdays:
                for specialty, doctor_name, hours in _SEED_DOCTORS:
                    for hour in hours:
                        minute = 30 if specialty == Specialty.CARDIOLOGY and hour == 9 else 0
                        db.add(
                            AppointmentSlotRecord(
                                specialty=specialty.value,
                                doctor_name=doctor_name,
                                starts_at=day.replace(hour=hour, minute=minute),
                                is_booked=False,
                            )
                        )
            db.commit()

    def list_available_slots(
        self,
        specialty: Specialty | None = None,
        limit: int = 4,
    ) -> list[AppointmentSlotDTO]:
        """List unbooked appointment slots ordered chronologically."""

        with Session(self.engine) as db:
            stmt = select(AppointmentSlotRecord).where(AppointmentSlotRecord.is_booked.is_(False))  # type: ignore[attr-defined]
            if specialty is not None:
                stmt = stmt.where(AppointmentSlotRecord.specialty == specialty.value)
            stmt = stmt.order_by(AppointmentSlotRecord.starts_at).limit(limit)
            rows = list(db.exec(stmt).all())
        return [_slot_to_dto(row) for row in rows]

    def book_slot(
        self,
        session_id: str,
        slot_id: int,
        patient_name: str,
    ) -> BookedAppointmentDTO | None:
        """Atomically book a free calendar slot and link it to an intake session."""

        with Session(self.engine) as db:
            slot = db.get(AppointmentSlotRecord, slot_id)
            if slot is None or slot.is_booked:
                return None

            existing_for_session = db.exec(
                select(AppointmentRecord).where(AppointmentRecord.session_id == session_id)
            ).first()
            if existing_for_session is not None:
                return _appointment_to_dto(existing_for_session)

            slot.is_booked = True
            appointment = AppointmentRecord(
                session_id=session_id,
                slot_id=slot_id,
                patient_name=patient_name,
                specialty=slot.specialty,
                doctor_name=slot.doctor_name,
                starts_at=slot.starts_at,
                status="confirmed",
            )
            db.add(slot)
            db.add(appointment)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return None
            db.refresh(appointment)
            return _appointment_to_dto(appointment)

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
            db.refresh(greeting_record)

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
            message_ids=[greeting_record.id or 0],
            message_llm_flags=[False],
        )

    def load_session(self, session_id: str) -> LoadedSession | None:
        """Hydrate ConversationState, appointment, and transcript metadata for a session ID."""

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return None

            msg_records = list(
                db.exec(
                    select(TranscriptMessageRecord)
                    .where(TranscriptMessageRecord.session_id == session_id)
                    .order_by(TranscriptMessageRecord.id)
                ).all()
            )
            appointment_row = db.exec(
                select(AppointmentRecord).where(AppointmentRecord.session_id == session_id)
            ).first()

        patient_info = PatientInfo(
            full_name=record.full_name,
            date_of_birth=record.date_of_birth,
            address=record.address,
            coverage_type=CoverageType(record.coverage_type) if record.coverage_type else None,
            insurance_payer=record.insurance_payer,
            insurance_id=record.insurance_id,
            chief_complaint=record.chief_complaint,
            preferred_specialty=(
                Specialty(record.preferred_specialty) if record.preferred_specialty else None
            ),
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
            consecutive_llm_errors=record.consecutive_llm_errors,
            handoff_reason=record.handoff_reason,
            assigned_staff_name=record.assigned_staff_name,
            booked_appointment=_appointment_to_dto(appointment_row) if appointment_row else None,
        )
        return LoadedSession(
            session_id=record.id,
            clinic_name=record.clinic_name,
            state=state,
            message_ids=[msg.id or 0 for msg in msg_records],
            message_llm_flags=[msg.used_llm for msg in msg_records],
        )

    def save_turn(
        self,
        session_id: str,
        state: ConversationState,
        previous_message_count: int,
        reply: AgentReply,
    ) -> None:
        """Persist updated patient fields, handoff state, and newly appended messages."""

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return

            patient = state.patient_info
            record.status = state.status.value
            record.turn_count = state.turn_count
            record.consecutive_llm_errors = state.consecutive_llm_errors
            record.handoff_reason = state.handoff_reason
            record.assigned_staff_name = state.assigned_staff_name
            record.full_name = patient.full_name
            record.date_of_birth = patient.date_of_birth
            record.address = patient.address
            record.coverage_type = patient.coverage_type.value if patient.coverage_type else None
            record.insurance_payer = patient.insurance_payer
            record.insurance_id = patient.insurance_id
            record.chief_complaint = patient.chief_complaint
            record.preferred_specialty = (
                patient.preferred_specialty.value if patient.preferred_specialty else None
            )
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
        """Delete a session, its transcript, and release any linked appointment (LGPD erasure)."""

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return False

            appointments = list(
                db.exec(
                    select(AppointmentRecord).where(AppointmentRecord.session_id == session_id)
                ).all()
            )
            for appt in appointments:
                slot = db.get(AppointmentSlotRecord, appt.slot_id)
                if slot is not None:
                    slot.is_booked = False
                    db.add(slot)
                db.delete(appt)

            db.exec(
                delete(TranscriptMessageRecord).where(
                    TranscriptMessageRecord.session_id == session_id
                )
            )
            db.delete(record)
            db.commit()
            return True

    def list_sessions(self, status_filter: str | None = None) -> list[StaffSessionRow]:
        """List intake sessions with token efficiency counters for the staff dashboard."""

        with Session(self.engine) as db:
            stmt = select(IntakeSessionRecord).order_by(IntakeSessionRecord.updated_at.desc())  # type: ignore[attr-defined]
            if status_filter:
                stmt = stmt.where(IntakeSessionRecord.status == status_filter)
            records = list(db.exec(stmt).all())

            rows: list[StaffSessionRow] = []
            for rec in records:
                msgs = list(
                    db.exec(
                        select(TranscriptMessageRecord).where(
                            TranscriptMessageRecord.session_id == rec.id
                        )
                    ).all()
                )
                bot_msgs = [m for m in msgs if m.actor in ("assistant", "system")][1:]
                llm_turns = sum(1 for m in bot_msgs if m.used_llm)
                fast_turns = sum(1 for m in bot_msgs if not m.used_llm)
                appt = db.exec(
                    select(AppointmentRecord).where(AppointmentRecord.session_id == rec.id)
                ).first()
                rows.append(
                    StaffSessionRow(
                        session_id=rec.id,
                        clinic_name=rec.clinic_name,
                        status=ConversationStatus(rec.status),
                        patient_name=rec.full_name,
                        chief_complaint=rec.chief_complaint,
                        preferred_specialty=(
                            Specialty(rec.preferred_specialty) if rec.preferred_specialty else None
                        ),
                        handoff_reason=rec.handoff_reason,
                        assigned_staff_name=rec.assigned_staff_name,
                        turn_count=rec.turn_count,
                        fast_turns=fast_turns,
                        llm_turns=llm_turns,
                        booked_appointment=_appointment_to_dto(appt) if appt else None,
                        updated_at=_ensure_utc(rec.updated_at),
                    )
                )
            return rows

    def list_appointments(self) -> list[BookedAppointmentDTO]:
        """Return all confirmed appointments sorted chronologically."""

        with Session(self.engine) as db:
            rows = list(
                db.exec(
                    select(AppointmentRecord).order_by(AppointmentRecord.starts_at)  # type: ignore[arg-type]
                ).all()
            )
        return [_appointment_to_dto(row) for row in rows]

    def staff_takeover(self, session_id: str, staff_name: str) -> LoadedSession | None:
        """Pause AI automation and assign the conversation to a human staff member."""

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return None
            if record.status in (
                ConversationStatus.COMPLETED.value,
                ConversationStatus.EMERGENCY.value,
            ):
                raise ValueError("Cannot take over a finished or emergency session.")

            now = datetime.now(UTC)
            record.status = ConversationStatus.HUMAN_TAKEOVER.value
            record.assigned_staff_name = staff_name.strip()
            if not record.handoff_reason:
                record.handoff_reason = "staff_manual_takeover"
            record.updated_at = now
            db.add(record)
            db.add(
                TranscriptMessageRecord(
                    session_id=session_id,
                    actor="system",
                    message=f"Atendimento humano assumido por {record.assigned_staff_name}.",
                    used_llm=False,
                    created_at=now,
                )
            )
            db.commit()

        return self.load_session(session_id)

    def staff_send_message(
        self,
        session_id: str,
        staff_name: str,
        message: str,
    ) -> LoadedSession | None:
        """Append a message sent by a human attendant while session is in takeover mode."""

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return None
            if record.status != ConversationStatus.HUMAN_TAKEOVER.value:
                raise ValueError("Session must be in human_takeover before staff can send replies.")

            now = datetime.now(UTC)
            if not record.assigned_staff_name:
                record.assigned_staff_name = staff_name.strip()
            record.updated_at = now
            db.add(record)
            db.add(
                TranscriptMessageRecord(
                    session_id=session_id,
                    actor="human_agent",
                    message=message.strip(),
                    used_llm=False,
                    created_at=now,
                )
            )
            db.commit()

        return self.load_session(session_id)

    def staff_release_to_ai(self, session_id: str) -> LoadedSession | None:
        """Return a handoff session back to the Iara AI assistant."""

        loaded = self.load_session(session_id)
        if loaded is None:
            return None

        if loaded.state.status not in (
            ConversationStatus.WAITING_HUMAN,
            ConversationStatus.HUMAN_TAKEOVER,
        ):
            raise ValueError("Only waiting_human or human_takeover sessions can return to AI.")

        if loaded.state.booked_appointment is not None:
            next_status = ConversationStatus.COMPLETED
        elif loaded.state.patient_info.is_complete():
            next_status = (
                ConversationStatus.SCHEDULING
                if loaded.state.patient_info.preferred_specialty is not None
                else ConversationStatus.CONFIRMING
            )
        else:
            next_status = ConversationStatus.COLLECTING

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return None
            now = datetime.now(UTC)
            record.status = next_status.value
            record.consecutive_llm_errors = 0
            record.handoff_reason = None
            record.assigned_staff_name = None
            record.updated_at = now
            db.add(record)
            db.add(
                TranscriptMessageRecord(
                    session_id=session_id,
                    actor="system",
                    message="Atendimento retomado pela assistente virtual Iara.",
                    used_llm=False,
                    created_at=now,
                )
            )
            db.commit()

        return self.load_session(session_id)

    def staff_close_session(
        self,
        session_id: str,
        closing_note: str | None = None,
    ) -> LoadedSession | None:
        """Mark an active or human-handled session as completed."""

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return None

            now = datetime.now(UTC)
            record.status = ConversationStatus.COMPLETED.value
            record.updated_at = now
            db.add(record)
            if closing_note and closing_note.strip():
                db.add(
                    TranscriptMessageRecord(
                        session_id=session_id,
                        actor="human_agent",
                        message=closing_note.strip(),
                        used_llm=False,
                        created_at=now,
                    )
                )
            db.commit()

        return self.load_session(session_id)
