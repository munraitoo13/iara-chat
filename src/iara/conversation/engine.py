"""Core conversational engine orchestrating structured intake, state transitions, and guardrails."""

from iara.conversation.guardrails import (
    EMERGENCY_RESPONSE_PTBR,
    detect_red_flag_keywords,
    validate_and_normalize_fields,
    validate_input_length,
)
from iara.conversation.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    FALLBACK_QUESTIONS_PTBR,
    build_analysis_payload,
    build_reply_payload,
    build_reply_system_prompt,
)
from iara.conversation.state import AgentReply, ConversationState, ConversationStatus
from iara.models.extraction import TurnAnalysis
from iara.models.patient import CoverageType, TranscriptItem
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


class IntakeAgent:
    """Stateless-LLM-backed conversational intake agent for Brazilian clinical triage."""

    def __init__(
        self,
        llm: LLMClient,
        clinic_name: str = "Clínica Iara",
        max_input_chars: int = 1000,
        max_turns: int = 30,
    ) -> None:
        self.llm = llm
        self.clinic_name = clinic_name
        self.max_input_chars = max_input_chars
        self.max_turns = max_turns
        self.state = ConversationState()
        self._reply_system_prompt = build_reply_system_prompt(clinic_name)

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
        """Process one user turn and return structured state alongside the PT-BR reply."""

        if self.state.status in (ConversationStatus.COMPLETED, ConversationStatus.EMERGENCY):
            return self._build_reply(
                "Este atendimento já foi encerrado. Inicie uma nova conversa para outro cadastro.",
            )

        if self.state.turn_count >= self.max_turns:
            self.state.status = ConversationStatus.COMPLETED
            return self._build_reply(
                "Atingimos o limite de mensagens desta sessão. "
                "Por favor, inicie um novo atendimento para continuar."
            )

        length_error = validate_input_length(user_input, self.max_input_chars)
        if length_error is not None:
            return self._build_reply(length_error)

        self.state.turn_count += 1
        self._record("user", user_input.strip())

        if detect_red_flag_keywords(user_input):
            return self._finish_emergency()

        recent = [
            {"actor": item.actor, "message": item.message} for item in self.state.transcript[-6:-1]
        ]
        analysis_payload = build_analysis_payload(
            status=self.state.status,
            patient_info=self.state.patient_info,
            recent_messages=recent,
            user_input=user_input.strip(),
        )

        try:
            analysis = self.llm.analyze_turn(EXTRACTION_SYSTEM_PROMPT, analysis_payload)
        except LLMError as exc:
            error_msg = (
                "Desculpe, ocorreu uma instabilidade ao processar sua mensagem. "
                "Por favor, tente enviar novamente em instantes."
            )
            self._record("system", error_msg)
            return self._build_reply(error_msg, error=str(exc))

        if analysis.clinical_red_flag:
            return self._finish_emergency()

        if analysis.prompt_injection_detected:
            return self._handle_prompt_injection()

        updated_fields, cleared_fields, errors = self._apply_updates(analysis)

        if self.state.status == ConversationStatus.CONFIRMING:
            return self._handle_confirming(
                analysis=analysis,
                has_changes=bool(updated_fields or cleared_fields),
                errors=errors,
                latest_user_message=user_input.strip(),
            )

        return self._handle_collecting(
            errors=errors,
            latest_user_message=user_input.strip(),
        )

    def _record(self, actor: str, message: str) -> None:
        self.state.transcript.append(TranscriptItem(actor=actor, message=message))

    def _build_reply(self, message: str, error: str | None = None) -> AgentReply:
        is_finished = self.state.status in (
            ConversationStatus.COMPLETED,
            ConversationStatus.EMERGENCY,
        )
        return AgentReply(
            message=message,
            status=self.state.status,
            patient_info=self.state.patient_info.model_copy(deep=True),
            is_finished=is_finished,
            error=error,
        )

    def _finish_emergency(self) -> AgentReply:
        self.state.status = ConversationStatus.EMERGENCY
        self._record("assistant", EMERGENCY_RESPONSE_PTBR)
        return self._build_reply(EMERGENCY_RESPONSE_PTBR)

    def _handle_prompt_injection(self) -> AgentReply:
        if self.state.status == ConversationStatus.CONFIRMING:
            reply_text = (
                "Sigo apenas o fluxo de pré-cadastro da clínica. "
                "Os dados do resumo acima estão corretos? Responda 'sim' para confirmar "
                "ou me diga qual informação deseja alterar."
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
        return self._build_reply(reply_text)

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
        for field_name, value in updates.items():
            setattr(self.state.patient_info, field_name, value)

        if updates.get("coverage_type") == CoverageType.PRIVATE:
            self.state.patient_info.insurance_payer = None
            self.state.patient_info.insurance_id = None
        elif (
            "insurance_payer" in updates or "insurance_id" in updates
        ) and self.state.patient_info.coverage_type is None:
            self.state.patient_info.coverage_type = CoverageType.INSURANCE

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

    def _handle_confirming(
        self,
        analysis: TurnAnalysis,
        has_changes: bool,
        errors: list[str],
        latest_user_message: str,
    ) -> AgentReply:
        if has_changes:
            if not self.state.patient_info.is_complete():
                self.state.status = ConversationStatus.COLLECTING
                return self._handle_collecting(
                    errors=errors,
                    latest_user_message=latest_user_message,
                )

            if errors:
                msg = f"{' '.join(errors)}\n\n" + self._summary_prompt(
                    "Mantive o cadastro atual abaixo:"
                )
            else:
                msg = self._summary_prompt("Atualizei seus dados conforme solicitado:")
            self._record("assistant", msg)
            return self._build_reply(msg)

        if errors:
            msg = (
                f"{' '.join(errors)}\n\n"
                "Se quiser tentar outra alteração, informe o campo corrigido, "
                "ou responda 'sim' para confirmar os dados atuais."
            )
            self._record("assistant", msg)
            return self._build_reply(msg)

        if analysis.confirmation_intent == "confirm" and self.state.patient_info.is_complete():
            self.state.status = ConversationStatus.COMPLETED
            msg = (
                f"Perfeito, {self.state.patient_info.full_name}! "
                f"Seu pré-cadastro na {self.clinic_name} foi concluído com sucesso. "
                "Nossa equipe dará sequência ao seu atendimento."
            )
            self._record("assistant", msg)
            return self._build_reply(msg)

        if analysis.confirmation_intent == "edit":
            msg = (
                "Sem problemas! Qual dado você gostaria de corrigir "
                "(nome, data de nascimento, endereço, convênio/particular ou queixa)?"
            )
            self._record("assistant", msg)
            return self._build_reply(msg)

        msg = (
            "Não consegui confirmar sua resposta. Responda 'sim' se os dados acima "
            "estiverem corretos ou me informe o que precisa corrigir."
        )
        self._record("assistant", msg)
        return self._build_reply(msg)

    def _handle_collecting(
        self,
        errors: list[str],
        latest_user_message: str,
    ) -> AgentReply:
        missing = self.state.patient_info.missing_fields()
        if not missing and not errors:
            self.state.status = ConversationStatus.CONFIRMING
            msg = self._summary_prompt(
                "Obrigada pelas informações! Confira seus dados antes de concluirmos:"
            )
            self._record("assistant", msg)
            return self._build_reply(msg)

        if not missing and errors:
            # All required fields are filled, yet the user also entered an invalid extra value.
            self.state.status = ConversationStatus.CONFIRMING
            msg = f"{' '.join(errors)}\n\n" + self._summary_prompt(
                "Confira os dados já registrados:"
            )
            self._record("assistant", msg)
            return self._build_reply(msg)

        return self._generate_next_question(missing, errors, latest_user_message)

    def _generate_next_question(
        self,
        missing: list[str],
        errors: list[str],
        latest_user_message: str,
    ) -> AgentReply:
        reply_payload = build_reply_payload(
            patient_info=self.state.patient_info,
            missing_fields=missing,
            validation_errors=errors,
            latest_user_message=latest_user_message,
        )

        llm_error: str | None = None
        try:
            reply_text = self.llm.generate_reply(self._reply_system_prompt, reply_payload)
        except LLMError as exc:
            llm_error = str(exc)
            fallback_q = FALLBACK_QUESTIONS_PTBR[missing[0]]
            reply_text = f"{' '.join(errors)} {fallback_q}".strip() if errors else fallback_q

        self._record("assistant", reply_text)
        return self._build_reply(reply_text, error=llm_error)
