"""MIME assembly for digest emails.

Builds the sendable message: multipart/alternative (plain text + HTML) with
inline figures attached as multipart/related image parts carrying matching
Content-IDs. Serialization is UTF-8-safe 7-bit transport and is refused with
a clear error when it exceeds MAX_SERIALIZED_EMAIL_BYTES, so an oversized
digest fails instead of being silently claimed as sent.
"""

import datetime
from email.header import Header
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from omegaconf import DictConfig

from .paper_presentation import InlineImage

__all__ = ("MAX_SERIALIZED_EMAIL_BYTES", "EmailTooLargeError", "build_digest_message")

MAX_SERIALIZED_EMAIL_BYTES = 25 * 1024 * 1024


class EmailTooLargeError(RuntimeError):
    """Raised when the serialized email exceeds MAX_SERIALIZED_EMAIL_BYTES."""


def _format_addr(s: str) -> str:
    name, addr = parseaddr(s)
    return formataddr((Header(name, "utf-8").encode(), addr))


def build_digest_message(
    config: DictConfig,
    html: str,
    inline_images: tuple[InlineImage, ...] = (),
    plain_text: str = "",
) -> EmailMessage:
    """Assemble the digest email with headers, optional plain part and inline images."""
    msg = EmailMessage()
    msg["From"] = _format_addr("Github Action <%s>" % config.email.sender)
    msg["To"] = _format_addr("You <%s>" % config.email.receiver)
    today = datetime.datetime.now().strftime("%Y/%m/%d")
    msg["Subject"] = f"Daily arXiv {today}"

    if plain_text:
        msg.set_content(plain_text, charset="utf-8")
        msg.add_alternative(html, subtype="html", charset="utf-8")
    else:
        msg.set_content(html, subtype="html", charset="utf-8")

    if inline_images:
        html_part = next(part for part in msg.walk() if part.get_content_type() == "text/html")
        for image in inline_images:
            maintype, _, subtype = image.media_type.partition("/")
            html_part.add_related(
                image.data,
                maintype=maintype,
                subtype=subtype,
                cid=f"<{image.cid}>",
                disposition="inline",
            )

    serialized = msg.as_string()
    if len(serialized) > MAX_SERIALIZED_EMAIL_BYTES:
        raise EmailTooLargeError(
            f"Serialized email is {len(serialized)} bytes, exceeding the "
            f"{MAX_SERIALIZED_EMAIL_BYTES}-byte (25 MiB) limit; not sending."
        )
    return msg
