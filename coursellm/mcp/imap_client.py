from __future__ import annotations

import email
import imaplib
import logging
from datetime import datetime
from email.header import decode_header
from typing import List

from core.config import settings

logger = logging.getLogger(__name__)


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        chunks: list[str] = []
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                chunks.append(payload.decode(charset, errors="replace"))
        return "\n".join(chunks)
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def fetch_recent_messages(limit: int = 50) -> List[dict]:
    """
    Fetch recent inbox messages via IMAP.
    Returns empty list on missing config or connection errors.
    """
    if settings.email_use_mock:
        logger.info("IMAP fetch skipped: email credentials not configured (mock mode)")
        return []

    host = settings.EMAIL_IMAP_HOST
    address = settings.EMAIL_ADDRESS
    password = settings.EMAIL_PASSWORD
    if not host or not address or not password:
        return []

    messages: list[dict] = []
    mail: imaplib.IMAP4_SSL | None = None
    try:
        mail = imaplib.IMAP4_SSL(host, settings.EMAIL_IMAP_PORT)
        mail.login(address, password)
        mail.select("INBOX")

        status, data = mail.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []

        ids = data[0].split()
        for msg_id in reversed(ids[-limit:]):
            status, msg_data = mail.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            subject = _decode_header_value(msg.get("Subject"))
            from_address = _decode_header_value(msg.get("From"))
            message_id = msg.get("Message-ID") or msg_id.decode()
            date_header = msg.get("Date")
            received_at = datetime.utcnow()
            if date_header:
                try:
                    received_at = email.utils.parsedate_to_datetime(date_header)
                    if received_at.tzinfo:
                        received_at = received_at.replace(tzinfo=None)
                except (TypeError, ValueError):
                    pass

            messages.append(
                {
                    "id": message_id,
                    "from_address": from_address,
                    "subject": subject,
                    "body": _extract_body(msg),
                    "received_at": received_at,
                }
            )
    except Exception as exc:
        logger.error("IMAP fetch failed: %s", exc)
        return []
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass

    return messages
