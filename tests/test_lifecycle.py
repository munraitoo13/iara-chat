"""Tests for Fase 6 appointment lifecycle: slot CRUD, cancel/reschedule, and 24h/2h reminders."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from google.genai._transformers import t_schema
from openai.lib._parsing._completions import type_to_response_format_param
from sqlmodel import Session

from iara.api.app import create_app
from iara.config import Settings
from iara.db.models import AppointmentReminderRecord
from iara.db.repository import SessionRepository, create_db_engine
from iara.models.extraction import TurnAnalysis


class _EmptyLLM:
    """Stub proving lifecycle and protocol fast-paths consume zero LLM calls."""

    def __init__(self) -> None:
        self.calls = 0

    def analyze_turn(self, system_instruction: str, user_payload: str) -> TurnAnalysis:
        self.calls += 1
        raise AssertionError("LLM should not be called in deterministic lifecycle paths")


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        llm_provider="vercel_gateway",
        ai_gateway_api_key="test-key",
        database_url=f"sqlite:///{tmp_path / 'lifecycle.db'}",
        clinic_name="Clínica Vida",
        min_cancel_notice_hours=2,
    )


def test_turn_analysis_strict_schemas_compatible_with_openai_and_gemini() -> None:
    openai_param = type_to_response_format_param(TurnAnalysis)
    assert openai_param["type"] == "json_schema"
    assert openai_param["json_schema"]["strict"] is True

    gemini_schema = t_schema(None, TurnAnalysis)
    assert gemini_schema is not None


def test_staff_slot_crud_batch_and_booked_delete_conflict(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings=settings, llm=_EmptyLLM())

    with TestClient(app) as client:
        target_day = (datetime.now(UTC) + timedelta(days=5)).date()
        single_time = datetime.combine(target_day, datetime.min.time(), tzinfo=UTC).replace(hour=14)

        created_single = client.post(
            "/api/v1/staff/slots",
            json={
                "specialty": "ortopedia",
                "doctor_name": "Dr. Marcos Souza",
                "starts_at": single_time.isoformat(),
            },
        )
        assert created_single.status_code == 201
        single_id = created_single.json()["slot_id"]

        created_batch = client.post(
            "/api/v1/staff/slots/batch",
            json={
                "specialty": "cardiologia",
                "doctor_name": "Dra. Helena Dias",
                "target_date": target_day.isoformat(),
                "start_hour": 8,
                "end_hour": 10,
                "interval_minutes": 30,
            },
        )
        assert created_batch.status_code == 201
        batch_rows = created_batch.json()
        assert len(batch_rows) == 4

        bad_batch = client.post(
            "/api/v1/staff/slots/batch",
            json={
                "specialty": "cardiologia",
                "doctor_name": "Dra. Helena Dias",
                "target_date": target_day.isoformat(),
                "start_hour": 12,
                "end_hour": 10,
                "interval_minutes": 30,
            },
        )
        assert bad_batch.status_code == 400

        # Delete an unbooked slot succeeds cleanly
        assert client.delete(f"/api/v1/staff/slots/{single_id}").status_code == 204
        ortho_slots = client.get("/api/v1/slots", params={"specialty": "ortopedia"}).json()
        assert all(row["slot_id"] != single_id for row in ortho_slots)

        # Book one batch slot and verify deleting it returns 409 Conflict
        booked_slot_id = batch_rows[0]["slot_id"]
        sid = client.post("/api/v1/sessions").json()["session_id"]
        client.patch(
            f"/api/v1/staff/sessions/{sid}/patient",
            json={"full_name": "Pedro Henrique", "phone": "(11) 99999-1234"},
        )
        book_res = client.post(
            f"/api/v1/staff/sessions/{sid}/book",
            json={"slot_id": booked_slot_id, "staff_name": "Recepção"},
        )
        assert book_res.status_code == 200

        delete_booked = client.delete(f"/api/v1/staff/slots/{booked_slot_id}")
        assert delete_booked.status_code == 409


def test_staff_cancel_reschedule_and_reminders_lifecycle(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    repo = SessionRepository(create_db_engine(settings.database_url))
    app = create_app(settings=settings, llm=_EmptyLLM(), repository=repo)

    with TestClient(app) as client:
        future_day = date.today() + timedelta(days=4)
        batch_slots = client.post(
            "/api/v1/staff/slots/batch",
            json={
                "specialty": "dermatologia",
                "doctor_name": "Dra. Beatriz Nogueira",
                "target_date": future_day.isoformat(),
                "start_hour": 10,
                "end_hour": 11,
                "interval_minutes": 30,
            },
        ).json()
        slot_a, slot_b = batch_slots[0], batch_slots[1]

        sid = client.post("/api/v1/sessions").json()["session_id"]
        client.patch(
            f"/api/v1/staff/sessions/{sid}/patient",
            json={
                "full_name": "Camila Ferreira",
                "phone": "(21) 98765-4321",
                "email": "camila@exemplo.com",
            },
        )
        booked = client.post(
            f"/api/v1/staff/sessions/{sid}/book",
            json={"slot_id": slot_a["slot_id"], "staff_name": "Recepção"},
        ).json()["booked_appointment"]
        appt_id = booked["appointment_id"]
        assert booked["patient_name"] == "Camila Ferreira"

        # 24h and 2h reminders were automatically created
        reminders = client.get("/api/v1/staff/reminders").json()
        appt_reminders = [r for r in reminders if r["appointment_id"] == appt_id]
        assert {r["reminder_type"] for r in appt_reminders} == {"24h", "2h"}
        assert all(r["status"] == "pending" for r in appt_reminders)
        assert all(r["recipient_email"] == "camila@exemplo.com" for r in appt_reminders)

        # Manual "Enviar agora" dispatches the first reminder
        first_rem_id = appt_reminders[0]["reminder_id"]
        sent_rem = client.post(f"/api/v1/staff/reminders/{first_rem_id}/send-now")
        assert sent_rem.status_code == 200
        assert sent_rem.json()["status"] == "sent"
        assert sent_rem.json()["sent_at"] is not None
        assert sent_rem.json()["channel"] == "mock"

        # Make the remaining pending reminder due and test batch dispatcher endpoint
        second_rem_id = appt_reminders[1]["reminder_id"]
        with Session(repo.engine) as db:
            rec = db.get(AppointmentReminderRecord, second_rem_id)
            assert rec is not None
            rec.scheduled_for = datetime.now(UTC) - timedelta(minutes=5)
            db.add(rec)
            db.commit()

        dispatched = client.post("/api/v1/staff/reminders/dispatch-due")
        assert dispatched.status_code == 200
        assert dispatched.json() == {"dispatched": 1, "channel": "mock"}

        # Staff reschedule moves appointment to slot_b, frees slot_a, and creates new reminders
        rescheduled = client.post(
            f"/api/v1/staff/appointments/{appt_id}/reschedule",
            json={"new_slot_id": slot_b["slot_id"]},
        )
        assert rescheduled.status_code == 200
        assert rescheduled.json()["slot_id"] == slot_b["slot_id"]

        free_after_reschedule = client.get(
            "/api/v1/slots", params={"specialty": "dermatologia"}
        ).json()
        free_ids = {row["slot_id"] for row in free_after_reschedule}
        assert slot_a["slot_id"] in free_ids
        assert slot_b["slot_id"] not in free_ids

        pending_after_reschedule = client.get(
            "/api/v1/staff/reminders", params={"status": "pending"}
        ).json()
        assert len([r for r in pending_after_reschedule if r["appointment_id"] == appt_id]) == 2

        # Staff cancel frees slot_b and marks pending reminders as cancelled
        cancelled = client.post(
            f"/api/v1/staff/appointments/{appt_id}/cancel",
            json={"reason": "Solicitação do paciente"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        assert client.get("/api/v1/staff/appointments").json() == []
        free_after_cancel = client.get("/api/v1/slots", params={"specialty": "dermatologia"}).json()
        assert slot_b["slot_id"] in {row["slot_id"] for row in free_after_cancel}

        pending_after_cancel = client.get(
            "/api/v1/staff/reminders", params={"status": "pending"}
        ).json()
        assert all(r["appointment_id"] != appt_id for r in pending_after_cancel)


def test_patient_self_service_reschedule_cancel_and_notice_window_escalation(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    llm = _EmptyLLM()
    app = create_app(settings=settings, llm=llm)

    with TestClient(app) as client:
        # 1. Book a far-future appointment (> 24h)
        s_book = client.post("/api/v1/sessions").json()["session_id"]
        client.patch(
            f"/api/v1/staff/sessions/{s_book}/patient",
            json={"full_name": "Lucas Andrade", "phone": "(11) 91111-2222"},
        )
        clinic_slots = client.get("/api/v1/slots", params={"specialty": "clinica_geral"}).json()
        far_slots = [
            s
            for s in clinic_slots
            if datetime.fromisoformat(s["starts_at"]) > datetime.now(UTC) + timedelta(hours=12)
        ]
        assert len(far_slots) >= 2
        orig_slot, alt_slot = far_slots[0], far_slots[1]

        appt = client.post(
            f"/api/v1/staff/sessions/{s_book}/book",
            json={"slot_id": orig_slot["slot_id"], "staff_name": "Recepção"},
        ).json()["booked_appointment"]
        protocol = appt["appointment_id"][:8].upper()

        # 2. Self-service reschedule in a fresh conversation (0 tokens)
        s_resched = client.post("/api/v1/sessions").json()["session_id"]
        r1 = client.post(
            f"/api/v1/sessions/{s_resched}/messages",
            json={"message": f"Quero remarcar #{protocol}"},
        )
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["reply"]["used_llm"] is False
        assert body1["reply"]["status"] == "scheduling"
        assert any(q["value"] == f"slot:{alt_slot['slot_id']}" for q in body1["quick_replies"])

        r2 = client.post(
            f"/api/v1/sessions/{s_resched}/messages",
            json={"message": f"slot:{alt_slot['slot_id']}"},
        )
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["reply"]["used_llm"] is False
        assert body2["reply"]["status"] == "completed"
        assert body2["reply"]["booked_appointment"]["slot_id"] == alt_slot["slot_id"]

        # 3. Self-service cancel in another conversation (0 tokens)
        s_cancel = client.post("/api/v1/sessions").json()["session_id"]
        r3 = client.post(
            f"/api/v1/sessions/{s_cancel}/messages",
            json={"message": f"cancelar #{protocol}"},
        )
        assert r3.status_code == 200
        body3 = r3.json()
        assert body3["reply"]["used_llm"] is False
        assert body3["reply"]["status"] == "completed"
        assert body3["reply"]["is_finished"] is True
        assert "cancelada" in body3["reply"]["message"].lower()

        # 4. Near-term appointment (< 2h notice window) escalates to human reception
        near_slot = client.post(
            "/api/v1/staff/slots",
            json={
                "specialty": "clinica_geral",
                "doctor_name": "Dr. Plantão Urgente",
                "starts_at": (datetime.now(UTC) + timedelta(minutes=45)).isoformat(),
            },
        ).json()
        s_near = client.post("/api/v1/sessions").json()["session_id"]
        client.patch(
            f"/api/v1/staff/sessions/{s_near}/patient",
            json={"full_name": "Mariana Luz", "phone": "(11) 92222-3333"},
        )
        near_appt = client.post(
            f"/api/v1/staff/sessions/{s_near}/book",
            json={"slot_id": near_slot["slot_id"], "staff_name": "Recepção"},
        ).json()["booked_appointment"]
        near_protocol = near_appt["appointment_id"][:8].upper()

        s_late = client.post("/api/v1/sessions").json()["session_id"]
        r4 = client.post(
            f"/api/v1/sessions/{s_late}/messages",
            json={"message": f"cancelar #{near_protocol}"},
        )
        assert r4.status_code == 200
        body4 = r4.json()
        assert body4["reply"]["used_llm"] is False
        assert body4["reply"]["status"] == "waiting_human"
        assert len(client.get("/api/v1/staff/appointments").json()) == 1
        assert llm.calls == 0
