from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ck3_clone import (
    CancelledError,
    CloneOptions,
    backup_existing,
    create_japanese_clone,
    finalize_clone,
)
from ck3_localize import valid_translation
from ck3_providers import PROVIDERS, validate_endpoint
from windows_credentials import delete_api_key, load_api_key, save_api_key


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> FakeResponse:  # noqa: PYI034 - keep compatibility with Python 3.10 without typing.Self
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def create_source(root: Path) -> Path:
    source = root / "ProviderSource"
    localization = source / "localization" / "english"
    localization.mkdir(parents=True)
    (localization / "provider_l_english.yml").write_text(
        'l_english:\n provider_title:0 "Provider translation [Character.GetName]"\n', encoding="utf-8"
    )
    (source / "descriptor.mod").write_text('name="Provider Source"\n', encoding="utf-8")
    return source


class ProviderTests(unittest.TestCase):
    def test_cancelled_translation_sends_no_new_remote_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = create_source(root)
            cancelled = threading.Event()
            cancelled.set()
            requests = 0

            def unexpected_request(_request: object, timeout: int = 0) -> FakeResponse:
                nonlocal requests
                del timeout
                requests += 1
                return FakeResponse({})

            provider = PROVIDERS["openai"]
            with (
                patch("urllib.request.urlopen", side_effect=unexpected_request),
                self.assertRaises(CancelledError),
            ):
                create_japanese_clone(
                    CloneOptions(
                        source=source,
                        output=root / "cancelled-output",
                        endpoint=provider.chat_endpoint,
                        provider="openai",
                        api_key="cancellation-test-secret",
                        model="test/translation-model",
                        workers=4,
                        work_root=root / "work",
                    ),
                    cancel_event=cancelled,
                )
            self.assertEqual(requests, 0)
            self.assertFalse((root / "cancelled-output").exists())

    def test_final_install_failure_restores_previous_clone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "Existing_Japanese"
            output.mkdir()
            (output / "old.txt").write_text("old clone", encoding="utf-8")
            launcher = root / "Existing_Japanese.mod"
            launcher.write_text("old launcher", encoding="utf-8")
            backup = backup_existing(output, launcher)
            self.assertIsNotNone(backup)

            clone = root / "verified-clone"
            clone.mkdir()
            (clone / "new.txt").write_text("new clone", encoding="utf-8")
            launcher_temp = root / "verified-launcher.mod"
            launcher_temp.write_text("new launcher", encoding="utf-8")
            real_replace = os.replace
            calls = 0

            def fail_second_replace(source: object, destination: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated launcher install failure")
                real_replace(source, destination)

            with (
                patch("ck3_clone.os.replace", side_effect=fail_second_replace),
                self.assertRaisesRegex(OSError, "simulated launcher"),
            ):
                finalize_clone(clone, launcher_temp, output, launcher, backup)

            self.assertEqual((output / "old.txt").read_text(encoding="utf-8"), "old clone")
            self.assertEqual(launcher.read_text(encoding="utf-8"), "old launcher")
            self.assertFalse((output / "new.txt").exists())

    def test_japanese_validation_rejects_source_text_with_a_prefix(self) -> None:
        source = "This complete English sentence must be translated."
        self.assertFalse(valid_translation(source, "訳：" + source, "Japanese"))
        self.assertTrue(valid_translation(source, "この英文はすべて日本語へ翻訳されています。", "Japanese"))

    def test_remote_providers_translate_only_through_allowlisted_endpoint(self) -> None:
        secret = "provider-test-secret-that-must-never-be-written"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = create_source(root)
            for provider_id in ("openai", "openrouter", "nanogpt", "nanogpt_subscription"):
                with self.subTest(provider=provider_id):
                    requests: list[tuple[str, dict[str, object], str | None]] = []

                    def fake_urlopen(
                        request: object, timeout: int = 0, request_log: list[tuple[str, dict[str, object], str | None]] = requests
                    ) -> FakeResponse:
                        del timeout
                        url = request.full_url
                        payload = json.loads(request.data.decode("utf-8"))
                        authorization = request.get_header("Authorization")
                        request_log.append((url, payload, authorization))
                        items = json.loads(payload["messages"][-1]["content"])["items"]
                        translated = {
                            item_id: "完全に翻訳された日本語文章" + "".join(re.findall(r"__CK3TOKEN_\d+__", text))
                            for item_id, text in items.items()
                        }
                        return FakeResponse({"choices": [{"message": {"content": json.dumps({"translations": translated}, ensure_ascii=False)}}]})

                    provider = PROVIDERS[provider_id]
                    output = root / f"output-{provider_id}"
                    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                        result = create_japanese_clone(
                            CloneOptions(
                                source=source,
                                output=output,
                                endpoint=provider.chat_endpoint,
                                provider=provider_id,
                                api_key=secret,
                                model="test/translation-model",
                                workers=1,
                                work_root=root / "work",
                            )
                        )
                    self.assertTrue(requests)
                    self.assertTrue(all(url == provider.chat_endpoint for url, _payload, _auth in requests))
                    self.assertTrue(all(auth == f"Bearer {secret}" for _url, _payload, auth in requests))
                    if provider_id == "openai":
                        self.assertIn("max_completion_tokens", requests[0][1])
                    else:
                        self.assertIn("max_tokens", requests[0][1])
                    manifest = json.loads((result.output / "japanese-clone-manifest.json").read_text(encoding="utf-8"))
                    self.assertEqual(manifest["translation_engine"], provider_id)
                    translated_file = next((result.output / "localization" / "japanese").glob("*.yml"))
                    translated_text = translated_file.read_text(encoding="utf-8-sig")
                    self.assertIn("完全に翻訳された日本語文章", translated_text)
                    self.assertNotIn("Provider translation", translated_text)

            secret_bytes = secret.encode("utf-8")
            for path in (item for item in root.rglob("*") if item.is_file()):
                self.assertNotIn(secret_bytes, path.read_bytes(), f"API key leaked into {path}")

    def test_remote_provider_rejects_nonofficial_endpoint(self) -> None:
        for provider_id in ("openai", "openrouter", "nanogpt", "nanogpt_subscription"):
            with self.subTest(provider=provider_id):
                with self.assertRaises(ValueError):
                    validate_endpoint(provider_id, "https://example.invalid/v1/chat/completions")
                with self.assertRaises(ValueError):
                    validate_endpoint(provider_id, PROVIDERS[provider_id].chat_endpoint + "?redirect=evil")

    @unittest.skipUnless(os.name == "nt", "Windows Credential Manager test")
    def test_windows_credential_manager_round_trip(self) -> None:
        target = f"automated_test_{uuid.uuid4().hex}"
        value = f"secret-{uuid.uuid4().hex}"
        try:
            save_api_key(target, value)
            self.assertEqual(load_api_key(target), value)
            self.assertTrue(delete_api_key(target))
            self.assertIsNone(load_api_key(target))
        finally:
            delete_api_key(target)


if __name__ == "__main__":
    unittest.main()
