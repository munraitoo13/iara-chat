"""Core conversational engine combining 0-token fast-paths with 1-call structured LLM turns."""

from iara.conversation.guardrails import (
    EMERGENCY_RESPONSE_PTBR,
    detect_red_flag_keywords,
    match_collecting_fast_path,
    match_confirmation_fast_path,
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
    """Hybrid conversational intake agent minimizing token usage via deterministic fast-paths."""

    def __init__(
        self,
        llm: LLMClient,
        clinic_name: str = "Clínica Modelo",
        max_input_chars: int = 1000,
        max_turns: int = 30,
        state: ConversationState | None = None,
    ) -> None:
        self.llm = llm
        self.clinic_name = clinic_name
        self.max_input_chars = max_input_chars
        self.max_turns = max_turns
        self.state = state or ConversationState()
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

        if self.state.turn_count >= self.max_turns:
            self.state.status = ConversationStatus.COMPLETED
            return self._build_reply(
                "Atingimos o limite de mensagens desta sessão. "
                "Por favor, inicie um novo atendimento para continuar.",
                used_llm=False,
            )

        length_error = validate_input_length(user_input, self.max_input_chars)
        if length_error is not None:
            return self._build_reply(length_error, used_llm=False)

        cleaned_input = user_input.strip()
        self.state.turn_count += 1
        self._record("user", cleaned_input)

        if detect_red_flag_keywords(cleaned_input):
            return self._finish_emergency(used_llm=False)

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
            error_msg = (
                "Desculpe, ocorreu uma instabilidade ao processar sua mensagem. "
                "Por favor, tente enviar novamente em instantes."
            )
            self._record("system", error_msg)
            return self._build_reply(error_msg, used_llm=True, error=str(exc))

        if analysis.clinical_red_flag:
            return self._finish_emergency(used_llm=True)

        if analysis.prompt_injection_detected:
            return self._handle_prompt_injection(used_llm=True)

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
            error=error,
        )

    def _finish_emergency(self, *, used_llm: bool) -> AgentReply:
        self.state.status = ConversationStatus.EMERGENCY
        self._record("assistant", EMERGENCY_RESPONSE_PTBR)
        return self._build_reply(EMERGENCY_RESPONSE_PTBR, used_llm=used_llm)

    def _handle_prompt_injection(self, *, used_llm: bool) -> AgentReply:
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
            self.state.status = ConversationStatus.COMPLETED
            msg = (
                f"Perfeito, {self.state.patient_info.full_name}! "
                f"Seu pré-cadastro na {self.clinic_name} foi concluído com sucesso. "
                "Nossa equipe dará sequência ao seu atendimento."
            )
            self._record("assistant", msg)
            return self._build_reply(msg, used_llm=used_llm)

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
