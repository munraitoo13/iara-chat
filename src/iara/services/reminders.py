"""Appointment reminder delivery adapters (Mock and Resend Email)."""

from typing import Protocol

import httpx

from iara.config import Settings


class ReminderSender(Protocol):
    """Protocol decoupling reminder scheduling/dispatch from delivery providers."""

    def send(
        self,
        recipient_email: str | None,
        subject: str,
        body: str,
    ) -> tuple[bool, str | None]:
        """Deliver a reminder message and return (success, error_detail)."""
        ...


class MockReminderSender:
    """Offline mock sender storing simulated deliveries for portfolio/demo visibility."""

    def send(
        self,
        recipient_email: str | None,
        subject: str,
        body: str,
    ) -> tuple[bool, str | None]:
        return True, None


class ResendEmailSender:
    """Real transactional email delivery adapter using the Resend REST API."""

    def __init__(self, api_key: str, from_email: str) -> None:
        self.api_key = api_key
        self.from_email = from_email

    def send(
        self,
        recipient_email: str | None,
        subject: str,
        body: str,
    ) -> tuple[bool, str | None]:
        if not recipient_email:
            return False, "Paciente sem e-mail cadastrado para envio via Resend."

        try:
            response = httpx.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": self.from_email,
                    "to": [recipient_email],
                    "subject": subject,
                    "text": body,
                },
                timeout=10.0,
            )
            if 200 <= response.status_code < 300:
                return True, None
            return False, f"Resend HTTP {response.status_code}: {response.text[:200]}"
        except Exception as exc:
            return False, f"Resend request failed: {exc}"


def create_reminder_sender(settings: Settings) -> tuple[str, ReminderSender]:
    """Instantiate Resend email sender when configured, otherwise safe offline Mock sender."""

    resend_key = settings.configured_resend_api_key()
    if resend_key is not None:
        return "resend_email", ResendEmailSender(
            api_key=resend_key,
            from_email=settings.resend_from_email,
        )
    return "mock", MockReminderSender()
