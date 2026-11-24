"""Integration tests for FastAPI endpoints, SQLModel persistence, and static Web Chat."""

from pathlib import Path

from fastapi.testclient import TestClient

from iara.api.app import create_app
from iara.config import Settings
from iara.db.repository import SessionRepository, create_db_engine
from iara.models.extraction import ExtractedPatientFields, TurnAnalysis


class FakeApiLLM:
    """Deterministic single-call LLM stub for FastAPI endpoint tests."""

    def __init__(self, analyses: list[TurnAnalysis]) -> None:
        self._analyses = list(analyses)

    def analyze_turn(self, system_instruction: str, user_payload: str) -> TurnAnalysis:
        return self._analyses.pop(0)


def _build_test_settings(tmp_path: Path) -> Settings:
    db_file = tmp_path / "test_iara.db"
    return Settings(
        llm_provider="vercel_gateway",
        ai_gateway_api_key="test-key",
        database_url=f"sqlite:///{db_file}",
        clinic_name="Clínica API Teste",
    )


def test_health_and_web_chat_index(tmp_path: Path) -> None:
    settings = _build_test_settings(tmp_path)
    llm = FakeApiLLM([])
    app = create_app(settings=settings, llm=llm)

    with TestClient(app) as client:
        health_res = client.get("/api/v1/health")
        assert health_res.status_code == 200
        body = health_res.json()
        assert body["status"] == "ok"
        assert body["provider"] == "vercel_gateway"
        assert body["clinic_name"] == "Clínica API Teste"

        index_res = client.get("/")
        assert index_res.status_code == 200
        assert "Ficha de Pré-Cadastro" in index_res.text


def test_cors_allows_arbitrary_localhost_dev_ports(tmp_path: Path) -> None:
    settings = _build_test_settings(tmp_path)
    app = create_app(settings=settings, llm=FakeApiLLM([]))

    with TestClient(app) as client:
        preflight = client.options(
            "/api/v1/sessions",
            headers={
                "Origin": "http://localhost:3001",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers.get("access-control-allow-origin") == "http://localhost:3001"

        health = client.get("/api/v1/health", headers={"Origin": "http://127.0.0.1:3001"})
        assert health.status_code == 200
        assert health.headers.get("access-control-allow-origin") == "http://127.0.0.1:3001"

        blocked = client.get("/api/v1/health", headers={"Origin": "https://evil.example.com"})
        assert blocked.status_code == 200
        assert "access-control-allow-origin" not in blocked.headers


def test_full_api_lifecycle_persistence_quick_replies_and_lgpd_delete(tmp_path: Path) -> None:
    settings = _build_test_settings(tmp_path)
    llm = FakeApiLLM(
        [
            TurnAnalysis(
                extracted=ExtractedPatientFields(
                    full_name="Juliana Costa",
                    address="Rua das Acácias, 88, Porto Alegre - RS, 90010-000",
                ),
                assistant_reply="Obrigada, Juliana! Qual é a sua data de nascimento?",
            ),
            TurnAnalysis(
                extracted=ExtractedPatientFields(chief_complaint="Dor de ouvido há 2 dias"),
            ),
        ]
    )
    app = create_app(settings=settings, llm=llm)

    with TestClient(app) as client:
        # 1. Create session
        create_res = client.post("/api/v1/sessions")
        assert create_res.status_code == 201
        created = create_res.json()
        session_id = created["session_id"]
        assert created["status"] == "collecting"
        assert len(created["messages"]) == 1
        assert "LGPD" in created["messages"][0]["message"]

        # 2. Multi-field LLM turn
        t1 = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"message": "Sou Juliana Costa, moro na Rua das Acácias 88, Porto Alegre RS"},
        )
        assert t1.status_code == 200
        assert t1.json()["reply"]["used_llm"] is True
        assert t1.json()["missing_fields"] == [
            "date_of_birth",
            "phone",
            "coverage_type",
            "chief_complaint",
        ]

        # 3. Exact date -> 0 tokens fast-path
        t2 = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"message": "22/07/1991"},
        )
        assert t2.status_code == 200
        assert t2.json()["reply"]["used_llm"] is False
        assert t2.json()["missing_fields"] == [
            "phone",
            "coverage_type",
            "chief_complaint",
        ]

        # 3b. Brazilian phone -> 0 tokens fast-path, surfaces quick_replies for coverage_type
        t_phone = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"message": "51998765432"},
        )
        assert t_phone.status_code == 200
        assert t_phone.json()["reply"]["used_llm"] is False
        assert t_phone.json()["reply"]["patient_info"]["phone"] == "(51) 99876-5432"
        assert t_phone.json()["quick_replies"] == [
            {"label": "Convênio", "value": "convênio"},
            {"label": "Particular", "value": "particular"},
        ]

        # 4. Quick reply "particular" -> 0 tokens fast-path
        t3 = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"message": "particular"},
        )
        assert t3.status_code == 200
        assert t3.json()["reply"]["used_llm"] is False
        assert t3.json()["missing_fields"] == ["chief_complaint"]

        # 5. Complaint -> transitions to CONFIRMING with quick replies
        t4 = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"message": "Estou com dor de ouvido há 2 dias"},
        )
        assert t4.status_code == 200
        assert t4.json()["reply"]["status"] == "confirming"
        assert t4.json()["quick_replies"] == [
            {"label": "Sim, confirmar", "value": "sim"},
            {"label": "Corrigir dados", "value": "não"},
        ]

        # 6. Quick reply "sim" -> 0 tokens fast-path enters scheduling with slot quick replies
        t5 = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"message": "sim"},
        )
        assert t5.status_code == 200
        assert t5.json()["reply"]["used_llm"] is False
        assert t5.json()["reply"]["status"] == "scheduling"
        first_slot_value = t5.json()["quick_replies"][0]["value"]
        assert first_slot_value.startswith("slot:")

        # 7. Select slot via quick reply -> 0 tokens fast-path completes booking
        t6 = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"message": first_slot_value},
        )
        assert t6.status_code == 200
        assert t6.json()["reply"]["used_llm"] is False
        assert t6.json()["reply"]["status"] == "completed"
        assert t6.json()["reply"]["is_finished"] is True
        assert t6.json()["reply"]["booked_appointment"] is not None

        # 8. Restore session using a fresh repository/app instance (restart simulation)
        fresh_repo = SessionRepository(create_db_engine(settings.database_url))
        fresh_app = create_app(settings=settings, llm=FakeApiLLM([]), repository=fresh_repo)
        with TestClient(fresh_app) as fresh_client:
            get_res = fresh_client.get(f"/api/v1/sessions/{session_id}")
            assert get_res.status_code == 200
            restored = get_res.json()
            assert restored["status"] == "completed"
            assert restored["patient_info"]["full_name"] == "Juliana Costa"
            assert restored["patient_info"]["phone"] == "(51) 99876-5432"
            assert restored["patient_info"]["coverage_type"] == "particular"
            assert restored["booked_appointment"] is not None
            assert len(restored["messages"]) == 15

            # 9. LGPD delete removes session and returns 404 afterward
            del_res = fresh_client.delete(f"/api/v1/sessions/{session_id}")
            assert del_res.status_code == 204
            assert fresh_client.get(f"/api/v1/sessions/{session_id}").status_code == 404


def test_unknown_session_returns_404(tmp_path: Path) -> None:
    settings = _build_test_settings(tmp_path)
    app = create_app(settings=settings, llm=FakeApiLLM([]))

    with TestClient(app) as client:
        assert client.get("/api/v1/sessions/nonexistent").status_code == 404
        assert (
            client.post(
                "/api/v1/sessions/nonexistent/messages",
                json={"message": "Olá"},
            ).status_code
            == 404
        )
        assert client.delete("/api/v1/sessions/nonexistent").status_code == 404
