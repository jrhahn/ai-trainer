"""A local mail server for the stubbed e2e stack (ai-trainer-ops#35).

The password reset sends its link by mail. The stubbed stack points the
backend here instead of at a provider, so a spec can read the mail and follow
the link: the whole path from "Forgot your password?" to signing in with the
new password, on every PR.

It speaks STARTTLS with a certificate from a throwaway CA, because the app's
mailer insists on encryption and the test must not need a switch that turns it
off. The CA goes into a bundle next to certifi's roots, and the backend trusts
it through SSL_CERT_FILE, so nothing in the app changes for the test.

    cd backend && uv run python ../frontend/e2e/mail_catcher.py \\
        --dir /abs/path --smtp-port 18725 --http-port 18726

``GET /messages`` answers the mails received so far as JSON, and
``GET /healthz`` is the stack's readiness check.
"""

from __future__ import annotations

import argparse
import json
import ssl
from email import message_from_bytes, policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import certifi
import trustme
from aiosmtpd.controller import Controller

MESSAGES: list[dict] = []


class Keep:
    async def handle_DATA(self, server, session, envelope):  # noqa: N802 — aiosmtpd's name
        message = message_from_bytes(envelope.content, policy=policy.default)
        body = message.get_body(preferencelist=("plain",))
        MESSAGES.append(
            {
                "to": list(envelope.rcpt_tos),
                "subject": str(message["Subject"]),
                "text": body.get_content() if body is not None else "",
            }
        )
        return "250 Message accepted"


class Api(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — http.server's name
        if self.path == "/healthz":
            payload: object = {"ok": True}
        elif self.path == "/messages":
            payload = MESSAGES
        else:
            self.send_error(404)
            return
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # quiet: the stack's log is the backend's
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True, help="where the CA bundle is written")
    parser.add_argument("--smtp-port", type=int, required=True)
    parser.add_argument("--http-port", type=int, required=True)
    args = parser.parse_args()

    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    ca = trustme.CA()
    server_cert = ca.issue_cert("127.0.0.1", "localhost")
    # certifi's roots as well, so the backend's other HTTPS still verifies.
    (out / "ca-bundle.pem").write_text(
        Path(certifi.where()).read_text() + ca.cert_pem.bytes().decode()
    )
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_cert.configure_cert(context)

    smtp = Controller(
        Keep(),
        hostname="127.0.0.1",
        port=args.smtp_port,
        tls_context=context,
        require_starttls=True,
    )
    smtp.start()
    try:
        ThreadingHTTPServer(("127.0.0.1", args.http_port), Api).serve_forever()
    finally:
        smtp.stop()


if __name__ == "__main__":
    main()
