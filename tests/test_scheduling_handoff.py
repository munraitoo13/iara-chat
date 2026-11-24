"""Tests for specialty triage, SQLModel scheduling, and human-in-the-loop handoff."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from iara.api.app import create_app
from iara.config import Settings
from iara.conversation.engine import IntakeAgent
from iara.conversation.guardrails import (
    detect_human_handoff_request,
    match_slot_selection,
    match_specialty_choice,
)
from iara.conversation.state import ConversationStatus
from iara.db.models import IntakeSessionRecord
from iara.db.repository import SessionRepository, create_db_engine
from iara.models.extraction import ExtractedPatientFields, TurnAnalysis
from iara.models.scheduling import Specialty, suggest_specialty_from_complaint
from iara.services.llm import LLMError


class StubLLM:
    """Deterministic stub for scheduling and handoff tests."""

    def __init__(self, items: list[TurnAnalysis | Exception]) -> None:
        self._items = list(items)
        self.calls = 0

    def analyze_turn(self, system_instruction: str, user_payload: str) -> TurnAnalysis:
        self.calls += 1
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _make_settings(tmp_path: Path, *, staff_api_key: str | None = None) -> Settings:
    return Settings(
        llm_provider="vercel_gateway",
        ai_gateway_api_key="test-key",
        database_url=f"sqlite:///{tmp_path / 'sched.db'}",
        clinic_name="Clínica Modelo",
        staff_api_key=staff_api_key,
    )


def test_specialty_heuristic_and_fast_paths(tmp_path: Path) -> None:
    assert suggest_specialty_from_complaint("Estou com dor lombar intensa") == Specialty.ORTHOPEDICS
    assert suggest_specialty_from_complaint("Manchas na pele") == Specialty.DERMATOLOGY
    assert suggest_specialty_from_complaint("Pressão alta e palpitação") == Specialty.CARDIOLOGY
    assert suggest_specialty_from_complaint("Check-up de rotina") == Specialty.GENERAL_PRACTICE

    assert detect_human_handoff_request("Quero falar com um atendente humano") is True
    assert detect_human_handoff_request("Tenho dor nas costas") is False
    assert match_specialty_choice("Cardiologista") == Specialty.CARDIOLOGY

    repo = SessionRepository(create_db_engine(f"sqlite:///{tmp_path / 'slots.db'}"))
    repo.init_db()
    slots = repo.list_available_slots(specialty=Specialty.ORTHOPEDICS, limit=4)
    assert len(slots) >= 2
    assert match_slot_selection(f"slot:{slots[1].slot_id}", slots) == slots[1]
    assert match_slot_selection("1", slots) == slots[0]


def test_two_consecutive_llm_errors_auto_escalate_to_waiting_human() -> None:
    llm = StubLLM([LLMError("timeout 1"), LLMError("timeout 2")])
    agent = IntakeAgent(llm=llm)

    r1 = agent.handle_message("Meu nome é João")
    assert r1.status == ConversationStatus.COLLECTING
    assert r1.error == "timeout 1"

    r2 = agent.handle_message("João Pedro")
    assert r2.status == ConversationStatus.WAITING_HUMAN
    assert r2.is_finished is False
    assert agent.state.handoff_reason == "consecutive_llm_errors"

    # Follow-up while waiting does not burn LLM calls
    r3 = agent.handle_message("Tem alguém aí?")
    assert r3.status == ConversationStatus.WAITING_HUMAN
    assert r3.used_llm is False
    assert llm.calls == 2


def test_end_to_end_scheduling_specialty_switch_booking_and_lgpd_release(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    llm = StubLLM(
        [
            TurnAnalysis(
                extracted=ExtractedPatientFields(
                    full_name="Fernanda Rocha",
                    date_of_birth_iso="1988-09-12",
                    address="Rua das Laranjeiras, 300, Rio de Janeiro - RJ",
                    coverage_type="particular",
                    chief_complaint="Dor na coluna lombar",
                )
            )
        ]
    )
    app = create_app(settings=settings, llm=llm)

    with TestClient(app) as client:
        s1 = client.post("/api/v1/sessions").json()["session_id"]
        s2 = client.post("/api/v1/sessions").json()["session_id"]

        # Intake completes -> confirmation
        t1 = client.post(
            f"/api/v1/sessions/{s1}/messages",
            json={"message": "Fernanda Rocha, 12/09/1988, Rua Laranjeiras 300 RJ, particular"},
        )
        assert t1.json()["reply"]["status"] == "confirming"

        # Confirm with 0 tokens -> enters SCHEDULING with suggested Ortopedia slots
        t2 = client.post(f"/api/v1/sessions/{s1}/messages", json={"message": "sim"})
        body2 = t2.json()
        assert body2["reply"]["used_llm"] is False
        assert body2["reply"]["status"] == "scheduling"
        assert body2["reply"]["patient_info"]["preferred_specialty"] == "ortopedia"
        assert len(body2["available_slots"]) >= 2
        assert body2["quick_replies"][0]["value"].startswith("slot:")

        # Switch specialty using 0-token fast-path
        t3 = client.post(f"/api/v1/sessions/{s1}/messages", json={"message": "Clínica Geral"})
        body3 = t3.json()
        assert body3["reply"]["used_llm"] is False
        assert body3["reply"]["status"] == "scheduling"
        assert body3["reply"]["patient_info"]["preferred_specialty"] == "clinica_geral"
        chosen_slot = body3["available_slots"][0]

        # Book slot via quick-reply value (0 tokens)
        t4 = client.post(
            f"/api/v1/sessions/{s1}/messages",
            json={"message": f"slot:{chosen_slot['slot_id']}"},
        )
        body4 = t4.json()
        assert body4["reply"]["used_llm"] is False
        assert body4["reply"]["status"] == "completed"
        assert body4["reply"]["is_finished"] is True
        assert body4["reply"]["booked_appointment"]["slot_id"] == chosen_slot["slot_id"]

        # Chosen slot is no longer returned in public available slots
        public_slots = client.get("/api/v1/slots", params={"specialty": "clinica_geral"}).json()
        assert all(slot["slot_id"] != chosen_slot["slot_id"] for slot in public_slots)

        # Direct double-booking attempt on repository returns None
        repo = SessionRepository(create_db_engine(settings.database_url))
        assert repo.book_slot(s2, chosen_slot["slot_id"], "Outro Paciente") is None

        # LGPD delete releases the booked slot back to available
        assert client.delete(f"/api/v1/sessions/{s1}").status_code == 204
        public_slots_after = client.get(
            "/api/v1/slots", params={"specialty": "clinica_geral"}
        ).json()
        assert any(slot["slot_id"] == chosen_slot["slot_id"] for slot in public_slots_after)


def test_human_handoff_takeover_polling_release_and_staff_auth(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, staff_api_key="secret-staff-token")
    llm = StubLLM(
        [
            TurnAnalysis(
                extracted=ExtractedPatientFields(full_name="Marcos Vinicius"),
                assistant_reply="Prazer, Marcos! Qual é a sua data de nascimento?",
            )
        ]
    )
    app = create_app(settings=settings, llm=llm)
    headers = {"X-Staff-Key": "secret-staff-token"}

    with TestClient(app) as client:
        # Staff endpoint rejects missing/wrong key when STAFF_API_KEY is configured
        assert client.get("/api/v1/staff/sessions").status_code == 401
        assert client.get("/api/v1/staff/sessions", headers=headers).status_code == 200

        session_id = client.post("/api/v1/sessions").json()["session_id"]

        # Patient requests human attendant (0 tokens)
        r_handoff = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"message": "Quero falar com um atendente humano, por favor"},
        )
        assert r_handoff.status_code == 200
        assert r_handoff.json()["reply"]["used_llm"] is False
        assert r_handoff.json()["reply"]["status"] == "waiting_human"

        # Queue lists session waiting for human
        queue = client.get(
            "/api/v1/staff/sessions",
            params={"status": "waiting_human"},
            headers=headers,
        ).json()
        assert len(queue) == 1
        assert queue[0]["handoff_reason"] == "patient_requested"

        # Staff takes over session
        takeover = client.post(
            f"/api/v1/staff/sessions/{session_id}/takeover",
            json={"staff_name": "Recepcionista Beatriz"},
            headers=headers,
        )
        assert takeover.status_code == 200
        assert takeover.json()["status"] == "human_takeover"
        last_msg_id = takeover.json()["messages"][-1]["id"]

        # While under human takeover, patient message does NOT invoke Iara AI
        silent_turn = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"message": "Preciso remarcar uma guia especial"},
        )
        assert silent_turn.status_code == 200
        assert silent_turn.json()["reply"]["status"] == "human_takeover"
        assert silent_turn.json()["reply"]["message"] == ""
        assert silent_turn.json()["reply"]["used_llm"] is False

        # Staff sends human reply
        staff_msg = client.post(
            f"/api/v1/staff/sessions/{session_id}/messages",
            json={"staff_name": "Recepcionista Beatriz", "message": "Olá! Já estou verificando."},
            headers=headers,
        )
        assert staff_msg.status_code == 200

        # Patient polls incrementally with ?after_message_id=
        polled = client.get(
            f"/api/v1/sessions/{session_id}",
            params={"after_message_id": last_msg_id},
        ).json()
        actors = [m["actor"] for m in polled["messages"]]
        assert actors == ["user", "human_agent"]
        assert polled["messages"][1]["message"] == "Olá! Já estou verificando."

        # Release back to Iara AI -> collects next field via LLM normally
        released = client.post(
            f"/api/v1/staff/sessions/{session_id}/release",
            headers=headers,
        )
        assert released.status_code == 200
        assert released.json()["status"] == "collecting"

        ai_back = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"message": "Meu nome é Marcos Vinicius"},
        )
        assert ai_back.status_code == 200
        assert ai_back.json()["reply"]["used_llm"] is True
        assert "Prazer, Marcos!" in ai_back.json()["reply"]["message"]

        # Staff can also close a session explicitly
        closed = client.post(
            f"/api/v1/staff/sessions/{session_id}/close",
            json={"closing_note": "Resolvido por telefone."},
            headers=headers,
        )
        assert closed.status_code == 200
        assert closed.json()["status"] == "completed"
        assert closed.json()["is_finished"] is True


def test_session_inactivity_timeout_expires_open_and_takeover_sessions(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'timeout.db'}"
    repo = SessionRepository(create_db_engine(db_url), session_timeout_minutes=20)
    repo.init_db()
    settings = Settings(
        llm_provider="vercel_gateway",
        ai_gateway_api_key="test-key",
        database_url=db_url,
        clinic_name="Clínica Modelo",
        session_timeout_minutes=20,
    )
    app = create_app(settings=settings, llm=StubLLM([]), repository=repo)

    s_stale_collecting = repo.create_session("Clínica Modelo", "Olá").session_id
    s_stale_takeover = repo.create_session("Clínica Modelo", "Olá").session_id
    repo.staff_takeover(s_stale_takeover, "Recepção")
    s_active = repo.create_session("Clínica Modelo", "Olá").session_id
    s_completed = repo.create_session("Clínica Modelo", "Olá").session_id
    repo.staff_close_session(s_completed, "Finalizado")

    old_time = datetime.now(UTC) - timedelta(minutes=25)
    with Session(repo.engine) as db:
        for sid in (s_stale_collecting, s_stale_takeover, s_completed):
            rec = db.get(IntakeSessionRecord, sid)
            assert rec is not None
            rec.updated_at = old_time
            db.add(rec)
        db.commit()

    with TestClient(app) as client:
        # Stale collecting & human_takeover sessions auto-expire without booking
        res_stale = client.get(f"/api/v1/sessions/{s_stale_collecting}")
        assert res_stale.status_code == 200
        assert res_stale.json()["status"] == "expired"
        assert res_stale.json()["is_finished"] is True
        assert res_stale.json()["booked_appointment"] is None

        res_takeover = client.get(f"/api/v1/sessions/{s_stale_takeover}")
        assert res_takeover.status_code == 200
        assert res_takeover.json()["status"] == "expired"
        assert res_takeover.json()["is_finished"] is True

        # Active and already-completed sessions are unaffected
        assert client.get(f"/api/v1/sessions/{s_active}").json()["status"] == "collecting"
        assert client.get(f"/api/v1/sessions/{s_completed}").json()["status"] == "completed"

        # Sending a message or trying staff takeover on expired session is handled cleanly
        msg_expired = client.post(
            f"/api/v1/sessions/{s_stale_collecting}/messages",
            json={"message": "Voltei"},
        )
        assert msg_expired.status_code == 200
        assert msg_expired.json()["reply"]["status"] == "expired"
        assert msg_expired.json()["reply"]["used_llm"] is False
        assert "inatividade" in msg_expired.json()["reply"]["message"].lower()

        takeover_expired = client.post(
            f"/api/v1/staff/sessions/{s_stale_collecting}/takeover",
            json={"staff_name": "Recepção"},
        )
        assert takeover_expired.status_code == 409
        assert repo.list_appointments() == []


def test_staff_copilot_patient_edit_offer_slots_and_direct_booking(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    llm = StubLLM(
        [
            TurnAnalysis(
                extracted=ExtractedPatientFields(
                    full_name="Patrícia Gomes",
                    date_of_birth_iso="1993-04-18",
                    address="Rua Verde, 15, Curitiba - PR",
                    coverage_type="particular",
                    chief_complaint="Manchas vermelhas na pele",
                )
            )
        ]
    )
    app = create_app(settings=settings, llm=llm)

    with TestClient(app) as client:
        s1 = client.post("/api/v1/sessions").json()["session_id"]
        s2 = client.post("/api/v1/sessions").json()["session_id"]

        # Patient requests human immediately and talks with human agent
        client.post(f"/api/v1/sessions/{s1}/messages", json={"message": "Falar com atendente"})
        client.post(
            f"/api/v1/staff/sessions/{s1}/takeover",
            json={"staff_name": "Recepção Ana"},
        )
        client.post(
            f"/api/v1/sessions/{s1}/messages",
            json={
                "message": (
                    "Sou Patrícia Gomes, 18/04/1993, Rua Verde 15 Curitiba, "
                    "particular, manchas na pele"
                )
            },
        )
        transcript_before = len(client.get(f"/api/v1/sessions/{s1}").json()["messages"])

        # 1. Silent AI Copilot extracts data without messaging patient or mutating transcript
        extracted = client.post(f"/api/v1/staff/sessions/{s1}/extract")
        assert extracted.status_code == 200
        ext_body = extracted.json()
        assert ext_body["extracted_patient"]["full_name"] == "Patrícia Gomes"
        assert ext_body["suggested_specialty"] == "dermatologia"
        assert len(client.get(f"/api/v1/sessions/{s1}").json()["messages"]) == transcript_before

        # 2. Staff saves patient form (switching convenio -> particular clears insurance fields)
        patched = client.patch(
            f"/api/v1/staff/sessions/{s1}/patient",
            json={
                "full_name": "Patrícia Gomes",
                "date_of_birth": "1993-04-18",
                "address": "Rua Verde, 15, Curitiba - PR",
                "coverage_type": "convenio",
                "insurance_payer": "Unimed",
                "insurance_id": "UN12345",
                "chief_complaint": "Manchas vermelhas na pele",
                "preferred_specialty": "dermatologia",
            },
        )
        assert patched.status_code == 200
        assert patched.json()["patient_info"]["insurance_payer"] == "Unimed"

        patched_private = client.patch(
            f"/api/v1/staff/sessions/{s1}/patient",
            json={"coverage_type": "particular"},
        )
        assert patched_private.status_code == 200
        assert patched_private.json()["patient_info"]["insurance_payer"] is None
        assert patched_private.json()["patient_info"]["insurance_id"] is None

        # 3. Staff offers slots into chat -> patient clicks chip (0 tokens)
        offered = client.post(
            f"/api/v1/staff/sessions/{s1}/offer-slots",
            json={"specialty": "dermatologia"},
        )
        assert offered.status_code == 200
        assert offered.json()["status"] == "scheduling"
        chip_value = offered.json()["quick_replies"][0]["value"]
        assert chip_value.startswith("slot:")

        booked_by_patient = client.post(
            f"/api/v1/sessions/{s1}/messages",
            json={"message": chip_value},
        )
        assert booked_by_patient.status_code == 200
        assert booked_by_patient.json()["reply"]["status"] == "completed"
        assert booked_by_patient.json()["reply"]["used_llm"] is False

        # 4. Direct staff booking on session 2 + 409 conflict on double booking
        client.post(f"/api/v1/sessions/{s2}/messages", json={"message": "Quero atendente"})
        client.post(
            f"/api/v1/staff/sessions/{s2}/takeover",
            json={"staff_name": "Recepção Ana"},
        )
        client.patch(
            f"/api/v1/staff/sessions/{s2}/patient",
            json={"full_name": "Ricardo Alves"},
        )
        free_slot = client.get("/api/v1/slots", params={"specialty": "cardiologia"}).json()[0]

        direct = client.post(
            f"/api/v1/staff/sessions/{s2}/book",
            json={"slot_id": free_slot["slot_id"], "staff_name": "Recepção Ana"},
        )
        assert direct.status_code == 200
        direct_body = direct.json()
        assert direct_body["status"] == "completed"
        assert direct_body["is_finished"] is True
        assert direct_body["booked_appointment"]["slot_id"] == free_slot["slot_id"]
        assert direct_body["messages"][-1]["actor"] == "human_agent"

        # Attempting to book same slot again returns 409 Conflict
        s3 = client.post("/api/v1/sessions").json()["session_id"]
        conflict = client.post(
            f"/api/v1/staff/sessions/{s3}/book",
            json={"slot_id": free_slot["slot_id"], "staff_name": "Recepção Ana"},
        )
        assert conflict.status_code == 409
