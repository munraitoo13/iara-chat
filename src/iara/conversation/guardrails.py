"""Deterministic validation rules and clinical safety guardrails."""

import re
import unicodedata
from datetime import date, datetime

from iara.models.extraction import ExtractedPatientFields
from iara.models.patient import CoverageType

EMERGENCY_RESPONSE_PTBR = (
    "Atenção: os sintomas informados podem indicar uma emergência médica ou situação de risco.\n"
    "Como sou uma assistente apenas para triagem inicial e pré-cadastro, não posso avaliar "
    "quadros urgentes com segurança.\n\n"
    "- Em emergência clínica, ligue imediatamente para o SAMU **192**, Bombeiros **193** "
    "ou procure o pronto-socorro/UPA mais próximo.\n"
    "- Em caso de crise emocional ou risco de autoagressão, ligue gratuitamente para o "
    "CVV **188** (atendimento 24h)."
)

_PLACEHOLDER_STRINGS = frozenset(
    {
        "none",
        "null",
        "n/a",
        "na",
        "nenhum",
        "nenhuma",
        "nao sei",
        "nao tenho",
        "idk",
        "unknown",
        "desconhecido",
        "anonimo",
    }
)

_RED_FLAG_PATTERNS = (
    re.compile(r"\bdor\s+(muito\s+|bem\s+|super\s+)?(forte\s+)?no\s+peito\b"),
    re.compile(r"\bdor\s+toracica\b"),
    re.compile(r"\bnao\s+consigo\s+respirar\b"),
    re.compile(r"\bfalta\s+de\s+ar\s+(intensa|grave|severa|muito\s+forte)\b"),
    re.compile(r"\b(desmaiei|desmaiando|desmaio|perdi\s+a\s+consciencia)\b"),
    re.compile(r"\b(convulsao|convulsoes|tendo\s+ataque)\b"),
    re.compile(r"\b(hemorragia|sangramento\s+(intenso|forte|incontrolavel))\b"),
    re.compile(r"\b(avc|derrame|boca\s+torta|fala\s+arrastada)\b"),
    re.compile(r"\b(suicid|tirar\s+(a\s+)?minha\s+vida|me\s+matar|quero\s+morrer)\b"),
)


def _strip_accents(value: str) -> str:
    """Normalize text to ASCII lowercase for resilient accent-insensitive matching."""

    normalized = unicodedata.normalize("NFD", value.lower())
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _collapse_spaces(value: str) -> str:
    return " ".join(value.split())


def validate_input_length(text: str, max_chars: int) -> str | None:
    """Return a PT-BR error message if input is empty or exceeds the character limit."""

    stripped = text.strip()
    if not stripped:
        return "Por favor, digite uma mensagem para continuarmos o atendimento."
    if len(stripped) > max_chars:
        return (
            f"Sua mensagem ficou muito longa (limite de {max_chars} caracteres). "
            "Por favor, resuma em uma mensagem mais curta."
        )
    return None


def detect_red_flag_keywords(text: str) -> bool:
    """Detect acute emergency or self-harm phrases deterministically before LLM calls."""

    normalized = _strip_accents(text)
    return any(pattern.search(normalized) for pattern in _RED_FLAG_PATTERNS)


def _parse_birth_date(raw_date: str) -> date | None:
    """Parse ISO YYYY-MM-DD (preferred) or Brazilian DD/MM/YYYY date strings."""

    cleaned = raw_date.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def validate_and_normalize_fields(
    extracted: ExtractedPatientFields,
    today: date | None = None,
) -> tuple[dict[str, object], list[str]]:
    """Validate and normalize extracted fields into domain values and PT-BR error messages."""

    updates: dict[str, object] = {}
    errors: list[str] = []
    ref_today = today or date.today()

    if extracted.full_name is not None:
        name = _collapse_spaces(extracted.full_name)
        if (
            len(name) >= 2
            and any(ch.isalpha() for ch in name)
            and _strip_accents(name) not in _PLACEHOLDER_STRINGS
        ):
            updates["full_name"] = name
        else:
            errors.append("Informe um nome válido (sem números isolados ou abreviações vazias).")

    if extracted.date_of_birth_iso is not None:
        parsed_dob = _parse_birth_date(extracted.date_of_birth_iso)
        min_dob = date(ref_today.year - 130, 1, 1)
        if parsed_dob is not None and min_dob <= parsed_dob <= ref_today:
            updates["date_of_birth"] = parsed_dob
        else:
            errors.append(
                "Data de nascimento inválida. Informe uma data válida no formato DD/MM/AAAA."
            )

    if extracted.address is not None:
        address = _collapse_spaces(extracted.address)
        if (
            len(address) >= 8
            and any(ch.isalpha() for ch in address)
            and _strip_accents(address) not in _PLACEHOLDER_STRINGS
        ):
            updates["address"] = address
        else:
            errors.append(
                "Endereço incompleto. Informe pelo menos rua/logradouro, número, cidade e estado."
            )

    if extracted.coverage_type is not None:
        updates["coverage_type"] = CoverageType(extracted.coverage_type)

    if extracted.insurance_payer is not None:
        payer = _collapse_spaces(extracted.insurance_payer)
        if (
            len(payer) >= 2
            and any(ch.isalpha() for ch in payer)
            and _strip_accents(payer) not in _PLACEHOLDER_STRINGS
        ):
            updates["insurance_payer"] = payer
        else:
            errors.append(
                "Nome do convênio inválido. Informe a operadora ou diga que é particular."
            )

    if extracted.insurance_id is not None:
        insurance_id = _collapse_spaces(extracted.insurance_id)
        if (
            len(insurance_id) >= 4
            and any(ch.isalnum() for ch in insurance_id)
            and _strip_accents(insurance_id) not in _PLACEHOLDER_STRINGS
        ):
            updates["insurance_id"] = insurance_id
        else:
            errors.append(
                "Número da carteirinha inválido. Informe o código completo da sua carteirinha."
            )

    if extracted.chief_complaint is not None:
        complaint = _collapse_spaces(extracted.chief_complaint)
        if (
            len(complaint) >= 3
            and any(ch.isalpha() for ch in complaint)
            and _strip_accents(complaint) not in _PLACEHOLDER_STRINGS
        ):
            updates["chief_complaint"] = complaint
        else:
            errors.append("Descreva brevemente os sintomas ou o motivo da sua consulta.")

    return updates, errors
