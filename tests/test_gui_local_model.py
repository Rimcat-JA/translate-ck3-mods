from __future__ import annotations

import json
import os
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ck3_gui import DEFAULT_ENDPOINT, CK3ModTranslator, local_server_error_message


class LocalServerMessageTests(unittest.TestCase):
    def test_authentication_error_explains_optional_token(self) -> None:
        message = local_server_error_message("HTTP Error 401: Unauthorized")

        self.assertIn("API token", message)
        self.assertIn("optional", message)
        self.assertIn("Refresh Models", message)

    def test_connection_refusal_explains_how_to_start_lm_studio(self) -> None:
        message = local_server_error_message("<urlopen error [WinError 10061] connection refused>")

        self.assertIn("LM Studio", message)
        self.assertIn("load a model", message)
        self.assertIn("start the Local Server", message)

    def test_missing_model_list_endpoints_offer_manual_model_fallback(self) -> None:
        message = local_server_error_message(
            "HTTP Error 404: Not Found",
            "http://127.0.0.1:1234/v1/chat/completions",
        )

        self.assertIn("server was reached", message)
        self.assertIn("/api/v1/models", message)
        self.assertIn("/v1/models", message)
        self.assertIn("/api/v0/models", message)
        self.assertIn("server version", message)
        self.assertIn("exact model ID manually", message)


@unittest.skipUnless(os.name == "nt", "Windows desktop GUI test")
class LocalModelGuiTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.window = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk desktop is unavailable: {exc}")
        self.window.withdraw()
        self.app = CK3ModTranslator(self.window, auto_connect=False)

    def tearDown(self) -> None:
        self.window.destroy()

    def test_local_model_is_editable_and_discovery_preserves_manual_value(self) -> None:
        self.assertEqual(str(self.app.model_combo.cget("state")), "normal")
        self.app.model_var.set("publisher/manual-model")

        self.app.handle_event(
            {
                "event": "models",
                "provider": "local",
                "models": ["loaded/model-a", "loaded/model-b"],
            }
        )

        self.assertEqual(self.app.selected_model(), "publisher/manual-model")
        self.assertEqual(
            tuple(self.app.model_combo.cget("values")),
            ("publisher/manual-model", "loaded/model-a", "loaded/model-b"),
        )
        self.assertTrue(self.app.models_verified)

        self.app.handle_event({"event": "models", "provider": "local", "models": []})
        self.assertEqual(self.app.selected_model(), "publisher/manual-model")
        self.assertIn("type the exact model ID", self.app.server_var.get())

    def test_local_optional_token_is_visible_and_never_written_to_settings(self) -> None:
        self.assertEqual(str(self.app.api_key_entry.winfo_manager()), "grid")
        self.assertIn("optional", str(self.app.api_key_label.cget("text")).casefold())
        self.assertIn("token", str(self.app.remember_key_check.cget("text")).casefold())

        with tempfile.TemporaryDirectory() as temporary:
            secret = "local-server-secret-that-must-not-leak"
            self.app.persistence_enabled = True
            self.app.settings_path = Path(temporary) / "settings.json"
            self.app.api_key_var.set(secret)
            self.app.api_keys_by_provider["local"] = secret

            self.app.save_settings()

            raw = self.app.settings_path.read_text(encoding="utf-8")
            settings = json.loads(raw)
            self.assertNotIn(secret, raw)
            self.assertNotIn("api_key", settings)

    def test_invalid_local_endpoint_credentials_are_sanitized_from_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings_path = Path(temporary) / "settings.json"
            unsafe_endpoint = "http://gui-user:gui-pass@127.0.0.1:1234/v1/chat/completions?token=gui-secret#part"
            self.app.persistence_enabled = True
            self.app.settings_path = settings_path
            self.app.endpoint_var.set(unsafe_endpoint)

            self.app.save_settings()

            raw = settings_path.read_text(encoding="utf-8")
            settings = json.loads(raw)
            self.assertEqual(settings["local_endpoint"], DEFAULT_ENDPOINT)
            for secret in ("gui-user", "gui-pass", "gui-secret"):
                self.assertNotIn(secret, raw)

    def test_invalid_local_endpoint_stops_before_settings_credentials_or_clone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            output = temporary_root / "Demo_Japanese"
            candidate = SimpleNamespace(name="Demo", root=temporary_root / "Demo")
            source = SimpleNamespace(locale_id="english")
            self.app.selected_jobs = lambda: [(candidate, source, output, "English")]  # type: ignore[method-assign]
            self.app.persistence_enabled = True
            self.app.settings_path = temporary_root / "settings.json"
            self.app.endpoint_var.set(
                "http://start-user:start-pass@127.0.0.1:1234/v1/chat/completions?token=start-secret"
            )
            self.app.model_var.set("manual/local-model")
            self.app.api_key_var.set("separate-local-token")

            with (
                mock.patch("ck3_gui._clone_function") as clone_function,
                mock.patch("ck3_gui.save_api_key") as save_key,
                mock.patch("ck3_gui.delete_api_key") as delete_key,
                mock.patch("ck3_gui.messagebox.showerror") as show_error,
            ):
                self.app.start()

            self.assertFalse(self.app.running)
            self.assertIsNone(self.app.worker)
            self.assertFalse(self.app.settings_path.exists())
            clone_function.assert_not_called()
            save_key.assert_not_called()
            delete_key.assert_not_called()
            error_text = str(show_error.call_args.args[1])
            self.assertIn("No settings or credentials were saved", error_text)
            self.assertNotIn("start-user", error_text)
            self.assertNotIn("start-pass", error_text)
            self.assertNotIn("start-secret", error_text)

    def test_unverified_manual_model_and_optional_token_are_passed_to_clone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            output = temporary_root / "Demo_Japanese"
            candidate = SimpleNamespace(name="Demo", root=temporary_root / "Demo")
            source = SimpleNamespace(locale_id="english")
            self.app.selected_jobs = lambda: [(candidate, source, output, "English")]  # type: ignore[method-assign]
            self.app.model_var.set("manual/local-model")
            self.app.api_key_var.set("optional-local-token")
            self.app.models_verified = False
            self.app.persistence_enabled = True
            self.app.settings_path = temporary_root / "settings.json"
            captured: list[object] = []

            def fake_clone(options: object, *_args: object) -> object:
                captured.append(options)
                return SimpleNamespace(output=output)

            with (
                mock.patch("ck3_gui._clone_function", return_value=fake_clone),
                mock.patch("ck3_gui.save_api_key") as save_key,
            ):
                self.app.start()
                self.assertIsNotNone(self.app.worker)
                self.app.worker.join(timeout=5)

            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].model, "manual/local-model")
            self.assertEqual(captured[0].api_key, "optional-local-token")
            save_key.assert_called_once_with("local", "optional-local-token")

    def test_local_connection_error_keeps_manual_model_and_shows_help(self) -> None:
        self.app.model_var.set("manual/local-model")

        self.app.handle_event(
            {
                "event": "models_error",
                "provider": "local",
                "message": "<urlopen error [WinError 10061]>",
            }
        )

        self.assertEqual(self.app.selected_model(), "manual/local-model")
        self.assertIn("LM Studio", self.app.server_var.get())
        self.assertIn("start the Local Server", self.app.server_var.get())
        self.assertIn("Advanced Settings", str(self.app.provider_hint.cget("text")))


if __name__ == "__main__":
    unittest.main()
