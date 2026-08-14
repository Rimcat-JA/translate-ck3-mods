from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FROZEN_APP = ROOT / "dist" / "CK3_Mod_Translator.exe"
sys.path.insert(0, str(SCRIPTS))

from ck3_clone import CloneOptions, create_localized_clone
from ck3_localize import parse_mod, valid_translation


class EnglishTranslationHandler(BaseHTTPRequestHandler):
    calls = 0
    prompts: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        body = json.dumps({"data": [{"id": "multilingual-test-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        type(self).calls += 1
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).prompts.append(payload["messages"][0]["content"])
        items = json.loads(payload["messages"][-1]["content"])["items"]
        translations = {
            item_id: "A complete natural English translation "
            + "".join(re.findall(r"__CK3TOKEN_\d+__", value))
            for item_id, value in items.items()
        }
        body = json.dumps(
            {"choices": [{"message": {"content": json.dumps({"translations": translations})}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def write_locale(path: Path, header: str, key: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{header}:\n {key}:0 "{value}"\n', encoding="utf-8")


class MultilingualEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        EnglishTranslationHandler.calls = 0
        EnglishTranslationHandler.prompts = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), EnglishTranslationHandler)
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

    def test_french_replace_layout_to_english_preserves_other_languages(self) -> None:
        source = self.root / "FrenchMod"
        write_locale(
            source / "localization" / "french" / "direct_l_french.yml",
            "l_french",
            "direct_key",
            "Une phrase française [Character.GetName]",
        )
        write_locale(
            source / "localization" / "replace" / "french" / "replace_l_french.yml",
            "l_french",
            "replace_key",
            "Une deuxième phrase française",
        )
        write_locale(
            source / "localization" / "english" / "obsolete_l_english.yml",
            "l_english",
            "obsolete",
            "Old target text",
        )
        spanish = source / "localization" / "spanish" / "keep_l_spanish.yml"
        write_locale(spanish, "l_spanish", "keep", "Texto que debe conservarse")
        (source / "common").mkdir()
        binary = source / "common" / "data.bin"
        binary.write_bytes(bytes(range(48)))
        (source / "descriptor.mod").write_text('name="French Test Mod"\n', encoding="utf-8")

        spec = parse_mod(str(source), "l_french", "French")
        self.assertEqual(len(spec.source_files), 2)
        output = self.root / "FrenchMod_English"
        options = CloneOptions(
            source=source,
            output=output,
            endpoint=self.endpoint,
            model="multilingual-test-model",
            workers=1,
            work_root=self.root / "work",
            source_language="French",
            source_locale="l_french",
            language="English",
            locale="l_english",
        )
        result = create_localized_clone(options)

        direct = output / "localization" / "english" / "direct_l_english.yml"
        replace = output / "localization" / "replace" / "english" / "replace_l_english.yml"
        self.assertTrue(direct.is_file())
        self.assertTrue(replace.is_file())
        self.assertIn("l_english:", direct.read_text(encoding="utf-8-sig"))
        self.assertIn("[Character.GetName]", direct.read_text(encoding="utf-8-sig"))
        self.assertFalse((output / "localization" / "french" / "direct_l_french.yml").exists())
        self.assertFalse((output / "localization" / "english" / "obsolete_l_english.yml").exists())
        self.assertEqual((output / spanish.relative_to(source)).read_bytes(), spanish.read_bytes())
        self.assertEqual((output / binary.relative_to(source)).read_bytes(), binary.read_bytes())
        self.assertIn(
            "French Test Mod (English Translation)",
            (output / "descriptor.mod").read_text(encoding="utf-8"),
        )
        manifest = json.loads((output / "translation-clone-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_language"], "French")
        self.assertEqual(manifest["source_locale"], "l_french")
        self.assertEqual(manifest["target_language"], "English")
        self.assertEqual(manifest["target_locale"], "l_english")
        self.assertEqual(result.localization_files, 2)
        self.assertTrue(any("from French into" in prompt for prompt in EnglishTranslationHandler.prompts))

        calls = EnglishTranslationHandler.calls
        create_localized_clone(CloneOptions(**{**options.__dict__, "overwrite": True}))
        self.assertEqual(EnglishTranslationHandler.calls, calls)
        self.assertTrue(any((self.root / "_ck3_translation_backups").iterdir()))

    def test_actual_japanese_stored_in_english_locale_can_translate_to_english(self) -> None:
        source = self.root / "JapaneseInEnglishHeader"
        original = source / "localization" / "english" / "mixed_l_english.yml"
        write_locale(original, "l_english", "mixed_key", "これは日本語の文章です [GetPlayer.GetName]")
        french = source / "localization" / "french" / "keep_l_french.yml"
        write_locale(french, "l_french", "keep_key", "Texte français conservé")
        (source / "descriptor.mod").write_text('name="Mixed Header"\n', encoding="utf-8")
        output = self.root / "Mixed_English"

        create_localized_clone(
            CloneOptions(
                source=source,
                output=output,
                endpoint=self.endpoint,
                model="multilingual-test-model",
                workers=1,
                work_root=self.root / "work",
                source_language="Japanese",
                source_locale="l_english",
                language="English",
                locale="l_english",
            )
        )
        translated = output / original.relative_to(source)
        text = translated.read_text(encoding="utf-8-sig")
        self.assertIn("A complete natural English translation", text)
        self.assertNotIn("これは日本語", text)
        self.assertIn("[GetPlayer.GetName]", text)
        self.assertEqual((output / french.relative_to(source)).read_bytes(), french.read_bytes())
        self.assertFalse(valid_translation("Полное русское предложение", "Полное русское предложение", "German", "Russian"))
        self.assertTrue(valid_translation("Полное русское предложение", "Ein vollständig übersetzter Satz", "German", "Russian"))

    @unittest.skipUnless(FROZEN_APP.is_file(), "frozen multilingual application has not been built")
    def test_frozen_app_translates_japanese_under_english_locale_to_english(self) -> None:
        source = self.root / "FrozenMixedLocale"
        original = source / "localization" / "english" / "mixed_l_english.yml"
        write_locale(original, "l_english", "frozen_mixed", "これは凍結版で翻訳する日本語です [GetPlayer.GetName]")
        (source / "descriptor.mod").write_text('name="Frozen Mixed Locale"\n', encoding="utf-8")
        output = self.root / "FrozenMixedLocale_English"
        result_path = self.root / "frozen-multilingual-result.json"

        completed = subprocess.run(
            [
                str(FROZEN_APP),
                "--headless-source",
                str(source),
                "--headless-output",
                str(output),
                "--headless-result",
                str(result_path),
                "--source-language",
                "japanese",
                "--source-locale",
                "l_english",
                "--target-language",
                "english",
                "--target-locale",
                "l_english",
                "--endpoint",
                self.endpoint,
                "--model",
                "multilingual-test-model",
                "--work-root",
                str(self.root / "frozen-work"),
                "--workers",
                "1",
            ],
            cwd=ROOT,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertTrue(json.loads(result_path.read_text(encoding="utf-8"))["ok"])
        translated = (output / original.relative_to(source)).read_text(encoding="utf-8-sig")
        self.assertIn("A complete natural English translation", translated)
        self.assertNotIn("これは凍結版", translated)
        self.assertIn("[GetPlayer.GetName]", translated)


if __name__ == "__main__":
    unittest.main()
