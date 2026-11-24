"""Unit tests for deterministic clinical guardrails and field normalization."""

from datetime import date

from iara.conversation.guardrails import (
    detect_red_flag_keywords,
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
