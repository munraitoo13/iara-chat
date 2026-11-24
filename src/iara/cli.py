"""Development CLI entrypoint for local testing of the Iara intake engine."""

import sys

from pydantic import ValidationError

from iara.config import get_settings
from iara.conversation.engine import IntakeAgent
from iara.services.llm import GeminiClient


def main() -> None:
    """Run an interactive CLI session against the configured Gemini model."""

    try:
        settings = get_settings()
    except ValidationError:
        print(
            "Erro de configuração: defina GEMINI_API_KEY no arquivo .env "
            "(copie .env.example para .env).",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    llm = GeminiClient(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_model,
    )
    agent = IntakeAgent(
        llm=llm,
        clinic_name=settings.clinic_name,
        max_input_chars=settings.max_input_chars,
        max_turns=settings.max_turns,
    )

    print(f"=== {settings.clinic_name} (Modo CLI Dev) ===")
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
        print(f"\nIara: {reply.message}\n")
        if reply.error:
            print(f"[aviso técnico: {reply.error}]\n", file=sys.stderr)
        if reply.is_finished:
            break


if __name__ == "__main__":
    main()
