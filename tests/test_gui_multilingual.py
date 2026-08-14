from __future__ import annotations

import os
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ck3_gui import AUTO_LANGUAGE_LABEL, CK3ModTranslator
from ck3_mod_scanner import scan_mod_folder


def create_mod(root: Path, folder: str, locale: str, value: str) -> Path:
    mod = root / folder
    localization = mod / "localization" / locale
    localization.mkdir(parents=True)
    (mod / "descriptor.mod").write_text(f'name="{folder}"\n', encoding="utf-8")
    (localization / f"text_l_{locale}.yml").write_text(
        f'l_{locale}:\n text_key:0 "{value}"\n',
        encoding="utf-8-sig",
    )
    return mod


@unittest.skipUnless(os.name == "nt", "Windows desktop GUI test")
class MultilingualGuiTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.window = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk desktop is unavailable: {exc}")
        self.window.withdraw()
        self.app = CK3ModTranslator(self.window, auto_connect=False)

    def tearDown(self) -> None:
        self.window.destroy()

    def test_english_language_controls_and_detected_source_column_exist(self) -> None:
        self.assertIn("CK3 Mod Translator", self.window.title())
        self.assertEqual(self.app.source_language_var.get(), AUTO_LANGUAGE_LABEL)
        self.assertEqual(self.app.target_language_var.get(), "Japanese")
        self.assertEqual(self.app.mod_tree.heading("language")["text"], "Detected source")
        self.assertEqual(self.app.start_button.cget("text"), "3. Translate Selected Mods")

    def test_actual_japanese_under_english_header_is_disabled_for_japanese_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mod = create_mod(
                Path(temporary),
                "JapaneseUnderEnglishHeader",
                "english",
                "これは日本語で書かれたキャラクターと王国の説明です。",
            )
            candidate = scan_mod_folder(mod)
            self.app.target_language_var.set("Japanese")
            self.app.source_language_var.set(AUTO_LANGUAGE_LABEL)

            eligible, source, detected, status = self.app.candidate_state(candidate)

            self.assertFalse(eligible)
            self.assertIsNotNone(source)
            self.assertEqual(source.detected_language_id, "japanese")
            self.assertEqual(detected, "Japanese")
            self.assertIn("Already Japanese", status)
            self.assertIn("l_english", status)

    def test_low_confidence_auto_requires_explicit_source_and_same_target_stays_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mod = create_mod(Path(temporary), "ShortFallback", "french", "Realm")
            candidate = scan_mod_folder(mod)
            self.app.target_language_var.set("Japanese")
            self.app.source_language_var.set(AUTO_LANGUAGE_LABEL)

            automatic = self.app.candidate_state(candidate)

            self.assertFalse(automatic[0])
            self.assertIn("choose Source language explicitly", automatic[3])

            self.app.source_language_var.set("English")
            explicit = self.app.candidate_state(candidate)
            self.assertTrue(explicit[0])
            self.assertEqual(explicit[1].locale_id, "french")
            self.assertIn("manually treated as English", explicit[3])

            self.app.source_language_var.set("Japanese")
            same_target = self.app.candidate_state(candidate)
            self.assertFalse(same_target[0])
            self.assertIn("Already Japanese", same_target[3])


if __name__ == "__main__":
    unittest.main()
