"""Core conversational engine for intake, scheduling, and human-in-the-loop handoff."""

from typing import Protocol

from iara.conversation.guardrails import (
    EMERGENCY_RESPONSE_PTBR,
    detect_human_handoff_request,
    detect_red_flag_keywords,
    match_collecting_fast_path,
    match_confirmation_fast_path,
    match_slot_selection,
    match_specialty_choice,
    validate_and_normalize_fields,
    validate_input_length,
)
from iara.conversation.prompts import (
    FALLBACK_QUESTIONS_PTBR,
    build_analysis_payload,
    build_turn_system_prompt,
)
from iara.conversation.state import AgentReply, ConversationState, ConversationStatus
from iara.models.extraction import TurnAnalysis
from iara.models.patient import CoverageType, TranscriptItem
from iara.models.scheduling import (
    SPECIALTY_LABELS_PTBR,
    AppointmentSlotDTO,
    BookedAppointmentDTO,
    Specialty,
    suggest_specialty_from_complaint,
)
from iara.services.llm import LLMClient, LLMError

_UPDATABLE_FIELDS = frozenset(
    {
        "full_name",
        "date_of_birth",
        "address",
        "coverage_type",
        "insurance_payer",
        "insurance_id",
        "chief_complaint",
    }
)

_MAX_CONSECUTIVE_LLM_ERRORS = 2


class SchedulingProvider(Protocol):
    """Protocol decoupling conversational scheduling from database/calendar storage."""

    def list_available_slots(
        self,
        specialty: Specialty | None = None,
        limit: int = 4,
    ) -> list[AppointmentSlotDTO]:
        """Return available upcoming appointment slots."""
        ...

    def book_slot(
        self,
        session_id: str,
        slot_id: int,
        patient_name: str,
    ) -> BookedAppointmentDTO | None:
        """Reserve an available slot for the given intake session."""
        ...


class IntakeAgent:
    """Hybrid conversational agent combining 0-token fast-paths, scheduling, and handoff."""

    def __init__(
        self,
        llm: LLMClient,
        clinic_name: str = "Clínica Modelo",
        max_input_chars: int = 1000,
        max_turns: int = 30,
        state: ConversationState | None = None,
        scheduler: SchedulingProvider | None = None,
        session_id: str | None = None,
    ) -> None:
        self.llm = llm
        self.clinic_name = clinic_name
        self.max_input_chars = max_input_chars
        self.max_turns = max_turns
        self.state = state or ConversationState()
        self.scheduler = scheduler
        self.session_id = session_id
        self._turn_system_prompt = build_turn_system_prompt(clinic_name)

    def greeting(self) -> str:
        """Return initial PT-BR welcome with LGPD privacy notice and record it once."""

        message = (
            f"Olá! Eu sou a Iara, assistente virtual de pré-cadastro da {self.clinic_name}.\n"
            "Seus dados serão usados exclusivamente para organizar seu atendimento (LGPD).\n"
            "Para começarmos, qual é o seu nome completo?"
        )
        if not self.state.transcript:
            self._record("assistant", message)
        return message

    def handle_message(self, user_input: str) -> AgentReply:
        """Process one user turn via fast-path (0 tokens) or unified 1-call LLM extraction."""

        if self.state.status in (ConversationStatus.COMPLETED, ConversationStatus.EMERGENCY):
            return self._build_reply(
                "Este atendimento já foi encerrado. Inicie uma nova conversa para outro cadastro.",
                used_llm=False,
            )

        length_error = validate_input_length(user_input, self.max_input_chars)
        if length_error is not None:
            return self._build_reply(length_error, used_llm=False)

        cleaned_input = user_input.strip()

        if self.state.status == ConversationStatus.HUMAN_TAKEOVER:
            self.state.turn_count += 1
            self._record("user", cleaned_input)
            return self._build_reply("", used_llm=False)

        if self.state.status == ConversationStatus.WAITING_HUMAN:
            self.state.turn_count += 1
            self._record("user", cleaned_input)
            if detect_red_flag_keywords(cleaned_input):
                return self._finish_emergency(used_llm=False)
            wait_msg = (
                "Sua solicitação já está na fila de atendimento humano da clínica. "
                "Nossa equipe assumirá a conversa por aqui em instantes."
            )
            self._record("assistant", wait_msg)
            return self._build_reply(wait_msg, used_llm=False)

        if self.state.turn_count >= self.max_turns:
            self.state.turn_count += 1
            self._record("user", cleaned_input)
            return self._escalate_to_human(reason="max_turns_reached", used_llm=False)

        self.state.turn_count += 1
        self._record("user", cleaned_input)

        if detect_red_flag_keywords(cleaned_input):
            return self._finish_emergency(used_llm=False)

        if detect_human_handoff_request(cleaned_input):
            return self._escalate_to_human(reason="patient_requested", used_llm=False)

        fast_reply = self._try_fast_path(cleaned_input)
        if fast_reply is not None:
            return fast_reply

        recent = [
            {"actor": item.actor, "message": item.message} for item in self.state.transcript[-6:-1]
        ]
        analysis_payload = build_analysis_payload(
            status=self.state.status,
            patient_info=self.state.patient_info,
            recent_messages=recent,
            user_input=cleaned_input,
        )

        try:
            analysis = self.llm.analyze_turn(self._turn_system_prompt, analysis_payload)
        except LLMError as exc:
            self.state.consecutive_llm_errors += 1
            if self.state.consecutive_llm_errors >= _MAX_CONSECUTIVE_LLM_ERRORS:
                return self._escalate_to_human(
                    reason="consecutive_llm_errors",
                    used_llm=True,
                    error=str(exc),
                )
            error_msg = (
                "Desculpe, ocorreu uma instabilidade ao processar sua mensagem. "
                "Por favor, tente enviar novamente em instantes."
            )
            self._record("system", error_msg)
            return self._build_reply(error_msg, used_llm=True, error=str(exc))

        self.state.consecutive_llm_errors = 0

        if analysis.clinical_red_flag:
            return self._finish_emergency(used_llm=True)

        if analysis.prompt_injection_detected:
            return self._handle_prompt_injection(used_llm=True)

        if analysis.human_handoff_requested:
            return self._escalate_to_human(reason="patient_requested", used_llm=True)

        if self.state.status == ConversationStatus.SCHEDULING:
            return self._handle_scheduling_llm(analysis)

        updated_fields, cleared_fields, errors = self._apply_updates(analysis)

        if self.state.status == ConversationStatus.CONFIRMING:
            return self._handle_confirming(
                intent=analysis.confirmation_intent,
                has_changes=bool(updated_fields or cleared_fields),
                errors=errors,
                used_llm=True,
            )

        return self._handle_collecting(
            errors=errors,
            suggested_reply=analysis.assistant_reply,
            used_llm=True,
        )

    def _try_fast_path(self, cleaned_input: str) -> AgentReply | None:
        if self.state.status == ConversationStatus.CONFIRMING:
            fast_intent = match_confirmation_fast_path(cleaned_input)
            if fast_intent is None:
                return None
            return self._handle_confirming(
                intent=fast_intent,
                has_changes=False,
                errors=[],
                used_llm=False,
            )

        if self.state.status == ConversationStatus.SCHEDULING:
            return self._try_scheduling_fast_path(cleaned_input)

        missing = self.state.patient_info.missing_fields()
        if not missing:
            return None

        fast_result = match_collecting_fast_path(cleaned_input, missing[0])
        if fast_result is None:
            return None

        updates, errors = fast_result
        self._merge_valid_updates(updates)
        missing_after = self.state.patient_info.missing_fields()

        if not missing_after and not errors:
            return self._handle_collecting(errors=[], suggested_reply=None, used_llm=False)

        if errors:
            next_question = FALLBACK_QUESTIONS_PTBR[missing_after[0]]
            msg = f"{' '.join(errors)} {next_question}"
            self._record("assistant", msg)
            return self._build_reply(msg, used_llm=False)

        ack = self._fast_path_acknowledgment(updates)
        msg = f"{ack} {FALLBACK_QUESTIONS_PTBR[missing_after[0]]}".strip()
        self._record("assistant", msg)
        return self._build_reply(msg, used_llm=False)

    def _try_scheduling_fast_path(self, cleaned_input: str) -> AgentReply | None:
        if self.scheduler is None:
            return None

        slots = self._current_specialty_slots()
        matched_slot = match_slot_selection(cleaned_input, slots)
        if matched_slot is not None:
            return self._book_selected_slot(matched_slot, used_llm=False)

        requested_specialty = match_specialty_choice(cleaned_input)
        if requested_specialty is not None:
            self.state.patient_info.preferred_specialty = requested_specialty
            label = SPECIALTY_LABELS_PTBR[requested_specialty]
            return self._present_slots_for_specialty(
                requested_specialty,
                header=f"Sem problemas! Busquei horários para **{label}**:",
                used_llm=False,
            )
        return None

    def _fast_path_acknowledgment(self, updates: dict[str, object]) -> str:
        coverage = updates.get("coverage_type")
        if coverage == CoverageType.PRIVATE:
            return "Certo, atendimento particular!"
        if coverage == CoverageType.INSURANCE:
            return "Certo, atendimento por convênio!"
        if "date_of_birth" in updates:
            return "Data registrada!"
        return "Anotado!"

    def _record(self, actor: str, message: str) -> None:
        self.state.transcript.append(TranscriptItem(actor=actor, message=message))

    def _build_reply(
        self,
        message: str,
        *,
        used_llm: bool,
        error: str | None = None,
    ) -> AgentReply:
        is_finished = self.state.status in (
            ConversationStatus.COMPLETED,
            ConversationStatus.EMERGENCY,
        )
        return AgentReply(
            message=message,
            status=self.state.status,
            patient_info=self.state.patient_info.model_copy(deep=True),
            is_finished=is_finished,
            used_llm=used_llm,
            booked_appointment=self.state.booked_appointment,
            error=error,
        )

    def _finish_emergency(self, *, used_llm: bool) -> AgentReply:
        self.state.status = ConversationStatus.EMERGENCY
        self._record("assistant", EMERGENCY_RESPONSE_PTBR)
        return self._build_reply(EMERGENCY_RESPONSE_PTBR, used_llm=used_llm)

    def _escalate_to_human(
        self,
        *,
        reason: str,
        used_llm: bool,
        error: str | None = None,
    ) -> AgentReply:
        self.state.status = ConversationStatus.WAITING_HUMAN
        self.state.handoff_reason = reason

        if reason == "consecutive_llm_errors":
            msg = (
                "Estou com dificuldade técnica temporária para processar seu atendimento. "
                "Já encaminhei sua conversa para nossa equipe humana responder em breve."
            )
        elif reason == "no_available_slots":
            msg = (
                "No momento não encontrei horários disponíveis na agenda automática. "
                "Encaminhei seu pré-cadastro para nossa recepção humana confirmar o melhor encaixe."
            )
        else:
            msg = (
                "Combinado! Já sinalizei nossa equipe de recepção para assumir seu atendimento "
                "humanizado por aqui. Por favor, aguarde um instante."
            )

        self._record("assistant", msg)
        return self._build_reply(msg, used_llm=used_llm, error=error)

    def _handle_prompt_injection(self, *, used_llm: bool) -> AgentReply:
        if self.state.status == ConversationStatus.CONFIRMING:
            reply_text = (
                "Sigo apenas o fluxo de pré-cadastro da clínica. "
                "Os dados do resumo acima estão corretos? Responda 'sim' para confirmar "
                "ou me diga qual informação deseja alterar."
            )
        elif self.state.status == ConversationStatus.SCHEDULING:
            reply_text = (
                "Sigo apenas o fluxo de agendamento da clínica. "
                "Escolha o número de um dos horários acima ou peça atendimento humano."
            )
        else:
            missing = self.state.patient_info.missing_fields()
            next_question = FALLBACK_QUESTIONS_PTBR.get(
                missing[0], "Como posso ajudar com seu pré-cadastro?"
            )
            reply_text = (
                f"Posso ajudar apenas com o seu pré-cadastro para consulta. {next_question}"
            )
        self._record("assistant", reply_text)
        return self._build_reply(reply_text, used_llm=used_llm)

    def _merge_valid_updates(self, updates: dict[str, object]) -> None:
        for field_name, value in updates.items():
            setattr(self.state.patient_info, field_name, value)

        if updates.get("coverage_type") == CoverageType.PRIVATE:
            self.state.patient_info.insurance_payer = None
            self.state.patient_info.insurance_id = None
        elif (
            "insurance_payer" in updates or "insurance_id" in updates
        ) and self.state.patient_info.coverage_type is None:
            self.state.patient_info.coverage_type = CoverageType.INSURANCE

    def _apply_updates(
        self,
        analysis: TurnAnalysis,
    ) -> tuple[dict[str, object], list[str], list[str]]:
        cleared_fields: list[str] = []
        for field_name in analysis.fields_to_clear:
            if field_name in _UPDATABLE_FIELDS:
                setattr(self.state.patient_info, field_name, None)
                cleared_fields.append(field_name)
                if field_name == "coverage_type":
                    self.state.patient_info.insurance_payer = None
                    self.state.patient_info.insurance_id = None

        updates, errors = validate_and_normalize_fields(analysis.extracted)
        self._merge_valid_updates(updates)

        if (
            not updates
            and not cleared_fields
            and analysis.invalid_field_reason
            and not errors
            and self.state.status == ConversationStatus.COLLECTING
        ):
            errors.append(analysis.invalid_field_reason)

        return updates, cleared_fields, errors

    def _summary_prompt(self, header: str) -> str:
        return (
            f"{header}\n\n"
            f"{self.state.patient_info.format_summary_ptbr()}\n\n"
            "Os dados estão corretos? Responda 'sim' para confirmar ou diga o que deseja ajustar."
        )

    def _current_specialty_slots(self) -> list[AppointmentSlotDTO]:
        if self.scheduler is None:
            return []
        specialty = self.state.patient_info.preferred_specialty
        slots = self.scheduler.list_available_slots(specialty=specialty, limit=4)
        if not slots and specialty is not None:
            slots = self.scheduler.list_available_slots(specialty=None, limit=4)
        return slots

    def _present_slots_for_specialty(
        self,
        specialty: Specialty,
        *,
        header: str,
        used_llm: bool,
    ) -> AgentReply:
        assert self.scheduler is not None
        slots = self.scheduler.list_available_slots(specialty=specialty, limit=4)
        fallback_note = ""
        if not slots:
            slots = self.scheduler.list_available_slots(specialty=None, limit=4)
            if not slots:
                return self._escalate_to_human(reason="no_available_slots", used_llm=used_llm)
            fallback_note = (
                f"\n*(Não há horários livres em {SPECIALTY_LABELS_PTBR[specialty]} no momento; "
                "listando outras especialidades disponíveis.)*\n"
            )

        self.state.status = ConversationStatus.SCHEDULING
        slot_lines = [
            f"{idx}. {slot.display_label} ({slot.specialty_label})"
            for idx, slot in enumerate(slots, start=1)
        ]
        msg = (
            f"{header}{fallback_note}\n\n"
            + "\n".join(slot_lines)
            + "\n\nEscolha o número do horário desejado, peça outra especialidade "
            "(Clínica Geral, Ortopedia, Dermatologia ou Cardiologia) ou diga "
            "'falar com atendente' para ajuda humana."
        )
        self._record("assistant", msg)
        return self._build_reply(msg, used_llm=used_llm)

    def _complete_or_start_scheduling(self, *, used_llm: bool) -> AgentReply:
        if self.scheduler is None:
            self.state.status = ConversationStatus.COMPLETED
            msg = (
                f"Perfeito, {self.state.patient_info.full_name}! "
                f"Seu pré-cadastro na {self.clinic_name} foi concluído com sucesso. "
                "Nossa equipe dará sequência ao seu atendimento."
            )
            self._record("assistant", msg)
            return self._build_reply(msg, used_llm=used_llm)

        specialty = self.state.patient_info.preferred_specialty or suggest_specialty_from_complaint(
            self.state.patient_info.chief_complaint
        )
        self.state.patient_info.preferred_specialty = specialty
        specialty_label = SPECIALTY_LABELS_PTBR[specialty]
        header = (
            f"Cadastro confirmado! Para agilizar seu agendamento, sugerimos **{specialty_label}** "
            "(triagem administrativa inicial, sem caráter diagnóstico). Confira os horários livres:"
        )
        return self._present_slots_for_specialty(specialty, header=header, used_llm=used_llm)

    def _book_selected_slot(self, slot: AppointmentSlotDTO, *, used_llm: bool) -> AgentReply:
        assert self.scheduler is not None
        session_ref = self.session_id or "cli-session"
        patient_name = self.state.patient_info.full_name or "Paciente"
        booked = self.scheduler.book_slot(session_ref, slot.slot_id, patient_name)
        if booked is None:
            specialty = self.state.patient_info.preferred_specialty or Specialty.GENERAL_PRACTICE
            return self._present_slots_for_specialty(
                specialty,
                header="Esse horário acabou de ser preenchido. Escolha uma das opções abaixo:",
                used_llm=used_llm,
            )

        self.state.booked_appointment = booked
        self.state.status = ConversationStatus.COMPLETED
        protocol = booked.appointment_id[:8].upper()
        date_str = booked.starts_at.strftime("%d/%m/%Y às %H:%M")
        msg = (
            f"Agendamento confirmado, {patient_name}!\n\n"
            f"- Protocolo: #{protocol}\n"
            f"- Especialidade: {booked.specialty_label}\n"
            f"- Profissional: {booked.doctor_name}\n"
            f"- Data e hora: {date_str}\n\n"
            f"A {self.clinic_name} agradece o contato. Caso precise remarcar, "
            "nossa equipe está à disposição."
        )
        self._record("assistant", msg)
        return self._build_reply(msg, used_llm=used_llm)

    def _handle_scheduling_llm(self, analysis: TurnAnalysis) -> AgentReply:
        if analysis.requested_specialty is not None:
            new_specialty = Specialty(analysis.requested_specialty)
            self.state.patient_info.preferred_specialty = new_specialty
            label = SPECIALTY_LABELS_PTBR[new_specialty]
            return self._present_slots_for_specialty(
                new_specialty,
                header=f"Busquei os horários disponíveis em **{label}**:",
                used_llm=True,
            )

        specialty = self.state.patient_info.preferred_specialty or Specialty.GENERAL_PRACTICE
        return self._present_slots_for_specialty(
            specialty,
            header="Não identifiquei o horário escolhido. Selecione uma das opções abaixo:",
            used_llm=True,
        )

    def _handle_confirming(
        self,
        *,
        intent: str,
        has_changes: bool,
        errors: list[str],
        used_llm: bool,
    ) -> AgentReply:
        if has_changes:
            if not self.state.patient_info.is_complete():
                self.state.status = ConversationStatus.COLLECTING
                return self._handle_collecting(
                    errors=errors,
                    suggested_reply=None,
                    used_llm=used_llm,
                )

            if errors:
                msg = f"{' '.join(errors)}\n\n" + self._summary_prompt(
                    "Mantive o cadastro atual abaixo:"
                )
            else:
                msg = self._summary_prompt("Atualizei seus dados conforme solicitado:")
            self._record("assistant", msg)
            return self._build_reply(msg, used_llm=used_llm)

        if errors:
            msg = (
                f"{' '.join(errors)}\n\n"
                "Se quiser tentar outra alteração, informe o campo corrigido, "
                "ou responda 'sim' para confirmar os dados atuais."
            )
            self._record("assistant", msg)
            return self._build_reply(msg, used_llm=used_llm)

        if intent == "confirm" and self.state.patient_info.is_complete():
            return self._complete_or_start_scheduling(used_llm=used_llm)

        if intent == "edit":
            msg = (
                "Sem problemas! Qual dado você gostaria de corrigir "
                "(nome, data de nascimento, endereço, convênio/particular ou queixa)?"
            )
            self._record("assistant", msg)
            return self._build_reply(msg, used_llm=used_llm)

        msg = (
            "Não consegui confirmar sua resposta. Responda 'sim' se os dados acima "
            "estiverem corretos ou me informe o que precisa corrigir."
        )
        self._record("assistant", msg)
        return self._build_reply(msg, used_llm=used_llm)

    def _handle_collecting(
        self,
        *,
        errors: list[str],
        suggested_reply: str | None,
        used_llm: bool,
    ) -> AgentReply:
        missing = self.state.patient_info.missing_fields()
        if not missing and not errors:
            self.state.status = ConversationStatus.CONFIRMING
            msg = self._summary_prompt(
                "Obrigada pelas informações! Confira seus dados antes de concluirmos:"
            )
            self._record("assistant", msg)
            return self._build_reply(msg, used_llm=used_llm)

        if not missing and errors:
            self.state.status = ConversationStatus.CONFIRMING
            msg = f"{' '.join(errors)}\n\n" + self._summary_prompt(
                "Confira os dados já registrados:"
            )
            self._record("assistant", msg)
            return self._build_reply(msg, used_llm=used_llm)

        cleaned_reply = (suggested_reply or "").strip()
        if not errors and cleaned_reply:
            reply_text = cleaned_reply
        else:
            fallback_q = FALLBACK_QUESTIONS_PTBR[missing[0]]
            reply_text = f"{' '.join(errors)} {fallback_q}".strip() if errors else fallback_q

        self._record("assistant", reply_text)
        return self._build_reply(reply_text, used_llm=used_llm)
