#!/usr/bin/env python3
"""Create a complete Japanese clone of one CK3 mod.

The selected LLM/API is used only by ck3_localize.py to translate localization
values. Copying, descriptor editing, validation, backups, and manifests are
deterministic standard-library operations.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ck3_localize import ModSpec, command_translate, locale_id, parse_mod
from ck3_providers import PROVIDERS, get_provider, models_endpoint, validate_endpoint

ProgressCallback = Callable[[dict[str, object]], None]


class CancelledError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class CloneOptions:
    source: Path
    output: Path | None = None
    endpoint: str = "http://127.0.0.1:1234/v1/chat/completions"
    provider: str = "local"
    api_key: str | None = None
    model: str | None = None
    workers: int = 4
    overwrite: bool = False
    work_root: Path | None = None
    language: str = "Japanese"
    locale: str = "l_japanese"
    batch_items: int = 8
    batch_chars: int = 5000
    long_threshold: int = 800
    long_segment: int = 600
    retries: int = 4
    timeout: int = 180
    max_tokens: int = 8000
    temperature: float = 0.2


@dataclasses.dataclass(frozen=True)
class CloneResult:
    output: Path
    launcher: Path
    backup: Path | None
    model: str
    files_copied: int
    localization_files: int
    entries: int
    cache: Path


def emit(callback: ProgressCallback | None, event: str, **values: object) -> None:
    if callback:
        callback({"event": event, **values})


def discover_models(endpoint: str, timeout: int = 5, provider: str = "local", api_key: str | None = None) -> list[str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(models_endpoint(provider, endpoint), headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    models = [str(row["id"]) for row in rows if isinstance(row, dict) and row.get("id")]
    return list(dict.fromkeys(models))


def default_output(source: Path) -> Path:
    destination = ck3_mod_directory()
    return destination / f"{source.name}_Japanese" if destination else source.parent / f"{source.name}_Japanese"


def ck3_mod_directory() -> Path | None:
    """Return the user's CK3 local-mod directory without requiring configuration."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        # CSIDL_PERSONAL resolves redirected Documents folders as well.
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) != 0:
            return None
        return Path(buffer.value) / "Paradox Interactive" / "Crusader Kings III" / "mod"
    except (AttributeError, OSError):
        return None


def default_work_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    return base / "CK3JapaneseModMaker"


def safe_stage_name(source: Path) -> str:
    digest = hashlib.sha256(str(source).casefold().encode("utf-8")).hexdigest()[:12]
    return f"source_{digest}"


def is_same_or_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_paths(source: Path, output: Path) -> ModSpec:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    spec = parse_mod(f"{safe_stage_name(source)}={source}")
    if not any(spec.english_root.rglob("*.yml")):
        raise RuntimeError(f"英語ローカライズYMLがありません: {spec.english_root}")
    if source == output:
        raise ValueError("出力先を元MODと同じ場所にはできません。")
    if is_same_or_inside(output, source):
        raise ValueError("出力先を元MODフォルダの内部にはできません。")
    if is_same_or_inside(source, output):
        raise ValueError("元MODを包含するフォルダは出力先にできません。")
    return spec


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(spec: ModSpec) -> str:
    digest = hashlib.sha256()
    for path in sorted(spec.english_root.rglob("*.yml"), key=lambda item: item.as_posix().casefold()):
        digest.update(path.relative_to(spec.english_root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run_translation(
    options: CloneOptions,
    spec: ModSpec,
    model: str,
    staging: Path,
    cache: Path,
    callback: ProgressCallback | None,
    cancel_event: threading.Event | None,
) -> tuple[int, int]:
    entries = 0
    files = 0

    def handle_line(line: str) -> None:
        nonlocal entries, files
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            emit(callback, "log", message=line)
            return
        if "entries" in data and "pending" in data:
            entries = int(data["entries"])
            emit(callback, "translation_started", **data)
        elif "completed_batches" in data:
            emit(callback, "translation_progress", **data)
        elif "files" in data and "errors" in data:
            files = int(data["files"])
            emit(callback, "translation_validated", **data)
        else:
            emit(callback, "log", message=line)

    class ProgressWriter:
        def __init__(self) -> None:
            self.pending = ""

        def write(self, text: str) -> int:
            self.pending += text
            while "\n" in self.pending:
                line, self.pending = self.pending.split("\n", 1)
                if line.strip():
                    handle_line(line.rstrip())
            return len(text)

        def flush(self) -> None:
            if self.pending.strip():
                handle_line(self.pending.rstrip())
            self.pending = ""

    arguments = argparse.Namespace(
        mod=[f"{spec.name}={spec.root}"],
        output=str(staging),
        cache=str(cache),
        language=options.language,
        locale=options.locale,
        endpoint=options.endpoint,
        provider=options.provider,
        api_key=options.api_key,
        model=model,
        api_key_env=None,
        glossary=None,
        extra_instructions=None,
        workers=options.workers,
        batch_items=options.batch_items,
        batch_chars=options.batch_chars,
        long_threshold=options.long_threshold,
        long_segment=options.long_segment,
        retries=options.retries,
        timeout=options.timeout,
        max_tokens=options.max_tokens,
        temperature=options.temperature,
        min_interval=0.0,
        cancel_event=cancel_event,
    )
    writer = ProgressWriter()
    try:
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            command_translate(arguments)
    except SystemExit as exc:
        if cancel_event and cancel_event.is_set():
            raise CancelledError("処理をキャンセルしました。翻訳キャッシュは保持されています。") from exc
        raise RuntimeError(f"翻訳処理に失敗しました（終了コード {exc.code}）。ログを確認してください。") from exc
    except Exception as exc:
        if cancel_event and cancel_event.is_set():
            raise CancelledError("処理をキャンセルしました。翻訳キャッシュは保持されています。") from exc
        raise
    finally:
        writer.flush()
    if cancel_event and cancel_event.is_set():
        raise CancelledError("処理をキャンセルしました。翻訳キャッシュは保持されています。")
    return files, entries


def descriptor_name(text: str, fallback: str) -> str:
    match = re.search(r'^\s*name\s*=\s*"((?:\\.|[^"])*)"', text, flags=re.MULTILINE)
    return match.group(1).replace(r'\"', '"') if match else fallback


def translated_display_name(name: str) -> str:
    return name if "日本語" in name else f"{name}（日本語化）"


def update_descriptor(path: Path, fallback_name: str, launcher_path: Path | None = None) -> str:
    if path.is_file():
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    else:
        text = 'tags={\n    "Translation"\n}\n'
    name = translated_display_name(descriptor_name(text, fallback_name)).replace('"', r'\"')
    if re.search(r'^\s*name\s*=', text, flags=re.MULTILINE):
        text = re.sub(r'^\s*name\s*=.*$', f'name="{name}"', text, count=1, flags=re.MULTILINE)
    else:
        text = f'name="{name}"\n' + text
    text = re.sub(r'^\s*(?:path|archive|remote_file_id)\s*=.*(?:\r?\n|$)', "", text, flags=re.MULTILINE)
    text = text.rstrip() + "\n"
    if launcher_path is not None:
        escaped = launcher_path.resolve().as_posix().replace('"', r'\"')
        text += f'path="{escaped}"\n'
    return text


def copy_and_replace(
    spec: ModSpec,
    staged_mod: Path,
    clone: Path,
    locale: str,
    callback: ProgressCallback | None,
) -> int:
    emit(callback, "copying", message="元MOD全体を複製しています…")
    shutil.copytree(spec.root, clone)
    localization = clone / "localization"
    english = clone / spec.english_root.relative_to(spec.root)
    target = localization / locale_id(locale)
    if english.exists():
        shutil.rmtree(english)
    if target.exists():
        shutil.rmtree(target)
    staged_locale = staged_mod / "localization" / locale_id(locale)
    if not staged_locale.is_dir():
        raise FileNotFoundError(f"検証済み翻訳フォルダがありません: {staged_locale}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staged_locale, target)
    descriptor = clone / "descriptor.mod"
    descriptor.write_text(update_descriptor(descriptor, spec.root.name), encoding="utf-8", newline="\n")
    return sum(1 for path in clone.rglob("*") if path.is_file())


def verify_clone(spec: ModSpec, staged_mod: Path, clone: Path, locale: str) -> None:
    cloned_english = clone / spec.english_root.relative_to(spec.root)
    if cloned_english.exists():
        raise RuntimeError("複製先に英語ローカライズが残っています。")
    staged_locale = staged_mod / "localization" / locale_id(locale)
    cloned_locale = clone / "localization" / locale_id(locale)
    staged_files = sorted(path.relative_to(staged_locale) for path in staged_locale.rglob("*.yml"))
    cloned_files = sorted(path.relative_to(cloned_locale) for path in cloned_locale.rglob("*.yml"))
    if staged_files != cloned_files:
        raise RuntimeError("複製先の日本語ローカライズファイル構成が翻訳結果と一致しません。")
    for relative in staged_files:
        if (staged_locale / relative).read_bytes() != (cloned_locale / relative).read_bytes():
            raise RuntimeError(f"複製先で翻訳ファイルが変化しました: {relative}")

    ignored_roots = {spec.english_root.relative_to(spec.root), Path(f"localization/{locale_id(locale)}")}
    for source_file in (path for path in spec.root.rglob("*") if path.is_file()):
        relative = source_file.relative_to(spec.root)
        if relative == Path("descriptor.mod") or any(root == relative or root in relative.parents for root in ignored_roots):
            continue
        target_file = clone / relative
        if not target_file.is_file() or sha256_file(source_file) != sha256_file(target_file):
            raise RuntimeError(f"元MODのファイルが正しく複製されていません: {relative}")


def backup_existing(output: Path, launcher: Path) -> Path | None:
    existing = [path for path in (output, launcher) if path.exists()]
    if not existing:
        return None
    stamp = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    backup = output.parent / "_ck3_japanese_backups" / f"{output.name}_{stamp}"
    serial = 1
    while backup.exists():
        backup = output.parent / "_ck3_japanese_backups" / f"{output.name}_{stamp}_{serial}"
        serial += 1
    backup.mkdir(parents=True)
    moved: list[tuple[Path, Path]] = []
    try:
        for path in existing:
            stored = backup / path.name
            shutil.move(str(path), str(stored))
            moved.append((path, stored))
    except Exception:
        for original, stored in reversed(moved):
            if stored.exists() and not original.exists():
                shutil.move(str(stored), str(original))
        with contextlib.suppress(OSError):
            backup.rmdir()
        raise
    return backup


def remove_generated_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def restore_backup(backup: Path, output: Path, launcher: Path) -> None:
    for original in (output, launcher):
        stored = backup / original.name
        if stored.exists():
            shutil.move(str(stored), str(original))


def finalize_clone(
    clone: Path,
    launcher_temp: Path,
    output: Path,
    launcher: Path,
    backup: Path | None,
) -> None:
    """Install the verified clone and launcher together, restoring an old version on failure."""
    output_installed = False
    launcher_installed = False
    try:
        os.replace(clone, output)
        output_installed = True
        os.replace(launcher_temp, launcher)
        launcher_installed = True
    except Exception:
        if launcher_installed:
            remove_generated_path(launcher)
        if output_installed:
            remove_generated_path(output)
        if backup is not None:
            restore_backup(backup, output, launcher)
        raise


def create_japanese_clone(
    options: CloneOptions,
    callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> CloneResult:
    source = options.source.expanduser().resolve()
    output = (options.output or default_output(source)).expanduser().resolve()
    provider = get_provider(options.provider)
    validate_endpoint(options.provider, options.endpoint)
    if provider.requires_key and not options.api_key:
        raise RuntimeError(f"{provider.label}のAPIキーを入力してください。")
    spec = validate_paths(source, output)
    launcher = output.parent / f"{output.name}.mod"
    if (output.exists() or launcher.exists()) and not options.overwrite:
        raise FileExistsError("出力先が既に存在します。上書きを選ぶと、既存版をバックアップして作り直します。")
    emit(callback, "checking", message=f"MODと{provider.label}を確認しています…")
    models = discover_models(options.endpoint, provider=options.provider, api_key=options.api_key) if options.provider == "local" or not options.model else []
    model = options.model or (models[0] if models else None)
    if not model:
        if options.provider == "local":
            raise RuntimeError("ローカルLLMが読み込まれていません。LM StudioでモデルとLocal Serverを開始してください。")
        raise RuntimeError(f"{provider.label}からモデルを取得できません。モデルIDとAPIキーを確認してください。")
    if options.provider == "local" and options.model and models and options.model not in models:
        raise RuntimeError(f"指定モデルがローカルサーバーにありません: {options.model}")
    emit(callback, "model_selected", model=model)

    work_root = (options.work_root or default_work_root()).expanduser().resolve()
    cache_root = work_root / "cache"
    run_root = work_root / "runs"
    cache_root.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    cache = cache_root / f"{safe_stage_name(source)}.sqlite"
    temporary_run = Path(tempfile.mkdtemp(prefix="run-", dir=run_root))
    temporary_output = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    staging = temporary_run / "staging"
    clone = temporary_output / output.name
    backup: Path | None = None
    try:
        files, entries = run_translation(options, spec, model, staging, cache, callback, cancel_event)
        if cancel_event and cancel_event.is_set():
            raise CancelledError("処理をキャンセルしました。翻訳キャッシュは保持されています。")
        staged_mod = staging / spec.name
        files_copied = copy_and_replace(spec, staged_mod, clone, options.locale, callback)
        emit(callback, "verifying", message="完全コピーと日本語置換を検証しています…")
        verify_clone(spec, staged_mod, clone, options.locale)
        launcher_temp = temporary_output / launcher.name
        launcher_temp.write_text(update_descriptor(clone / "descriptor.mod", spec.root.name, output), encoding="utf-8", newline="\n")
        manifest = {
            "schema": 1,
            "source_name": spec.root.name,
            "source_localization_sha256": source_fingerprint(spec),
            "language": options.language,
            "locale": options.locale,
            "translation_engine": options.provider,
            "translation_model": model,
            "counts": {"copied_files": files_copied, "localization_files": files, "entries": entries},
        }
        (clone / "japanese-clone-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        if options.overwrite:
            backup = backup_existing(output, launcher)
        finalize_clone(clone, launcher_temp, output, launcher, backup)
        result = CloneResult(output, launcher, backup, model, files_copied, files, entries, cache)
        emit(callback, "done", output=str(output), launcher=str(launcher), entries=entries, files=files)
        return result
    finally:
        shutil.rmtree(temporary_run, ignore_errors=True)
        shutil.rmtree(temporary_output, ignore_errors=True)


def console_progress(event: dict[str, object]) -> None:
    stream = sys.__stdout__ or sys.stdout
    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    stream.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copy one CK3 mod and replace its English localization with validated Japanese")
    parser.add_argument("source", help="path to the source CK3 mod folder")
    parser.add_argument("--output", help="output mod folder (default: CK3 local-mod directory/<source>_Japanese)")
    parser.add_argument("--endpoint", help="provider endpoint; uses the selected provider default when omitted")
    parser.add_argument("--provider", choices=tuple(PROVIDERS), default="local")
    parser.add_argument("--api-key-env", help="environment variable containing a remote provider API key")
    parser.add_argument("--model", help="model id; local mode automatically selects the first loaded model when omitted")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--work-root", help="cache and temporary-work directory")
    parser.add_argument("--overwrite", action="store_true", help="back up and replace an existing generated clone")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = create_japanese_clone(
        CloneOptions(
            source=Path(args.source),
            output=Path(args.output) if args.output else None,
            endpoint=args.endpoint or get_provider(args.provider).chat_endpoint,
            provider=args.provider,
            api_key=os.environ.get(args.api_key_env) if args.api_key_env else None,
            model=args.model,
            workers=args.workers,
            work_root=Path(args.work_root) if args.work_root else None,
            overwrite=args.overwrite,
        ),
        console_progress,
    )
    print(json.dumps(dataclasses.asdict(result), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
