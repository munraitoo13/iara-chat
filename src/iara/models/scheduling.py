"""Scheduling domain models, specialty triage heuristics, and appointment DTOs."""

import re
import unicodedata
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class Specialty(StrEnum):
    """Supported medical specialties for initial appointment routing."""

    GENERAL_PRACTICE = "clinica_geral"
    ORTHOPEDICS = "ortopedia"
    DERMATOLOGY = "dermatologia"
    CARDIOLOGY = "cardiologia"


SPECIALTY_LABELS_PTBR: dict[Specialty, str] = {
    Specialty.GENERAL_PRACTICE: "Clínica Geral",
    Specialty.ORTHOPEDICS: "Ortopedia",
    Specialty.DERMATOLOGY: "Dermatologia",
    Specialty.CARDIOLOGY: "Cardiologia",
}

_SPECIALTY_KEYWORDS: tuple[tuple[Specialty, re.Pattern[str]], ...] = (
    (
        Specialty.ORTHOPEDICS,
        re.compile(
            r"\b(lombar|coluna|costas|joelho|ombro|tornozelo|pulso|osso|fratura|"
            r"musculo|tendinite|articulac|entorse|ortoped)\b"
        ),
    ),
    (
        Specialty.DERMATOLOGY,
        re.compile(
            r"\b(pele|mancha|manchas|coceira|alergia\s+na\s+pele|acne|espinha|"
            r"micose|unha|cabelo|queda\s+de\s+cabelo|dermat)\b"
        ),
    ),
    (
        Specialty.CARDIOLOGY,
        re.compile(
            r"\b(coracao|pressao\s+alta|hipertensao|palpitac|arritmia|"
            r"batimento|colesterol|cardio)\b"
        ),
    ),
)


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.lower())
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def suggest_specialty_from_complaint(complaint: str | None) -> Specialty:
    """Suggest an administrative triage specialty from chief complaint keywords."""

    if not complaint:
        return Specialty.GENERAL_PRACTICE
    normalized = _strip_accents(complaint)
    for specialty, pattern in _SPECIALTY_KEYWORDS:
        if pattern.search(normalized):
            return specialty
    return Specialty.GENERAL_PRACTICE


class AppointmentSlotDTO(BaseModel):
    """Available calendar slot offered to the patient during scheduling."""

    slot_id: int
    specialty: Specialty
    specialty_label: str
    doctor_name: str
    starts_at: datetime
    display_label: str


class BookedAppointmentDTO(BaseModel):
    """Confirmed appointment booking attached to an intake session."""

    appointment_id: str
    slot_id: int
    specialty: Specialty
    specialty_label: str
    doctor_name: str
    starts_at: datetime
    status: str = "confirmed"
