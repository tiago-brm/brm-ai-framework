from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

import httpx

RESEND_API_URL = "https://api.resend.com/emails"


class EmailSendError(Exception):
    pass


def email_configured() -> bool:
    return _resend_configured() or _smtp_configured()


def _resend_configured() -> bool:
    return bool(os.getenv("RESEND_BRM_API_KEY"))


def _smtp_configured() -> bool:
    return all(os.getenv(key) for key in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS"))


def send_email(destinatario: str, assunto: str, corpo: str) -> dict[str, str]:
    if not destinatario:
        raise EmailSendError("destinatario is required")

    if _resend_configured():
        return _send_via_resend(destinatario, assunto, corpo)
    return _send_via_smtp(destinatario, assunto, corpo)


def _send_via_resend(destinatario: str, assunto: str, corpo: str) -> dict[str, str]:
    api_key = os.environ["RESEND_BRM_API_KEY"]
    from_email = os.getenv("BRM_FROM_EMAIL", "noreply@brmsolutions.com.br")
    from_name = os.getenv("BRM_FROM_NAME", "BRM")

    try:
        response = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "from": f"{from_name} <{from_email}>",
                "to": [destinatario],
                "subject": assunto,
                "text": corpo,
            },
            timeout=15.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise EmailSendError(f"resend: {exc}") from exc

    body = response.json()
    return {
        "status": "sent",
        "provider": "resend",
        "to": destinatario,
        "subject": assunto,
        "id": str(body.get("id", "")),
    }


def _send_via_smtp(destinatario: str, assunto: str, corpo: str) -> dict[str, str]:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    sender = os.getenv("SMTP_SENDER", user)
    use_ssl = os.getenv("SMTP_SSL", "false").strip().lower() == "true"

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg.set_content(corpo)

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.starttls()
                smtp.login(user, password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailSendError(f"smtp: {exc}") from exc

    return {"status": "sent", "provider": "smtp", "to": destinatario, "subject": assunto}
