"""Development CLI entrypoint for local testing of the Iara intake engine."""

import sys

from pydantic import ValidationError

from iara.config import get_settings
from iara.conversation.engine import IntakeAgent
from iara.services.llm import create_llm_client


def main() -> None:
    """Run an interactive CLI session against the configured LLM provider."""

    try:
        settings = get_settings()
    except ValidationError as exc:
        print(
            "Erro de configuração: copie .env.example para .env e defina "
            "AI_GATEWAY_API_KEY (ou GEMINI_API_KEY se LLM_PROVIDER=gemini).\n"
            f"Detalhe: {exc.errors()[0]['msg']}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    llm = create_llm_client(settings)
    agent = IntakeAgent(
        llm=llm,
        clinic_name=settings.clinic_name,
        max_input_chars=settings.max_input_chars,
        max_turns=settings.max_turns,
    )

    print(
        f"=== Iara · {settings.clinic_name} "
        f"({settings.llm_provider}: {settings.active_model_name()}) ==="
    )
    print("Digite 'sair' a qualquer momento para encerrar.\n")
    print(f"Iara: {agent.greeting()}\n")

    while True:
        try:
            user_input = input("Você: ")
        except EOFError, KeyboardInterrupt:
            print("\nIara: Atendimento encerrado. Até breve!")
            break

        if user_input.strip().lower() in {"sair", "exit", "quit"}:
            print("Iara: Obrigada por entrar em contato. Até breve!")
            break

        reply = agent.handle_message(user_input)
        mode_tag = "[1 chamada · LLM]" if reply.used_llm else "[0 tokens · fast-path]"
        print(f"\nIara {mode_tag}: {reply.message}\n")
        if reply.error:
            print(f"[aviso técnico: {reply.error}]\n", file=sys.stderr)
        if reply.is_finished:
            break


if __name__ == "__main__":
    main()
