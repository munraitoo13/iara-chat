"""Database engine setup and repository operations for intake, scheduling, and reminders."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, SQLModel, create_engine, delete, select

from iara.conversation.state import AgentReply, ConversationState, ConversationStatus
from iara.db.models import (
    AppointmentRecord,
    AppointmentReminderRecord,
    AppointmentSlotRecord,
    IntakeSessionRecord,
    TranscriptMessageRecord,
)
from iara.models.patient import CoverageType, PatientInfo, TranscriptItem
from iara.models.scheduling import (
    SPECIALTY_LABELS_PTBR,
    AppointmentSlotDTO,
    BookedAppointmentDTO,
    NoticeWindowError,
    ReminderDTO,
    Specialty,
    suggest_specialty_from_complaint,
)
from iara.services.reminders import ReminderSender

_CLINIC_TZ = ZoneInfo("America/Sao_Paulo")

_OPEN_STATUSES: tuple[str, ...] = (
    ConversationStatus.COLLECTING.value,
    ConversationStatus.CONFIRMING.value,
    ConversationStatus.SCHEDULING.value,
    ConversationStatus.WAITING_HUMAN.value,
    ConversationStatus.HUMAN_TAKEOVER.value,
)

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


def _to_utc(dt: datetime) -> datetime:
    """Normalize local or naive datetimes (assumed clinic TZ) to UTC."""

    if dt.tzinfo is None:
        return dt.replace(tzinfo=_CLINIC_TZ).astimezone(UTC)
    return dt.astimezone(UTC)


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
        patient_name=record.patient_name,
        specialty=specialty,
        specialty_label=SPECIALTY_LABELS_PTBR[specialty],
        doctor_name=record.doctor_name,
        starts_at=local_starts_at,
        status=record.status,
    )


def _reminder_to_dto(record: AppointmentReminderRecord) -> ReminderDTO:
    return ReminderDTO(
        reminder_id=record.id or 0,
        appointment_id=record.appointment_id,
        patient_name=record.patient_name,
        recipient_phone=record.recipient_phone,
        recipient_email=record.recipient_email,
        reminder_type=record.reminder_type,
        channel=record.channel,
        scheduled_for=_ensure_utc(record.scheduled_for).astimezone(_CLINIC_TZ),
        status=record.status,
        message_preview=record.message_preview,
        sent_at=_ensure_utc(record.sent_at).astimezone(_CLINIC_TZ) if record.sent_at else None,
        last_error=record.last_error,
    )


def _build_reminder_preview(
    *,
    clinic_name: str,
    patient_name: str,
    doctor_name: str,
    specialty_label: str,
    starts_at: datetime,
    reminder_type: str,
    protocol: str,
) -> str:
    local_dt = _ensure_utc(starts_at).astimezone(_CLINIC_TZ)
    date_str = local_dt.strftime("%d/%m/%Y às %H:%M")
    if reminder_type == "24h":
        return (
            f"Olá, {patient_name}! Lembrete da {clinic_name}: sua consulta de "
            f"{specialty_label} com {doctor_name} é amanhã, {date_str} (Protocolo #{protocol}). "
            "Se precisar remarcar ou cancelar, avise com antecedência."
        )
    return (
        f"Olá, {patient_name}! Sua consulta na {clinic_name} com {doctor_name} "
        f"({specialty_label}) começa em cerca de 2 horas ({date_str}, Protocolo #{protocol}). "
        "Lembre-se de levar documento com foto e carteirinha do convênio, se aplicável."
    )


class SessionRepository:
    """Persistence adapter for clinical intake, calendar slots, bookings, and reminders."""

    def __init__(
        self,
        engine: Engine,
        session_timeout_minutes: int = 20,
        min_cancel_notice_hours: int = 2,
    ) -> None:
        self.engine = engine
        self.session_timeout_minutes = session_timeout_minutes
        self.min_cancel_notice_hours = min_cancel_notice_hours

    def init_db(self) -> None:
        """Create database tables and seed realistic calendar slots idempotently."""

        SQLModel.metadata.create_all(self.engine)
        self.seed_default_slots()

    def expire_stale_sessions(self, *, now: datetime | None = None) -> int:
        """Close idle open sessions beyond the inactivity threshold without booking slots."""

        if self.session_timeout_minutes <= 0:
            return 0

        ref_now = now or datetime.now(UTC)
        cutoff = ref_now - timedelta(minutes=self.session_timeout_minutes)
        expired_count = 0

        with Session(self.engine) as db:
            open_records = list(
                db.exec(
                    select(IntakeSessionRecord).where(
                        IntakeSessionRecord.status.in_(_OPEN_STATUSES)  # type: ignore[attr-defined]
                    )
                ).all()
            )
            for rec in open_records:
                if _ensure_utc(rec.updated_at) <= cutoff:
                    rec.status = ConversationStatus.EXPIRED.value
                    rec.updated_at = ref_now
                    db.add(rec)
                    db.add(
                        TranscriptMessageRecord(
                            session_id=rec.id,
                            actor="system",
                            message=(
                                "Atendimento encerrado automaticamente após "
                                f"{self.session_timeout_minutes} minutos de inatividade."
                            ),
                            used_llm=False,
                            created_at=ref_now,
                        )
                    )
                    expired_count += 1
            if expired_count:
                db.commit()

        return expired_count

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

    def create_slot(
        self,
        *,
        specialty: Specialty,
        doctor_name: str,
        starts_at: datetime,
    ) -> AppointmentSlotDTO:
        """Create a single calendar slot."""

        utc_start = _to_utc(starts_at)
        record = AppointmentSlotRecord(
            specialty=specialty.value,
            doctor_name=doctor_name.strip(),
            starts_at=utc_start,
            is_booked=False,
        )
        with Session(self.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)
        return _slot_to_dto(record)

    def create_slots_batch(
        self,
        *,
        specialty: Specialty,
        doctor_name: str,
        target_date: date,
        start_hour: int,
        end_hour: int,
        interval_minutes: int,
    ) -> list[AppointmentSlotDTO]:
        """Generate multiple slots within a day and time range."""

        if end_hour <= start_hour:
            raise ValueError("Hora final deve ser maior que a hora inicial.")

        cursor = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            start_hour,
            0,
            tzinfo=_CLINIC_TZ,
        )
        end_dt = datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            end_hour,
            0,
            tzinfo=_CLINIC_TZ,
        )
        delta = timedelta(minutes=interval_minutes)

        created: list[AppointmentSlotRecord] = []
        with Session(self.engine) as db:
            while cursor < end_dt:
                rec = AppointmentSlotRecord(
                    specialty=specialty.value,
                    doctor_name=doctor_name.strip(),
                    starts_at=cursor.astimezone(UTC),
                    is_booked=False,
                )
                db.add(rec)
                created.append(rec)
                cursor += delta
            db.commit()
            for rec in created:
                db.refresh(rec)

        return [_slot_to_dto(rec) for rec in created]

    def delete_slot(self, slot_id: int) -> bool:
        """Delete an unbooked slot; raises ValueError if slot has an active booking."""

        with Session(self.engine) as db:
            slot = db.get(AppointmentSlotRecord, slot_id)
            if slot is None:
                return False
            if slot.is_booked:
                raise ValueError("Não é possível remover um horário já reservado.")
            db.delete(slot)
            db.commit()
            return True

    def _schedule_reminders_for_appointment(
        self,
        db: Session,
        *,
        appointment: AppointmentRecord,
        session_record: IntakeSessionRecord | None,
        now: datetime,
    ) -> None:
        protocol = appointment.id[:8].upper()
        specialty_label = SPECIALTY_LABELS_PTBR[Specialty(appointment.specialty)]
        starts_utc = _ensure_utc(appointment.starts_at)

        for label, hours_before in (("24h", 24), ("2h", 2)):
            scheduled_for = starts_utc - timedelta(hours=hours_before)
            if scheduled_for <= now:
                continue
            preview = _build_reminder_preview(
                clinic_name=session_record.clinic_name if session_record else "Clínica",
                patient_name=appointment.patient_name,
                doctor_name=appointment.doctor_name,
                specialty_label=specialty_label,
                starts_at=starts_utc,
                reminder_type=label,
                protocol=protocol,
            )
            db.add(
                AppointmentReminderRecord(
                    appointment_id=appointment.id,
                    session_id=appointment.session_id,
                    patient_name=appointment.patient_name,
                    recipient_phone=session_record.phone if session_record else None,
                    recipient_email=session_record.email if session_record else None,
                    reminder_type=label,
                    channel="mock",
                    scheduled_for=scheduled_for,
                    status="pending",
                    message_preview=preview,
                )
            )

    def book_slot(
        self,
        session_id: str,
        slot_id: int,
        patient_name: str,
        *,
        now: datetime | None = None,
    ) -> BookedAppointmentDTO | None:
        """Atomically book a free slot and schedule 24h + 2h appointment reminders."""

        ref_now = now or datetime.now(UTC)
        with Session(self.engine) as db:
            slot = db.get(AppointmentSlotRecord, slot_id)
            if slot is None or slot.is_booked:
                return None

            existing_for_session = db.exec(
                select(AppointmentRecord).where(
                    AppointmentRecord.session_id == session_id,
                    AppointmentRecord.status == "confirmed",
                )
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
            session_record = db.get(IntakeSessionRecord, session_id)
            db.add(slot)
            db.add(appointment)
            db.flush()
            self._schedule_reminders_for_appointment(
                db,
                appointment=appointment,
                session_record=session_record,
                now=ref_now,
            )
            db.commit()
            db.refresh(appointment)
            return _appointment_to_dto(appointment)

    def find_appointment_by_protocol(self, protocol: str) -> BookedAppointmentDTO | None:
        """Look up a confirmed appointment by full UUID or 8-char protocol prefix."""

        cleaned = protocol.strip().lstrip("#").lower()
        if not cleaned:
            return None

        with Session(self.engine) as db:
            rows = list(
                db.exec(
                    select(AppointmentRecord).where(AppointmentRecord.status == "confirmed")
                ).all()
            )
        for row in rows:
            if row.id.lower() == cleaned or row.id.lower().startswith(cleaned):
                return _appointment_to_dto(row)
        return None

    def cancel_appointment(
        self,
        appointment_id: str,
        *,
        reason: str | None = None,
        enforce_notice_window: bool = False,
        now: datetime | None = None,
    ) -> BookedAppointmentDTO | None:
        """Cancel a confirmed appointment, free its slot, and cancel pending reminders."""

        ref_now = now or datetime.now(UTC)
        with Session(self.engine) as db:
            appt = db.get(AppointmentRecord, appointment_id)
            if appt is None:
                return None
            if appt.status != "confirmed":
                raise ValueError("Esta consulta já não está confirmada.")

            if enforce_notice_window:
                remaining = _ensure_utc(appt.starts_at) - ref_now
                if remaining < timedelta(hours=self.min_cancel_notice_hours):
                    raise NoticeWindowError(
                        f"Cancelamento automático permitido apenas com pelo menos "
                        f"{self.min_cancel_notice_hours}h de antecedência."
                    )

            slot = db.get(AppointmentSlotRecord, appt.slot_id)
            if slot is not None:
                slot.is_booked = False
                db.add(slot)

            appt.status = "cancelled"
            appt.cancel_reason = reason.strip() if reason else None
            db.add(appt)

            pending_reminders = list(
                db.exec(
                    select(AppointmentReminderRecord).where(
                        AppointmentReminderRecord.appointment_id == appointment_id,
                        AppointmentReminderRecord.status == "pending",
                    )
                ).all()
            )
            for rem in pending_reminders:
                rem.status = "cancelled"
                db.add(rem)

            db.commit()
            db.refresh(appt)
            return _appointment_to_dto(appt)

    def reschedule_appointment(
        self,
        appointment_id: str,
        new_slot_id: int,
        *,
        enforce_notice_window: bool = False,
        now: datetime | None = None,
    ) -> BookedAppointmentDTO | None:
        """Atomically move an appointment to a new free slot and regenerate reminders."""

        ref_now = now or datetime.now(UTC)
        with Session(self.engine) as db:
            appt = db.get(AppointmentRecord, appointment_id)
            if appt is None:
                return None
            if appt.status != "confirmed":
                raise ValueError("Apenas consultas confirmadas podem ser remarcadas.")

            if enforce_notice_window:
                remaining = _ensure_utc(appt.starts_at) - ref_now
                if remaining < timedelta(hours=self.min_cancel_notice_hours):
                    raise NoticeWindowError(
                        f"Remarcação automática permitida apenas com pelo menos "
                        f"{self.min_cancel_notice_hours}h de antecedência."
                    )

            new_slot = db.get(AppointmentSlotRecord, new_slot_id)
            if new_slot is None or new_slot.is_booked:
                raise ValueError("Novo horário indisponível ou já reservado.")

            old_slot = db.get(AppointmentSlotRecord, appt.slot_id)
            if old_slot is not None:
                old_slot.is_booked = False
                db.add(old_slot)

            new_slot.is_booked = True
            db.add(new_slot)

            pending_reminders = list(
                db.exec(
                    select(AppointmentReminderRecord).where(
                        AppointmentReminderRecord.appointment_id == appointment_id,
                        AppointmentReminderRecord.status == "pending",
                    )
                ).all()
            )
            for rem in pending_reminders:
                rem.status = "cancelled"
                db.add(rem)

            appt.slot_id = new_slot_id
            appt.specialty = new_slot.specialty
            appt.doctor_name = new_slot.doctor_name
            appt.starts_at = new_slot.starts_at
            db.add(appt)

            session_record = db.get(IntakeSessionRecord, appt.session_id)
            if session_record is not None:
                session_record.preferred_specialty = new_slot.specialty
                session_record.reschedule_appointment_id = None
                session_record.updated_at = ref_now
                db.add(session_record)

            db.flush()
            self._schedule_reminders_for_appointment(
                db,
                appointment=appt,
                session_record=session_record,
                now=ref_now,
            )
            db.commit()
            db.refresh(appt)
            return _appointment_to_dto(appt)

    def list_reminders(self, status_filter: str | None = None) -> list[ReminderDTO]:
        """List scheduled, sent, or cancelled reminders ordered by target timestamp."""

        with Session(self.engine) as db:
            stmt = select(AppointmentReminderRecord).order_by(
                AppointmentReminderRecord.scheduled_for  # type: ignore[arg-type]
            )
            if status_filter:
                stmt = stmt.where(AppointmentReminderRecord.status == status_filter)
            rows = list(db.exec(stmt).all())
        return [_reminder_to_dto(row) for row in rows]

    def _send_and_update_reminder(
        self,
        db: Session,
        rec: AppointmentReminderRecord,
        sender: ReminderSender,
        channel: str,
        now: datetime,
    ) -> None:
        subject = f"Lembrete de consulta ({rec.reminder_type})"
        ok, err = sender.send(rec.recipient_email, subject, rec.message_preview)
        rec.channel = channel
        if ok:
            rec.status = "sent"
            rec.sent_at = now
            rec.last_error = None
        else:
            rec.status = "failed"
            rec.last_error = err
        db.add(rec)

    def dispatch_reminder_by_id(
        self,
        reminder_id: int,
        sender: ReminderSender,
        channel: str,
        *,
        now: datetime | None = None,
    ) -> ReminderDTO | None:
        """Immediately dispatch a single reminder (e.g. demo button in staff dashboard)."""

        ref_now = now or datetime.now(UTC)
        with Session(self.engine) as db:
            rec = db.get(AppointmentReminderRecord, reminder_id)
            if rec is None:
                return None
            if rec.status == "cancelled":
                raise ValueError("Não é possível enviar um lembrete cancelado.")
            self._send_and_update_reminder(db, rec, sender, channel, ref_now)
            db.commit()
            db.refresh(rec)
            return _reminder_to_dto(rec)

    def dispatch_due_reminders(
        self,
        sender: ReminderSender,
        channel: str,
        *,
        now: datetime | None = None,
    ) -> int:
        """Dispatch all pending reminders whose scheduled time has arrived."""

        ref_now = now or datetime.now(UTC)
        processed = 0
        with Session(self.engine) as db:
            pending = list(
                db.exec(
                    select(AppointmentReminderRecord).where(
                        AppointmentReminderRecord.status == "pending"
                    )
                ).all()
            )
            for rec in pending:
                if _ensure_utc(rec.scheduled_for) <= ref_now:
                    self._send_and_update_reminder(db, rec, sender, channel, ref_now)
                    processed += 1
            if processed:
                db.commit()
        return processed

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

        self.expire_stale_sessions()
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
                select(AppointmentRecord).where(
                    AppointmentRecord.session_id == session_id,
                    AppointmentRecord.status == "confirmed",
                )
            ).first()

        patient_info = PatientInfo(
            full_name=record.full_name,
            date_of_birth=record.date_of_birth,
            phone=record.phone,
            email=record.email,
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
            reschedule_appointment_id=record.reschedule_appointment_id,
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
            record.reschedule_appointment_id = state.reschedule_appointment_id
            record.full_name = patient.full_name
            record.date_of_birth = patient.date_of_birth
            record.phone = patient.phone
            record.email = patient.email
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
        """Delete a session, its transcript/reminders, and release slots (LGPD erasure)."""

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return False

            db.exec(
                delete(AppointmentReminderRecord).where(
                    AppointmentReminderRecord.session_id == session_id
                )
            )
            appointments = list(
                db.exec(
                    select(AppointmentRecord).where(AppointmentRecord.session_id == session_id)
                ).all()
            )
            for appt in appointments:
                if appt.status == "confirmed":
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

        self.expire_stale_sessions()
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
                    select(AppointmentRecord).where(
                        AppointmentRecord.session_id == rec.id,
                        AppointmentRecord.status == "confirmed",
                    )
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

    def list_appointments(self, include_cancelled: bool = False) -> list[BookedAppointmentDTO]:
        """Return confirmed (and optionally cancelled) appointments sorted chronologically."""

        with Session(self.engine) as db:
            stmt = select(AppointmentRecord).order_by(AppointmentRecord.starts_at)  # type: ignore[arg-type]
            if not include_cancelled:
                stmt = stmt.where(AppointmentRecord.status == "confirmed")
            rows = list(db.exec(stmt).all())
        return [_appointment_to_dto(row) for row in rows]

    def staff_takeover(self, session_id: str, staff_name: str) -> LoadedSession | None:
        """Pause AI automation and assign the conversation to a human staff member."""

        self.expire_stale_sessions()
        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return None
            if record.status in (
                ConversationStatus.COMPLETED.value,
                ConversationStatus.EMERGENCY.value,
                ConversationStatus.EXPIRED.value,
            ):
                raise ValueError("Cannot take over a finished, emergency, or expired session.")

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

        self.expire_stale_sessions()
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

    def staff_update_patient(
        self,
        session_id: str,
        updates: dict[str, object],
    ) -> LoadedSession | None:
        """Update structured patient fields manually or after copilot review."""

        self.expire_stale_sessions()
        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return None
            if record.status in (
                ConversationStatus.EXPIRED.value,
                ConversationStatus.EMERGENCY.value,
            ):
                raise ValueError("Cannot update patient details on an expired/emergency session.")

            allowed = {
                "full_name",
                "date_of_birth",
                "phone",
                "email",
                "address",
                "coverage_type",
                "insurance_payer",
                "insurance_id",
                "chief_complaint",
                "preferred_specialty",
            }
            for key, value in updates.items():
                if key not in allowed:
                    continue
                if isinstance(value, (CoverageType, Specialty)):
                    setattr(record, key, value.value)
                elif isinstance(value, str):
                    setattr(record, key, value.strip() or None)
                else:
                    setattr(record, key, value)

            if record.coverage_type == CoverageType.PRIVATE.value:
                record.insurance_payer = None
                record.insurance_id = None

            record.updated_at = datetime.now(UTC)
            db.add(record)
            db.commit()

        return self.load_session(session_id)

    def staff_book_appointment(
        self,
        session_id: str,
        slot_id: int,
        staff_name: str,
    ) -> LoadedSession | None:
        """Book a calendar slot directly from staff dashboard and confirm in chat."""

        self.expire_stale_sessions()
        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return None
            if record.status in (
                ConversationStatus.EXPIRED.value,
                ConversationStatus.EMERGENCY.value,
            ):
                raise ValueError("Cannot book an appointment for an expired or emergency session.")
            patient_name = record.full_name or "Paciente"

        booked = self.book_slot(session_id, slot_id, patient_name)
        if booked is None:
            raise ValueError("Horário indisponível ou já reservado.")

        protocol = booked.appointment_id[:8].upper()
        date_str = booked.starts_at.strftime("%d/%m/%Y às %H:%M")
        confirmation_msg = (
            f"Agendamento confirmado por {staff_name.strip()}!\n\n"
            f"- Protocolo: #{protocol}\n"
            f"- Especialidade: {booked.specialty_label}\n"
            f"- Profissional: {booked.doctor_name}\n"
            f"- Data e hora: {date_str}"
        )

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return None
            now = datetime.now(UTC)
            record.status = ConversationStatus.COMPLETED.value
            record.preferred_specialty = booked.specialty.value
            record.assigned_staff_name = staff_name.strip()
            record.updated_at = now
            db.add(record)
            db.add(
                TranscriptMessageRecord(
                    session_id=session_id,
                    actor="human_agent",
                    message=confirmation_msg,
                    used_llm=False,
                    created_at=now,
                )
            )
            db.commit()

        return self.load_session(session_id)

    def staff_offer_slots_in_chat(
        self,
        session_id: str,
        specialty: Specialty | None = None,
    ) -> LoadedSession | None:
        """Send available calendar slot chips into patient chat for 1-click selection."""

        loaded = self.load_session(session_id)
        if loaded is None:
            return None
        if loaded.state.status in (
            ConversationStatus.COMPLETED,
            ConversationStatus.EMERGENCY,
            ConversationStatus.EXPIRED,
        ):
            raise ValueError("Cannot offer slots on a finished or expired session.")

        target_specialty = (
            specialty
            or loaded.state.patient_info.preferred_specialty
            or suggest_specialty_from_complaint(loaded.state.patient_info.chief_complaint)
        )
        slots = self.list_available_slots(specialty=target_specialty, limit=4)
        if not slots:
            slots = self.list_available_slots(specialty=None, limit=4)
        if not slots:
            raise ValueError("Não há horários disponíveis na agenda.")

        slot_lines = [
            f"{idx}. {slot.display_label} ({slot.specialty_label})"
            for idx, slot in enumerate(slots, start=1)
        ]
        offer_msg = (
            f"Separei estes horários disponíveis em **{SPECIALTY_LABELS_PTBR[target_specialty]}**:"
            "\n\n" + "\n".join(slot_lines) + "\n\nClique em um horário abaixo ou digite o número."
        )

        with Session(self.engine) as db:
            record = db.get(IntakeSessionRecord, session_id)
            if record is None:
                return None
            now = datetime.now(UTC)
            record.status = ConversationStatus.SCHEDULING.value
            record.preferred_specialty = target_specialty.value
            record.handoff_reason = None
            record.assigned_staff_name = None
            record.updated_at = now
            db.add(record)
            db.add(
                TranscriptMessageRecord(
                    session_id=session_id,
                    actor="assistant",
                    message=offer_msg,
                    used_llm=False,
                    created_at=now,
                )
            )
            db.commit()

        return self.load_session(session_id)
