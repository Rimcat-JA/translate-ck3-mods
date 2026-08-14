#!/usr/bin/env python3
"""Create a complete translated clone of one CK3 mod.

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
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ck3_http import urlopen_no_redirect
from ck3_languages import discover_locale_files, language_spec, normalize_language_id
from ck3_localize import ModSpec, command_translate, parse_mod
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
    source_language: str = "English"
    source_locale: str = "l_english"
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


def _model_ids(payload: object) -> list[str]:
    """Read OpenAI-compatible and LM Studio native model-list payloads."""
    if not isinstance(payload, dict):
        return []

    candidates: list[object] = []

    def is_translation_model(row: object) -> bool:
        if not isinstance(row, dict):
            return True
        model_type = row.get("type")
        if not isinstance(model_type, str):
            return True
        return model_type.strip().casefold() not in {"embedding", "embeddings"}

    data = payload.get("data")
    if isinstance(data, list):
        candidates.extend(
            row.get("id") or row.get("key") if isinstance(row, dict) else row
            for row in data
            if is_translation_model(row)
        )
    models = payload.get("models")
    if isinstance(models, list):
        # LM Studio's native /api/v1/models endpoint uses `key` for the
        # JIT-loadable model identifier. Some older/native-compatible servers
        # expose the same value as `id` instead.
        candidates.extend(
            row.get("key") or row.get("id") if isinstance(row, dict) else row
            for row in models
            if is_translation_model(row)
        )

    identifiers: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        identifier = candidate.strip()
        if identifier and identifier not in seen:
            identifiers.append(identifier)
            seen.add(identifier)
    return identifiers


def _native_models_endpoints(endpoint: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(endpoint)
    return tuple(
        urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        for path in ("/api/v1/models", "/api/v0/models")
    )


def model_discovery_endpoints(endpoint: str, provider: str = "local") -> tuple[str, ...]:
    """Return validated model-list URLs in provider-specific preference order."""
    primary = models_endpoint(provider, endpoint)
    if provider != "local":
        return (primary,)
    native_v1, native_v0 = _native_models_endpoints(endpoint)
    # Prefer native v1's type metadata so embedding-only downloads cannot
    # become the automatically selected translation model.
    return tuple(dict.fromkeys((native_v1, primary, native_v0)))


def discover_models(endpoint: str, timeout: int = 5, provider: str = "local", api_key: str | None = None) -> list[str]:
    # Validate and derive every destination before a credential-bearing header
    # is constructed.
    discovery_endpoints = model_discovery_endpoints(endpoint, provider)
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    first_error: Exception | None = None
    received_response = False
    for discovery_endpoint in discovery_endpoints:
        request = urllib.request.Request(discovery_endpoint, headers=headers)
        try:
            with urlopen_no_redirect(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            if provider != "local":
                raise
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
                if exc.code in {401, 403}:
                    # Authentication failures are actionable and apply to the
                    # server, so do not hide them behind alternate-path probes.
                    raise
            first_error = first_error or exc
            continue
        received_response = True
        identifiers = _model_ids(payload)
        if identifiers:
            return identifiers

    if not received_response and first_error is not None:
        raise first_error
    return []


def default_output(source: Path, target_language: str = "Japanese") -> Path:
    try:
        display = language_spec(target_language).display_name
    except ValueError:
        display = target_language.strip() or "Translated"
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", display).strip("_") or "Translated"
    destination = ck3_mod_directory()
    return destination / f"{source.name}_{suffix}" if destination else source.parent / f"{source.name}_{suffix}"


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


def validate_paths(
    source: Path,
    output: Path,
    source_locale: str = "l_english",
    source_language: str = "English",
) -> ModSpec:
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    spec = parse_mod(f"{safe_stage_name(source)}={source}", source_locale, source_language)
    if not spec.source_files:
        raise RuntimeError(f"No source-localization YML files were found: {source / 'localization'}")
    if source == output:
        raise ValueError("The output cannot be the source mod itself.")
    if is_same_or_inside(output, source):
        raise ValueError("The output cannot be inside the source mod folder.")
    if is_same_or_inside(source, output):
        raise ValueError("The output cannot contain the source mod folder.")
    return spec


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(spec: ModSpec) -> str:
    digest = hashlib.sha256()
    for path in spec.source_files:
        digest.update(path.relative_to(spec.root).as_posix().encode("utf-8"))
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
        source_language=options.source_language,
        source_locale=options.source_locale,
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
            raise CancelledError("Translation was cancelled. The translation cache was kept.") from exc
        raise RuntimeError(f"Translation failed with exit code {exc.code}; check the log.") from exc
    except Exception as exc:
        if cancel_event and cancel_event.is_set():
            raise CancelledError("Translation was cancelled. The translation cache was kept.") from exc
        raise
    finally:
        writer.flush()
    if cancel_event and cancel_event.is_set():
        raise CancelledError("Translation was cancelled. The translation cache was kept.")
    return files, entries


def descriptor_name(text: str, fallback: str) -> str:
    match = re.search(r'^\s*name\s*=\s*"((?:\\.|[^"])*)"', text, flags=re.MULTILINE)
    return match.group(1).replace(r'\"', '"') if match else fallback


def translated_display_name(name: str, language: str = "Japanese", legacy_label: bool = False) -> str:
    if legacy_label and language.strip().casefold() == "japanese":
        return name if "日本語" in name else f"{name}（日本語化）"
    suffix = f"({language.strip()} Translation)"
    return name if name.casefold().endswith(suffix.casefold()) else f"{name} {suffix}"


def update_descriptor(
    path: Path,
    fallback_name: str,
    launcher_path: Path | None = None,
    language: str = "Japanese",
    legacy_label: bool = False,
) -> str:
    if path.is_file():
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    else:
        text = 'tags={\n    "Translation"\n}\n'
    name = translated_display_name(descriptor_name(text, fallback_name), language, legacy_label).replace('"', r'\"')
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
    target_language: str = "Japanese",
    legacy_label: bool = False,
) -> int:
    emit(callback, "copying", message="Copying the complete source mod...")
    shutil.copytree(spec.root, clone)
    target_id = normalize_language_id(locale)
    source_paths = {path.relative_to(spec.root) for path in spec.source_files}
    old_target_paths = {
        path.relative_to(spec.root) for path in discover_locale_files(spec.root, target_id)
    }
    replacement_paths = source_paths | old_target_paths
    for relative in replacement_paths:
        candidate = clone / relative
        if candidate.is_file() or candidate.is_symlink():
            candidate.unlink()

    staged_files = sorted(
        (path for path in staged_mod.rglob("*.yml") if path.is_file()),
        key=lambda item: item.relative_to(staged_mod).as_posix().casefold(),
    )
    if not staged_files:
        raise FileNotFoundError(f"No validated translated YML files were found: {staged_mod}")
    for source_file in staged_files:
        relative = source_file.relative_to(staged_mod)
        original = spec.root / relative
        if original.is_file() and relative not in replacement_paths:
            raise RuntimeError(f"Translated output would overwrite a different source locale: {relative}")
        destination = clone / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination)

    localization = clone / "localization"
    if localization.is_dir():
        for directory in sorted(
            (path for path in localization.rglob("*") if path.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            with contextlib.suppress(OSError):
                directory.rmdir()
    descriptor = clone / "descriptor.mod"
    descriptor.write_text(
        update_descriptor(descriptor, spec.root.name, language=target_language, legacy_label=legacy_label),
        encoding="utf-8",
        newline="\n",
    )
    return sum(1 for path in clone.rglob("*") if path.is_file())


def verify_clone(spec: ModSpec, staged_mod: Path, clone: Path, locale: str) -> None:
    target_id = normalize_language_id(locale)
    source_paths = {path.relative_to(spec.root) for path in spec.source_files}
    old_target_paths = {
        path.relative_to(spec.root) for path in discover_locale_files(spec.root, target_id)
    }
    replaced_paths = source_paths | old_target_paths
    staged_files = sorted(
        (path for path in staged_mod.rglob("*.yml") if path.is_file()),
        key=lambda item: item.relative_to(staged_mod).as_posix().casefold(),
    )
    staged_paths = {path.relative_to(staged_mod) for path in staged_files}
    for staged_file in staged_files:
        relative = staged_file.relative_to(staged_mod)
        cloned_file = clone / relative
        if not cloned_file.is_file() or staged_file.read_bytes() != cloned_file.read_bytes():
            raise RuntimeError(f"A translated file changed in the clone: {relative}")
    for relative in replaced_paths - staged_paths:
        if (clone / relative).exists():
            raise RuntimeError(f"A replaced source/target localization file remains: {relative}")

    for source_file in (path for path in spec.root.rglob("*") if path.is_file()):
        relative = source_file.relative_to(spec.root)
        if relative == Path("descriptor.mod") or relative in replaced_paths:
            continue
        target_file = clone / relative
        if not target_file.is_file() or sha256_file(source_file) != sha256_file(target_file):
            raise RuntimeError(f"A non-localization source file was not copied byte-for-byte: {relative}")


def backup_existing(
    output: Path,
    launcher: Path,
    directory_name: str = "_ck3_japanese_backups",
) -> Path | None:
    existing = [path for path in (output, launcher) if path.exists()]
    if not existing:
        return None
    stamp = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    backup = output.parent / directory_name / f"{output.name}_{stamp}"
    serial = 1
    while backup.exists():
        backup = output.parent / directory_name / f"{output.name}_{stamp}_{serial}"
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


def _create_localized_clone(
    options: CloneOptions,
    callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    *,
    legacy_compat: bool = False,
) -> CloneResult:
    source = options.source.expanduser().resolve()
    output = (options.output or default_output(source, options.language)).expanduser().resolve()
    provider = get_provider(options.provider)
    provider_name = {
        "local": "Local LLM",
        "openai": "OpenAI API",
        "openrouter": "OpenRouter API",
        "nanogpt": "NanoGPT API",
        "nanogpt_subscription": "NanoGPT Subscription API",
    }.get(options.provider, provider.label)
    validate_endpoint(options.provider, options.endpoint)
    if provider.requires_key and not options.api_key:
        raise RuntimeError(f"Enter an API key for {provider_name}.")
    if options.source_language.strip().casefold() == options.language.strip().casefold():
        raise ValueError("Source and target language are the same; translation is not needed.")
    spec = validate_paths(source, output, options.source_locale, options.source_language)
    launcher = output.parent / f"{output.name}.mod"
    if (output.exists() or launcher.exists()) and not options.overwrite:
        raise FileExistsError("The output already exists. Enable overwrite to back it up and rebuild it.")
    emit(callback, "checking", message=f"Checking the mod and {provider_name}...")
    # An explicit model ID is authoritative. LM Studio can JIT-load downloaded
    # models that are absent from /v1/models, and some authenticated/local
    # configurations do not expose a model-list endpoint at all.
    model = options.model.strip() if options.model else None
    models = (
        []
        if model
        else discover_models(
            options.endpoint,
            provider=options.provider,
            api_key=options.api_key,
        )
    )
    model = model or (models[0] if models else None)
    if not model:
        if options.provider == "local":
            raise RuntimeError(
                "No compatible local model was discovered. Start the LM Studio Local Server, "
                "then enter the exact model ID or load a chat model and enable JIT loading."
            )
        raise RuntimeError(f"No model is available from {provider_name}; check the model ID and API key.")
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
            raise CancelledError("Translation was cancelled. The translation cache was kept.")
        staged_mod = staging / spec.name
        files_copied = copy_and_replace(
            spec,
            staged_mod,
            clone,
            options.locale,
            callback,
            options.language,
            legacy_compat,
        )
        emit(callback, "verifying", message="Verifying the complete copy and localized replacements...")
        verify_clone(spec, staged_mod, clone, options.locale)
        launcher_temp = temporary_output / launcher.name
        launcher_temp.write_text(
            update_descriptor(
                clone / "descriptor.mod",
                spec.root.name,
                output,
                options.language,
                legacy_compat,
            ),
            encoding="utf-8",
            newline="\n",
        )
        manifest = {
            "schema": 2,
            "source_name": spec.root.name,
            "source_localization_sha256": source_fingerprint(spec),
            "source_language": options.source_language,
            "source_locale": f"l_{normalize_language_id(options.source_locale)}",
            "target_language": options.language,
            "target_locale": f"l_{normalize_language_id(options.locale)}",
            # Retain the version-1 fields for downstream readers.
            "language": options.language,
            "locale": options.locale,
            "translation_engine": options.provider,
            "translation_model": model,
            "counts": {"copied_files": files_copied, "localization_files": files, "entries": entries},
        }
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        (clone / "translation-clone-manifest.json").write_text(
            manifest_text, encoding="utf-8", newline="\n"
        )
        if legacy_compat and options.language.strip().casefold() == "japanese":
            (clone / "japanese-clone-manifest.json").write_text(
                manifest_text, encoding="utf-8", newline="\n"
            )
        if options.overwrite:
            backup = backup_existing(
                output,
                launcher,
                "_ck3_japanese_backups" if legacy_compat else "_ck3_translation_backups",
            )
        finalize_clone(clone, launcher_temp, output, launcher, backup)
        result = CloneResult(output, launcher, backup, model, files_copied, files, entries, cache)
        emit(callback, "done", output=str(output), launcher=str(launcher), entries=entries, files=files)
        return result
    finally:
        shutil.rmtree(temporary_run, ignore_errors=True)
        shutil.rmtree(temporary_output, ignore_errors=True)


def create_localized_clone(
    options: CloneOptions,
    callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> CloneResult:
    """Create a generic source-to-target translation clone."""
    return _create_localized_clone(options, callback, cancel_event)


def create_japanese_clone(
    options: CloneOptions,
    callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> CloneResult:
    """Version-1 compatibility wrapper; new code should use create_localized_clone."""
    return _create_localized_clone(options, callback, cancel_event, legacy_compat=True)


def console_progress(event: dict[str, object]) -> None:
    stream = sys.__stdout__ or sys.stdout
    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    stream.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copy one CK3 mod and replace one source localization with a validated translation")
    parser.add_argument("source", help="path to the source CK3 mod folder")
    parser.add_argument("--output", help="output mod folder (default: CK3 local-mod directory/<source>_<target>)")
    parser.add_argument("--source-language", default="English", help="actual language of the source text")
    parser.add_argument("--source-locale", default="l_english", help="CK3 locale/header containing the source text")
    parser.add_argument("--target-language", "--language", dest="language", default="Japanese")
    parser.add_argument("--target-locale", "--locale", dest="locale", default="l_japanese")
    parser.add_argument("--endpoint", help="provider endpoint; uses the selected provider default when omitted")
    parser.add_argument("--provider", choices=tuple(PROVIDERS), default="local")
    parser.add_argument(
        "--api-key-env",
        help="environment variable containing a provider token (optional for authenticated local servers)",
    )
    parser.add_argument(
        "--model",
        help="exact model id; when omitted, local mode selects the first discovered chat model (including JIT candidates)",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--work-root", help="cache and temporary-work directory")
    parser.add_argument("--overwrite", action="store_true", help="back up and replace an existing generated clone")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = create_localized_clone(
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
            source_language=args.source_language,
            source_locale=args.source_locale,
            language=args.language,
            locale=args.locale,
        ),
        console_progress,
    )
    print(json.dumps(dataclasses.asdict(result), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
