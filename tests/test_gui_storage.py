from __future__ import annotations

import json
import os
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ck3_gui import JapaneseModMaker


@unittest.skipUnless(os.name == "nt", "Windows desktop storage test")
class GuiStorageTests(unittest.TestCase):
    def test_settings_and_logs_never_store_api_key(self) -> None:
        try:
            window = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk desktop is unavailable: {exc}")
        window.withdraw()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                app = JapaneseModMaker(window, auto_connect=False)
                app.persistence_enabled = True
                app.settings_path = root / "settings.json"
                app.log_path = root / "logs" / "test.log"
                app.active_provider = "openai"
                app.models_by_provider["openai"] = "test-model"
                secret = "storage-test-secret-that-must-be-redacted"
                app.api_key_var.set(secret)
                app.source_var.set(str(root / "ExampleMod"))
                app.output_var.set(str(root / "ExampleMod_Japanese"))

                app.save_settings()
                app.append_log(f"simulated error accidentally contained {secret}")

                settings = json.loads(app.settings_path.read_text(encoding="utf-8"))
                self.assertEqual(settings["provider"], "openai")
                self.assertEqual(settings["last_source"], str(root / "ExampleMod"))
                self.assertNotIn("api_key", settings)
                stored = b"".join(path.read_bytes() for path in root.rglob("*") if path.is_file())
                self.assertNotIn(secret.encode("utf-8"), stored)
                self.assertIn("[APIキー非表示]", app.log_path.read_text(encoding="utf-8"))
        finally:
            window.destroy()


if __name__ == "__main__":
    unittest.main()
