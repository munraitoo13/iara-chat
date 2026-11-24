"""Domain and structured extraction models for patient intake and scheduling."""

from iara.models.extraction import ExtractedPatientFields, TurnAnalysis
from iara.models.patient import CoverageType, PatientInfo, TranscriptItem
from iara.models.scheduling import (
    SPECIALTY_LABELS_PTBR,
    AppointmentSlotDTO,
    BookedAppointmentDTO,
    Specialty,
    suggest_specialty_from_complaint,
)

__all__ = [
    "SPECIALTY_LABELS_PTBR",
    "AppointmentSlotDTO",
    "BookedAppointmentDTO",
    "CoverageType",
    "ExtractedPatientFields",
    "PatientInfo",
    "Specialty",
    "TranscriptItem",
    "TurnAnalysis",
    "suggest_specialty_from_complaint",
]
