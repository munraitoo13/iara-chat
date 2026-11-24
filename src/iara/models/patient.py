"""Patient intake domain models and Brazilian clinical formatting."""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from iara.models.scheduling import SPECIALTY_LABELS_PTBR, Specialty


class CoverageType(StrEnum):
    """Supported payment/coverage modalities in Brazilian healthcare."""

    INSURANCE = "convenio"
    PRIVATE = "particular"


FIELD_LABELS_PTBR: dict[str, str] = {
    "full_name": "nome completo",
    "date_of_birth": "data de nascimento",
    "address": "endereço completo (rua, número, cidade, estado e CEP)",
    "coverage_type": "modalidade de atendimento (convênio ou particular)",
    "insurance_payer": "operadora do convênio",
    "insurance_id": "número da carteirinha do convênio",
    "chief_complaint": "queixa principal ou motivo da consulta",
}


class PatientInfo(BaseModel):
    """Structured clinical intake record collected by Iara."""

    full_name: str | None = None
    date_of_birth: date | None = None
    address: str | None = None
    coverage_type: CoverageType | None = None
    insurance_payer: str | None = None
    insurance_id: str | None = None
    chief_complaint: str | None = None
    preferred_specialty: Specialty | None = None

    def missing_fields(self) -> list[str]:
        """Return ordered keys of fields that still need to be collected."""

        missing: list[str] = []
        for field_name in ("full_name", "date_of_birth", "address", "coverage_type"):
            if getattr(self, field_name) is None:
                missing.append(field_name)

        if self.coverage_type == CoverageType.INSURANCE:
            for insurance_field in ("insurance_payer", "insurance_id"):
                if getattr(self, insurance_field) is None:
                    missing.append(insurance_field)

        if self.chief_complaint is None:
            missing.append("chief_complaint")

        return missing

    def is_complete(self) -> bool:
        """Return True when all required fields for the selected modality are present."""

        return not self.missing_fields()

    def format_summary_ptbr(self) -> str:
        """Format collected patient details in Brazilian Portuguese for confirmation."""

        dob_text = self.date_of_birth.strftime("%d/%m/%Y") if self.date_of_birth else "-"
        coverage_label = (
            "Convênio" if self.coverage_type == CoverageType.INSURANCE else "Particular"
        )

        lines = [
            f"- Nome completo: {self.full_name or '-'}",
            f"- Data de nascimento: {dob_text}",
            f"- Endereço: {self.address or '-'}",
            f"- Atendimento: {coverage_label}",
        ]
        if self.coverage_type == CoverageType.INSURANCE:
            lines.append(f"- Operadora: {self.insurance_payer or '-'}")
            lines.append(f"- Carteirinha: {self.insurance_id or '-'}")
        lines.append(f"- Queixa principal: {self.chief_complaint or '-'}")
        if self.preferred_specialty is not None:
            lines.append(
                f"- Especialidade sugerida: {SPECIALTY_LABELS_PTBR[self.preferred_specialty]}"
            )
        return "\n".join(lines)


class TranscriptItem(BaseModel):
    """Single message entry in a conversation transcript."""

    actor: Literal["user", "assistant", "human_agent", "system"]
    message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
