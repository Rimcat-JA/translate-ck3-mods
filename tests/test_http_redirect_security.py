from __future__ import annotations

import argparse
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ck3_clone import discover_models
from ck3_http import RedirectRejectedError
from ck3_localize import Translator


class RedirectTargetHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[tuple[str, str, str | None]]] = []

    def record(self) -> None:
        type(self).requests.append(
            (self.command, self.path, self.headers.get("Authorization"))
        )
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = record
    do_POST = record

    def log_message(self, _format: str, *_args: object) -> None:
        return


class RedirectOriginHandler(BaseHTTPRequestHandler):
    target_url = ""
    requests: ClassVar[list[tuple[str, str, str | None]]] = []

    def redirect(self) -> None:
        type(self).requests.append(
            (self.command, self.path, self.headers.get("Authorization"))
        )
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        self.send_response(302)
        self.send_header("Location", f"{type(self).target_url}/credential-capture")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = redirect
    do_POST = redirect

    def log_message(self, _format: str, *_args: object) -> None:
        return


class RedirectSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        RedirectTargetHandler.requests = []
        RedirectOriginHandler.requests = []
        self.target = ThreadingHTTPServer(("127.0.0.1", 0), RedirectTargetHandler)
        self.target_thread = threading.Thread(
            target=self.target.serve_forever, daemon=True
        )
        self.target_thread.start()
        RedirectOriginHandler.target_url = (
            f"http://127.0.0.1:{self.target.server_port}"
        )
        self.origin = ThreadingHTTPServer(("127.0.0.1", 0), RedirectOriginHandler)
        self.origin_thread = threading.Thread(
            target=self.origin.serve_forever, daemon=True
        )
        self.origin_thread.start()

    def tearDown(self) -> None:
        for server, thread in (
            (self.origin, self.origin_thread),
            (self.target, self.target_thread),
        ):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.origin.server_port}/v1/chat/completions"

    def test_model_discovery_rejects_redirect_without_forwarding_token(self) -> None:
        token = "dummy-local-token"

        with self.assertRaises(RedirectRejectedError) as raised:
            discover_models(self.endpoint, api_key=token)

        self.assertNotIn(token, str(raised.exception))
        self.assertEqual(
            RedirectOriginHandler.requests,
            [("GET", "/api/v1/models", f"Bearer {token}")],
        )
        self.assertEqual(RedirectTargetHandler.requests, [])

    def test_chat_request_rejects_redirect_without_forwarding_token(self) -> None:
        token = "dummy-local-token"
        translator = Translator(
            argparse.Namespace(
                provider="local",
                endpoint=self.endpoint,
                api_key=token,
                api_key_env=None,
                glossary=None,
                extra_instructions=None,
                source_language="English",
                language="Japanese",
                min_interval=0.0,
                max_tokens=2000,
                model="manual-local-model",
                temperature=0.2,
                retries=4,
                timeout=5,
                cancel_event=None,
            ),
            [],
        )

        with self.assertRaises(RedirectRejectedError) as raised:
            translator.request_masked({"item-1": "Translate this sentence."})

        self.assertNotIn(token, str(raised.exception))
        self.assertEqual(
            RedirectOriginHandler.requests,
            [("POST", "/v1/chat/completions", f"Bearer {token}")],
        )
        self.assertEqual(RedirectTargetHandler.requests, [])


if __name__ == "__main__":
    unittest.main()
