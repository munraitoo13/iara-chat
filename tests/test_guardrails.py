"""Unit tests for deterministic clinical guardrails, fast-paths, and field normalization."""

from datetime import date

from iara.conversation.guardrails import (
    detect_protocol_action,
    detect_red_flag_keywords,
    match_collecting_fast_path,
    match_confirmation_fast_path,
    normalize_brazilian_phone,
    normalize_email,
    validate_and_normalize_fields,
    validate_input_length,
)
from iara.models.extraction import ExtractedPatientFields
from iara.models.patient import CoverageType


def test_validate_input_length_rejects_empty_and_oversized() -> None:
    assert validate_input_length("   ", max_chars=100) is not None
    assert validate_input_length("a" * 101, max_chars=100) is not None
    assert validate_input_length("João da Silva", max_chars=100) is None


def test_detect_red_flag_keywords_accent_insensitive() -> None:
    assert detect_red_flag_keywords("Estou com dor forte no peito agora")
    assert detect_red_flag_keywords("Nao consigo respirar direito")
    assert detect_red_flag_keywords("Tive um desmaio em casa e boca torta")
    assert detect_red_flag_keywords("Quero tirar a minha vida")
    assert not detect_red_flag_keywords("Estou com dor de garganta leve há dois dias")


def test_confirmation_fast_path_matches_unambiguous_choices_only() -> None:
    assert match_confirmation_fast_path("Sim!") == "confirm"
    assert match_confirmation_fast_path("  tudo certo. ") == "confirm"
    assert match_confirmation_fast_path("Não") == "edit"
    assert match_confirmation_fast_path("corrigir") == "edit"
    assert match_confirmation_fast_path("não, muda meu endereço para Rua X") is None


def test_collecting_fast_path_for_coverage_and_date() -> None:
    res_private = match_collecting_fast_path("Particular.", "coverage_type")
    assert res_private == ({"coverage_type": CoverageType.PRIVATE}, [])

    res_ins = match_collecting_fast_path("Convênio", "coverage_type")
    assert res_ins == ({"coverage_type": CoverageType.INSURANCE}, [])

    assert match_collecting_fast_path("Tenho Unimed, número 12345", "coverage_type") is None

    res_dob = match_collecting_fast_path("14/05/1992", "date_of_birth", today=date(2026, 11, 24))
    assert res_dob == ({"date_of_birth": date(1992, 5, 14)}, [])

    res_bad_dob = match_collecting_fast_path(
        "31/02/1992", "date_of_birth", today=date(2026, 11, 24)
    )
    assert res_bad_dob is not None
    assert res_bad_dob[0] == {}
    assert len(res_bad_dob[1]) == 1


def test_validate_and_normalize_fields_accepts_valid_brazilian_data() -> None:
    extracted = ExtractedPatientFields(
        full_name="  Maria   Clara  Souza ",
        date_of_birth_iso="1992-05-14",
        address="Rua das Flores, 120, São Paulo - SP, CEP 01001-000",
        coverage_type="convenio",
        insurance_payer="Unimed",
        insurance_id="99887766",
        chief_complaint="Dor de cabeça persistente",
    )

    updates, errors = validate_and_normalize_fields(extracted, today=date(2026, 11, 24))

    assert errors == []
    assert updates["full_name"] == "Maria Clara Souza"
    assert updates["date_of_birth"] == date(1992, 5, 14)
    assert updates["coverage_type"] == CoverageType.INSURANCE
    assert updates["insurance_payer"] == "Unimed"
    assert updates["insurance_id"] == "99887766"


def test_validate_and_normalize_fields_accepts_br_date_fallback() -> None:
    extracted = ExtractedPatientFields(date_of_birth_iso="14/05/1992")
    updates, errors = validate_and_normalize_fields(extracted, today=date(2026, 11, 24))

    assert errors == []
    assert updates["date_of_birth"] == date(1992, 5, 14)


def test_validate_and_normalize_fields_rejects_invalid_values() -> None:
    extracted = ExtractedPatientFields(
        full_name="12345",
        date_of_birth_iso="2099-01-01",
        address="Rua 1",
        insurance_payer="nenhum",
        insurance_id="12",
        chief_complaint="..",
    )

    updates, errors = validate_and_normalize_fields(extracted, today=date(2026, 11, 24))

    assert updates == {}
    assert len(errors) == 6


def test_phone_email_normalization_and_protocol_action_fast_paths() -> None:
    assert normalize_brazilian_phone("+55 (11) 98765-4321") == "(11) 98765-4321"
    assert normalize_brazilian_phone("1133445566") == "(11) 3344-5566"
    assert normalize_brazilian_phone("99999999999") is None
    assert normalize_brazilian_phone("123") is None

    assert normalize_email("  Maria.Clara@Clinica.COM.br ") == "maria.clara@clinica.com.br"
    assert normalize_email("sem-arroba") is None

    phone_hit = match_collecting_fast_path("11987654321", "phone")
    assert phone_hit == ({"phone": "(11) 98765-4321"}, [])

    bad_phone_hit = match_collecting_fast_path("12345678", "phone")
    assert bad_phone_hit is not None
    assert bad_phone_hit[0] == {}
    assert len(bad_phone_hit[1]) == 1

    extracted = ExtractedPatientFields(phone="11 912345678", email="ANA@TESTE.COM")
    updates, errors = validate_and_normalize_fields(extracted, today=date(2026, 11, 24))
    assert errors == []
    assert updates == {"phone": "(11) 91234-5678", "email": "ana@teste.com"}

    bad_extracted = ExtractedPatientFields(phone="00000000000", email="invalido@")
    bad_updates, bad_errors = validate_and_normalize_fields(bad_extracted, today=date(2026, 11, 24))
    assert bad_updates == {}
    assert len(bad_errors) == 2

    assert detect_protocol_action("Quero cancelar #A1B2C3D4") == ("cancel", "a1b2c3d4")
    assert detect_protocol_action("Gostaria de remarcar meu horário ab12cd34") == (
        "reschedule",
        "ab12cd34",
    )
    assert detect_protocol_action("Quero cancelar minha consulta") is None
