from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ck3_clone import (
    CloneOptions,
    create_localized_clone,
    discover_models,
    model_discovery_endpoints,
)
from ck3_providers import validate_endpoint


class LocalModelHandler(BaseHTTPRequestHandler):
    openai_status = 200
    openai_payload: ClassVar[object] = {"data": []}
    native_status = 200
    native_payload: ClassVar[object] = {"models": []}
    legacy_status = 200
    legacy_payload: ClassVar[object] = {"data": []}
    get_requests: ClassVar[list[tuple[str, str | None]]] = []
    post_requests: ClassVar[list[tuple[str, str | None, dict[str, object]]]] = []

    @classmethod
    def reset(cls) -> None:
        cls.openai_status = 200
        cls.openai_payload = {"data": []}
        cls.native_status = 200
        cls.native_payload = {"models": []}
        cls.legacy_status = 200
        cls.legacy_payload = {"data": []}
        cls.get_requests = []
        cls.post_requests = []

    def send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        type(self).get_requests.append((self.path, self.headers.get("Authorization")))
        if self.path == "/v1/models":
            self.send_json(type(self).openai_status, type(self).openai_payload)
        elif self.path == "/api/v1/models":
            self.send_json(type(self).native_status, type(self).native_payload)
        elif self.path == "/api/v0/models":
            self.send_json(type(self).legacy_status, type(self).legacy_payload)
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).post_requests.append(
            (self.path, self.headers.get("Authorization"), payload)
        )
        items = json.loads(payload["messages"][-1]["content"])["items"]
        translations = {
            item_id: "ローカルモデルで完全に翻訳された文章です。"
            + "".join(re.findall(r"__CK3TOKEN_\d+__", value))
            for item_id, value in items.items()
        }
        self.send_json(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"translations": translations}, ensure_ascii=False
                            )
                        }
                    }
                ]
            },
        )

    def log_message(self, _format: str, *_args: object) -> None:
        return


def create_source(root: Path, name: str) -> Path:
    source = root / name
    localization = source / "localization" / "english"
    localization.mkdir(parents=True)
    (localization / "source_l_english.yml").write_text(
        'l_english:\n source_text:0 "A complete sentence to translate."\n',
        encoding="utf-8",
    )
    (source / "descriptor.mod").write_text(
        f'name="{name}"\n', encoding="utf-8"
    )
    return source


class LocalModelDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        LocalModelHandler.reset()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), LocalModelHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions"

    def test_discovers_openai_compatible_data_ids(self) -> None:
        LocalModelHandler.native_status = 404
        LocalModelHandler.openai_payload = {
            "data": [
                {"id": "loaded/model-a"},
                {"id": "loaded/model-b"},
                {"id": "loaded/model-a"},
            ]
        }

        self.assertEqual(
            discover_models(self.endpoint), ["loaded/model-a", "loaded/model-b"]
        )
        self.assertEqual(
            [path for path, _auth in LocalModelHandler.get_requests],
            ["/api/v1/models", "/v1/models"],
        )

    def test_model_endpoint_derivation_preserves_custom_port_and_ipv6(self) -> None:
        self.assertEqual(
            model_discovery_endpoints(
                "http://localhost:9876/custom/chat/completions"
            ),
            (
                "http://localhost:9876/api/v1/models",
                "http://localhost:9876/v1/models",
                "http://localhost:9876/api/v0/models",
            ),
        )
        self.assertEqual(
            model_discovery_endpoints(
                "http://[::1]:4321/v1/chat/completions"
            ),
            (
                "http://[::1]:4321/api/v1/models",
                "http://[::1]:4321/v1/models",
                "http://[::1]:4321/api/v0/models",
            ),
        )

    def test_local_endpoint_rejects_credentials_query_and_fragment_without_echo(self) -> None:
        rejected = (
            (
                "credentials",
                "http://user:password@127.0.0.1:1234/v1/chat/completions",
            ),
            (
                "query",
                "http://127.0.0.1:1234/v1/chat/completions?token=sensitive-query",
            ),
            (
                "fragment",
                "http://127.0.0.1:1234/v1/chat/completions#sensitive-fragment",
            ),
        )
        for endpoint_type, endpoint in rejected:
            with self.subTest(endpoint_type=endpoint_type):
                with self.assertRaises(ValueError) as raised:
                    validate_endpoint("local", endpoint)
                self.assertNotIn("password", str(raised.exception))
                self.assertNotIn("sensitive", str(raised.exception))

    def test_falls_back_to_lm_studio_native_keys_and_ids(self) -> None:
        LocalModelHandler.native_payload = {
            "models": [
                {"type": "embedding", "key": "not-a-translation-model"},
                {"type": "llm", "key": "publisher/jit-model"},
                {"type": "llm", "id": "legacy-native-id"},
                {"type": "llm", "key": "publisher/jit-model"},
            ]
        }

        self.assertEqual(
            discover_models(self.endpoint),
            ["publisher/jit-model", "legacy-native-id"],
        )
        self.assertEqual(
            [path for path, _auth in LocalModelHandler.get_requests],
            ["/api/v1/models"],
        )

    def test_falls_back_to_lm_studio_legacy_native_v0(self) -> None:
        LocalModelHandler.openai_status = 404
        LocalModelHandler.native_status = 404
        LocalModelHandler.legacy_payload = {
            "data": [
                {"type": "embeddings", "id": "not-a-chat-model"},
                {"type": "vlm", "id": "legacy-vlm"},
                {"type": "llm", "id": "legacy-llm"},
            ]
        }

        self.assertEqual(
            discover_models(self.endpoint),
            ["legacy-vlm", "legacy-llm"],
        )
        self.assertEqual(
            [path for path, _auth in LocalModelHandler.get_requests],
            ["/api/v1/models", "/v1/models", "/api/v0/models"],
        )

    def test_manual_model_bypasses_unavailable_or_incomplete_lists_for_jit_load(self) -> None:
        manual_model = "publisher/downloaded-jit-model"
        scenarios = (
            ("unavailable", 503, {"error": "model listing disabled"}),
            ("not-listed", 200, {"data": [{"id": "another-loaded-model"}]}),
        )
        for label, status, payload in scenarios:
            with self.subTest(model_list=label):
                LocalModelHandler.reset()
                LocalModelHandler.openai_status = status
                LocalModelHandler.openai_payload = payload
                source = create_source(self.root, f"ManualModelSource-{label}")
                result = create_localized_clone(
                    CloneOptions(
                        source=source,
                        output=self.root / f"ManualModelOutput-{label}",
                        endpoint=self.endpoint,
                        model=manual_model,
                        workers=1,
                        work_root=self.root / f"manual-work-{label}",
                    )
                )

                self.assertEqual(result.model, manual_model)
                self.assertEqual(LocalModelHandler.get_requests, [])
                self.assertTrue(LocalModelHandler.post_requests)
                self.assertTrue(
                    all(
                        request[2]["model"] == manual_model
                        for request in LocalModelHandler.post_requests
                    )
                )

    def test_empty_discovery_explains_manual_model_and_jit_options(self) -> None:
        source = create_source(self.root, "NoDiscoveredModelSource")

        with self.assertRaises(RuntimeError) as raised:
            create_localized_clone(
                CloneOptions(
                    source=source,
                    output=self.root / "NoDiscoveredModelOutput",
                    endpoint=self.endpoint,
                    workers=1,
                    work_root=self.root / "no-model-work",
                )
            )

        message = str(raised.exception)
        self.assertIn("exact model ID", message)
        self.assertIn("JIT", message)
        self.assertEqual(LocalModelHandler.post_requests, [])

    def test_optional_local_token_is_sent_to_discovery_and_translation(self) -> None:
        token = "local-test-token"
        model = "authenticated-local-model"
        LocalModelHandler.native_status = 404
        LocalModelHandler.openai_payload = {"data": [{"id": model}]}

        self.assertEqual(
            discover_models(self.endpoint, api_key=token),
            [model],
        )
        source = create_source(self.root, "AuthenticatedSource")
        result = create_localized_clone(
            CloneOptions(
                source=source,
                output=self.root / "AuthenticatedOutput",
                endpoint=self.endpoint,
                api_key=token,
                model=model,
                workers=1,
                work_root=self.root / "authenticated-work",
            )
        )

        self.assertEqual(result.model, model)
        expected_header = f"Bearer {token}"
        self.assertEqual(
            LocalModelHandler.get_requests,
            [
                ("/api/v1/models", expected_header),
                ("/v1/models", expected_header),
            ],
        )
        self.assertTrue(LocalModelHandler.post_requests)
        self.assertTrue(
            all(auth == expected_header for _path, auth, _payload in LocalModelHandler.post_requests)
        )
        token_bytes = token.encode("utf-8")
        self.assertFalse(
            any(
                token_bytes in path.read_bytes()
                for path in self.root.rglob("*")
                if path.is_file()
            )
        )

    def test_authentication_errors_are_not_masked_by_native_fallbacks(self) -> None:
        for status in (401, 403):
            with self.subTest(status=status):
                LocalModelHandler.reset()
                LocalModelHandler.native_status = status

                with self.assertRaises(urllib.error.HTTPError) as raised:
                    discover_models(self.endpoint)

                self.assertEqual(raised.exception.code, status)
                self.assertEqual(
                    [path for path, _auth in LocalModelHandler.get_requests],
                    ["/api/v1/models"],
                )


if __name__ == "__main__":
    unittest.main()
