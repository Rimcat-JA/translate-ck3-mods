from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "ck3_pipeline.py"
BUNDLER = ROOT / "scripts" / "ck3_bundle.py"
CLONER = ROOT / "scripts" / "ck3_clone.py"
FROZEN_APP = ROOT / "dist" / "CK3_Japanese_Mod_Maker.exe"


class LocalTranslationHandler(BaseHTTPRequestHandler):
    calls = 0

    def do_GET(self) -> None:
        body = json.dumps({"data": [{"id": "test-local-model", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        type(self).calls += 1
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        items = json.loads(payload["messages"][-1]["content"])["items"]
        translations = {
            key: "完全に翻訳された日本語文章" + "".join(re.findall(r"__CK3TOKEN_\d+__", value))
            for key, value in items.items()
        }
        body = json.dumps({"choices": [{"message": {"content": json.dumps({"translations": translations}, ensure_ascii=False)}}]}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def create_mod(root: Path, name: str, entries: list[tuple[str, str]]) -> Path:
    mod = root / name
    folder = mod / "localization" / "english"
    folder.mkdir(parents=True)
    text = "l_english:\n" + "\n".join(f' {key}:0 "{value}"' for key, value in entries) + "\n"
    (folder / f"{name.lower()}_l_english.yml").write_text(text, encoding="utf-8")
    return mod


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        LocalTranslationHandler.calls = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), LocalTranslationHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def run_program(self, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, *args], cwd=ROOT, text=True, capture_output=True, check=False)
        if ok and result.returncode:
            self.fail(f"command failed ({result.returncode})\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def test_full_pipeline_and_cache_reuse(self) -> None:
        first = create_mod(self.root, "First", [("hello", "Hello"), ("same", "Shared")])
        second = create_mod(self.root, "Second", [("world", "World"), ("same", "Shared")])
        config = {
            "translation": {
                "mods": [{"name": "First", "path": str(first)}, {"name": "Second", "path": str(second)}],
                "staging_output": str(self.root / "stage"),
                "cache": str(self.root / "memory.sqlite"),
                "language": "Japanese",
                "locale": "l_japanese",
                "endpoint": f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions",
                "model": "test-local-model",
                "workers": 1,
            },
            "bundle": {
                "destination": str(self.root / "bundle"), "id": "combined-ja", "name": "Combined JA",
                "collision_policy": "error", "overwrite": True,
            },
        }
        config_path = self.root / "pipeline.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self.run_program(str(PIPELINE), "--config", str(config_path))
        calls = LocalTranslationHandler.calls
        self.assertGreater(calls, 0)

        mod_root = self.root / "bundle" / "combined-ja"
        launcher = self.root / "bundle" / "combined-ja.mod"
        archive = self.root / "bundle" / "combined-ja.zip"
        self.assertTrue((mod_root / "descriptor.mod").is_file())
        self.assertIn('path="mod/combined-ja"', launcher.read_text(encoding="utf-8"))
        manifest = json.loads((mod_root / "bundle-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["counts"], {"input_entries": 4, "unique_entries": 3, "collisions": 1})
        self.assertEqual(manifest["metadata"]["translation_engine"], "local-openai-compatible")
        self.assertNotIn(str(self.root), json.dumps(manifest))
        with zipfile.ZipFile(archive) as handle:
            self.assertIsNone(handle.testzip())
            self.assertIn("combined-ja/descriptor.mod", handle.namelist())
            self.assertIn("combined-ja.mod", handle.namelist())
        first_zip_hash = hashlib.sha256(archive.read_bytes()).hexdigest()

        self.run_program(str(PIPELINE), "--config", str(config_path))
        self.assertEqual(LocalTranslationHandler.calls, calls, "validated SQLite translations should be reused")
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), first_zip_hash, "bundle ZIP must be reproducible")
        self.assertTrue(any((self.root / "bundle" / "_bundle_backups").iterdir()))

    def test_conflicting_duplicate_fails_by_default_and_last_can_win(self) -> None:
        stage_a = create_mod(self.root, "StageA", [("duplicate", "訳：first")])
        stage_b = create_mod(self.root, "StageB", [("duplicate", "訳：last")])
        # Convert the fixtures from source layout to staged Japanese layout.
        for root in (stage_a, stage_b):
            english = root / "localization" / "english"
            japanese = root / "localization" / "japanese"
            english.rename(japanese)
            path = next(japanese.glob("*.yml"))
            path.write_bytes(b"\xef\xbb\xbf" + path.read_text(encoding="utf-8").replace("l_english:", "l_japanese:").encode())
        base = [str(BUNDLER), "--source", f"A={stage_a}", "--source", f"B={stage_b}", "--destination", str(self.root / "out"), "--bundle-id", "test", "--bundle-name", "Test", "--language", "Japanese", "--locale", "l_japanese"]
        failed = self.run_program(*base, ok=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("Conflicting key", failed.stderr)
        self.run_program(*base, "--collision-policy", "last")
        output = next((self.root / "out" / "test" / "localization" / "japanese").glob("*.yml"))
        self.assertIn("訳：last", output.read_text(encoding="utf-8-sig"))

    def test_remote_llm_endpoint_is_rejected(self) -> None:
        source = create_mod(self.root, "Only", [("hello", "Hello")])
        config = {
            "translation": {"mods": [{"name": "Only", "path": str(source)}], "staging_output": "stage", "cache": "cache.sqlite", "language": "Japanese", "locale": "l_japanese", "endpoint": "https://api.example.com/v1/chat/completions", "model": "remote"},
            "bundle": {"destination": "out", "id": "test", "name": "Test"},
        }
        path = self.root / "remote.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        failed = self.run_program(str(PIPELINE), "--config", str(path), "--dry-run", ok=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("loopback local-LLM", failed.stderr)
        config["translation"]["endpoint"] = "http://127.evil.example/v1/chat/completions"
        path.write_text(json.dumps(config), encoding="utf-8")
        deceptive = self.run_program(str(PIPELINE), "--config", str(path), "--dry-run", ok=False)
        self.assertNotEqual(deceptive.returncode, 0)

    def test_clone_rejects_empty_localization_and_output_inside_source(self) -> None:
        empty = self.root / "EmptyMod"
        (empty / "localization" / "english").mkdir(parents=True)
        failed = self.run_program(
            str(CLONER), str(empty), "--output", str(self.root / "empty-output"),
            "--endpoint", f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions", ok=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("英語ローカライズYMLがありません", failed.stderr)

        source = create_mod(self.root, "UnsafeOutput", [("safe_key", "Safe source")])
        unsafe = self.run_program(
            str(CLONER), str(source), "--output", str(source / "generated"),
            "--endpoint", f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions", ok=False,
        )
        self.assertNotEqual(unsafe.returncode, 0)
        self.assertIn("元MODフォルダの内部", unsafe.stderr)

    def test_one_path_creates_complete_japanese_clone(self) -> None:
        source = create_mod(self.root, "FullMod", [("hello", "Hello [Character.GetName]"), ("description", "A complete English description.")])
        (source / "common" / "scripted_rules").mkdir(parents=True)
        (source / "common" / "scripted_rules" / "rules.txt").write_text("rule = { value = yes }\n", encoding="utf-8")
        (source / "gfx").mkdir()
        (source / "gfx" / "asset.bin").write_bytes(bytes(range(64)))
        (source / "descriptor.mod").write_text('name="Full Mod"\nversion="2.0"\nremote_file_id="123456"\n', encoding="utf-8")
        old_japanese = source / "localization" / "japanese"
        old_japanese.mkdir()
        (old_japanese / "obsolete_l_japanese.yml").write_bytes(b"\xef\xbb\xbf" + 'l_japanese:\n obsolete:0 "古い"\n'.encode())
        output = self.root / "FullMod_Japanese"
        work = self.root / "app-work"
        command = [
            str(CLONER), str(source), "--output", str(output), "--work-root", str(work),
            "--endpoint", f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions", "--workers", "1",
        ]
        self.run_program(*command)
        calls = LocalTranslationHandler.calls
        self.assertGreater(calls, 0)
        self.assertTrue((source / "localization" / "english").is_dir(), "source mod must remain unchanged")
        self.assertFalse((output / "localization" / "english").exists())
        japanese_files = list((output / "localization" / "japanese").glob("*.yml"))
        self.assertEqual(len(japanese_files), 1)
        japanese_text = japanese_files[0].read_text(encoding="utf-8-sig")
        self.assertIn("完全に翻訳された日本語文章[Character.GetName]", japanese_text)
        self.assertNotIn("A complete English description.", japanese_text)
        self.assertFalse((output / "localization" / "japanese" / "obsolete_l_japanese.yml").exists())
        self.assertEqual((output / "common" / "scripted_rules" / "rules.txt").read_bytes(), (source / "common" / "scripted_rules" / "rules.txt").read_bytes())
        self.assertEqual((output / "gfx" / "asset.bin").read_bytes(), (source / "gfx" / "asset.bin").read_bytes())
        descriptor_text = (output / "descriptor.mod").read_text(encoding="utf-8")
        self.assertIn("日本語化", descriptor_text)
        self.assertNotIn("remote_file_id", descriptor_text)
        launcher = output.parent / f"{output.name}.mod"
        self.assertIn(output.resolve().as_posix(), launcher.read_text(encoding="utf-8"))
        manifest = json.loads((output / "japanese-clone-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["translation_engine"], "local")
        self.assertNotIn(str(source), json.dumps(manifest))

        self.run_program(*command, "--overwrite")
        self.assertEqual(LocalTranslationHandler.calls, calls, "second clone should reuse all translated values")
        self.assertTrue(any((self.root / "_ck3_japanese_backups").iterdir()))

    @unittest.skipUnless(FROZEN_APP.is_file(), "frozen Windows application has not been built")
    def test_frozen_exe_runs_translation_and_clone_engine(self) -> None:
        source = create_mod(self.root, "FrozenSource", [("frozen_key", "Frozen executable translation")])
        (source / "common").mkdir()
        (source / "common" / "data.txt").write_text("preserve me", encoding="utf-8")
        output = self.root / "FrozenSource_Japanese"
        result_path = self.root / "frozen-result.json"
        result = subprocess.run(
            [
                str(FROZEN_APP), "--headless-source", str(source), "--headless-output", str(output),
                "--headless-result", str(result_path), "--work-root", str(self.root / "frozen-work"),
                "--endpoint", f"http://127.0.0.1:{self.server.server_port}/v1/chat/completions", "--workers", "1",
            ],
            cwd=ROOT,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        report = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertTrue(report["ok"])
        self.assertFalse((output / "localization" / "english").exists())
        translated = next((output / "localization" / "japanese").glob("*.yml")).read_text(encoding="utf-8-sig")
        self.assertIn("完全に翻訳された日本語文章", translated)
        self.assertNotIn("Frozen executable translation", translated)
        self.assertEqual((output / "common" / "data.txt").read_text(encoding="utf-8"), "preserve me")


if __name__ == "__main__":
    unittest.main()
