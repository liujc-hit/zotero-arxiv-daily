"""MIME assembly and SMTP delivery tests: real aiosmtpd round-trip, budgets, cleanup."""

import email.parser
import email.policy
import smtplib
import socket

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult

from zotero_arxiv_daily.email_message import (
    MAX_SERIALIZED_EMAIL_BYTES,
    EmailTooLargeError,
    build_digest_message,
)
from zotero_arxiv_daily.paper_presentation import InlineImage
from zotero_arxiv_daily.utils import send_email

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-payload"


def _free_port() -> int:
    """Bind an ephemeral port up-front; aiosmtpd 1.4.6 cannot probe port 0."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------------------
# local SMTP server (aiosmtpd) — no user credentials involved
# ---------------------------------------------------------------------------


class _Recorder:
    """aiosmtpd handler capturing envelopes; accepts any AUTH attempt."""

    def __init__(self):
        self.envelopes = []

    async def handle_DATA(self, server, session, envelope):
        self.envelopes.append((envelope.mail_from, envelope.rcpt_tos, envelope.content))
        return "250 Message accepted for delivery"

    @staticmethod
    def authenticator(server, session, envelope, mechanism, auth_data):
        return AuthResult(success=True, auth_data=auth_data)


@pytest.fixture()
def smtp_service():
    recorder = _Recorder()
    controller = Controller(
        recorder,
        hostname="127.0.0.1",
        port=_free_port(),
        authenticator=_Recorder.authenticator,
        auth_require_tls=False,
    )
    controller.start()
    try:
        yield recorder, controller.port
    finally:
        controller.stop()


def _delivery_config(config, port):
    config.email.smtp_server = "127.0.0.1"
    config.email.smtp_port = port
    return config


def _parse(raw: bytes):
    return email.parser.BytesParser(policy=email.policy.default).parsebytes(raw)


# ---------------------------------------------------------------------------
# MIME structure delivered over a real SMTP round-trip
# ---------------------------------------------------------------------------


def test_legacy_two_arg_send_email_delivers_html(smtp_service, config):
    recorder, port = smtp_service
    send_email(_delivery_config(config, port), "<html><body>legacy body 中文</body></html>")

    assert len(recorder.envelopes) == 1
    sender, recipients, raw = recorder.envelopes[0]
    assert sender == "test@example.com"
    assert recipients == ["test@example.com"]
    message = _parse(raw)
    assert message["Subject"].startswith("Daily arXiv")
    assert "test@example.com" in message["From"]
    assert "test@example.com" in message["To"]
    html_part = message.get_body(("html",))
    assert html_part is not None
    assert "legacy body 中文" in html_part.get_content()
    assert raw.decode("utf-8").isascii()


def test_send_email_delivers_alternative_with_plain_and_inline_image(smtp_service, config):
    recorder, port = smtp_service
    html = '<p>Digest</p><img src="cid:figure-0" alt="fig">'
    send_email(
        _delivery_config(config, port),
        html,
        inline_images=(InlineImage("figure-0", PNG_BYTES, "image/png"),),
        plain_text="Plain digest fallback",
    )

    message = _parse(recorder.envelopes[0][2])
    assert message.get_content_type() == "multipart/alternative"
    plain_part = message.get_body(("plain",))
    html_part = message.get_body(("html",))
    assert plain_part is not None and html_part is not None
    assert "Plain digest fallback" in plain_part.get_content()
    assert 'src="cid:figure-0"' in html_part.get_content()

    image_parts = [part for part in message.walk() if part.get_content_type() == "image/png"]
    assert len(image_parts) == 1
    image_part = image_parts[0]
    assert image_part.get("Content-ID") == "<figure-0>"
    assert image_part.get_content_disposition() == "inline"
    assert image_part.get_content() == PNG_BYTES


def test_every_cid_reference_has_matching_content_id_over_the_wire(smtp_service, config):
    recorder, port = smtp_service
    images = (
        InlineImage("figure-0", PNG_BYTES, "image/png"),
        InlineImage("figure-1", PNG_BYTES, "image/jpeg"),
    )
    html = '<img src="cid:figure-0"><img src="cid:figure-1">'
    send_email(_delivery_config(config, port), html, inline_images=images)
    message = _parse(recorder.envelopes[0][2])
    html_part = message.get_body(("html",))
    assert html_part is not None
    referenced = {part.split('"')[0] for part in html_part.get_content().split("cid:")[1:]}
    content_ids = set()
    for part in message.walk():
        if part.get_content_maintype() == "image":
            content_id = part.get("Content-ID")
            if content_id is not None:
                content_ids.add(content_id.strip("<>"))
    assert referenced == content_ids == {"figure-0", "figure-1"}


def test_image_payload_survives_round_trip_untranslated(smtp_service, config):
    recorder, port = smtp_service
    send_email(
        _delivery_config(config, port),
        '<img src="cid:figure-0">',
        inline_images=(InlineImage("figure-0", PNG_BYTES, "image/png"),),
    )
    message = _parse(recorder.envelopes[0][2])
    delivered = next(part.get_content() for part in message.walk() if part.get_content_maintype() == "image")
    assert delivered == PNG_BYTES


# ---------------------------------------------------------------------------
# serialized size budget: excess text fails clearly, nothing is sent
# ---------------------------------------------------------------------------


def test_oversized_text_raises_and_sends_nothing(smtp_service, config):
    recorder, port = smtp_service
    oversized_html = "<p>" + "x" * MAX_SERIALIZED_EMAIL_BYTES + "</p>"
    with pytest.raises(EmailTooLargeError, match="25"):
        send_email(_delivery_config(config, port), oversized_html)
    assert recorder.envelopes == []


def test_image_bearing_message_under_limit_is_sent(smtp_service, config):
    recorder, port = smtp_service
    send_email(
        _delivery_config(config, port),
        '<img src="cid:figure-0">',
        inline_images=(InlineImage("figure-0", PNG_BYTES, "image/png"),),
    )
    assert len(recorder.envelopes) == 1


# ---------------------------------------------------------------------------
# connection cleanup without swallowing the original exception
# ---------------------------------------------------------------------------


class _FailingLoginStub:
    """SMTP stub whose login always fails; records quit() calls."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.quit_called = False
        type(self).instances.append(self)

    def starttls(self):
        pass

    def login(self, user, password):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def sendmail(self, sender, recipients, msg):
        raise AssertionError("sendmail must not run when login fails")

    def quit(self):
        self.quit_called = True


def test_starttls_login_failure_cleans_up_and_propagates(config, monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _FailingLoginStub)
    with pytest.raises(smtplib.SMTPAuthenticationError):
        send_email(config, "<html>x</html>")
    assert _FailingLoginStub.instances[0].quit_called is True


def test_sendmail_failure_cleans_up_and_propagates(config, monkeypatch):
    class StubSendmailFails:
        instances = []

        def __init__(self, *args, **kwargs):
            self.quit_called = False
            type(self).instances.append(self)

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, sender, recipients, msg):
            raise smtplib.SMTPRecipientsRefused({"test@example.com": (550, b"no such user")})

        def quit(self):
            self.quit_called = True

    monkeypatch.setattr(smtplib, "SMTP", StubSendmailFails)
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        send_email(config, "<html>x</html>")
    assert StubSendmailFails.instances[0].quit_called is True


def test_port_465_login_failure_never_touches_plain_smtp_and_cleans_up(config, monkeypatch):
    config.email.smtp_port = 465
    connections = []
    created = []

    class StubSSLLoginFails:
        def __init__(self, host, port, *, timeout):
            connections.append((host, port, timeout))
            created.append(self)
            self.quit_called = False

        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

        def sendmail(self, sender, recipients, msg):
            raise AssertionError("sendmail must not run when login fails")

        def quit(self):
            self.quit_called = True

    def reject_plain(*args, **kwargs):
        raise AssertionError("Port 465 must never fall back to plain SMTP")

    monkeypatch.setattr(smtplib, "SMTP", reject_plain)
    monkeypatch.setattr(smtplib, "SMTP_SSL", StubSSLLoginFails)
    with pytest.raises(smtplib.SMTPAuthenticationError):
        send_email(config, "<html>x</html>")
    assert connections == [(config.email.smtp_server, 465, 30)]
    assert created[0].quit_called is True


# ---------------------------------------------------------------------------
# build_digest_message unit checks
# ---------------------------------------------------------------------------


def test_build_digest_message_without_plain_text_is_html_with_utf8(config):
    message = build_digest_message(config, "<p>only html</p>")
    # With no images and no plain text, a bare text/html part is the correct nesting.
    assert message.get_content_type() in ("text/html", "multipart/related")
    html_part = message.get_body(("html",))
    assert html_part is not None
    assert html_part.get_content_charset() == "utf-8"


def test_build_digest_message_attaches_images_inline_with_content_id(config):
    message = build_digest_message(
        config,
        '<img src="cid:figure-3">',
        inline_images=(InlineImage("figure-3", PNG_BYTES, "image/jpeg"),),
        plain_text="digest",
    )
    image_part = next(part for part in message.walk() if part.get_content_type() == "image/jpeg")
    assert image_part.get("Content-ID") == "<figure-3>"
    assert image_part.get_content_disposition() == "inline"
