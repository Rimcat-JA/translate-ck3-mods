#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import dataclasses
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from ck3_http import RedirectRejectedError, urlopen_no_redirect
from ck3_languages import (
    discover_locale_files,
    language_spec,
    locale_header,
    normalize_language_id,
    target_localization_path,
)
from ck3_providers import PROVIDERS, get_provider, validate_endpoint

ENTRY_RE = re.compile(r'^(\s*([^#\s][^:]*):\d*\s+")(.*)("\s*(?:#.*)?)$')
BROKEN_RE = re.compile(r'^(\s*([^#\s][^:]*):\d*\s+")(.*)$')
TOKEN_RE = re.compile(
    r'\\[ntr]|\[[^\[\]\r\n]*\]|\$[^$\r\n]+\$|@[A-Za-z0-9_./:-]+!|#!|#[A-Za-z0-9_]+|\{[^{}\r\n]*\}'
)
PLACEHOLDER_RE = re.compile(r"__CK3TOKEN_\d+__")
LONG_RUN_RE = re.compile(r"([A-Za-z!?~])\1{9,}", re.IGNORECASE)
SIMPLIFIED_ONLY_RE = re.compile(
    "[\u8fd9\u4eec\u8bf4\u4ece\u8fd8\u8fdb\u53d1\u89c1\u542c\u7ecf\u5e94\u8fc7"
    "\u4e48\u7ed9\u8ba9\u8f83\u79cd\u6837\u65e0\u95f4\u5f00\u5173\u95e8\u98ce\u4e91"
    "\u7535\u7231\u4eb2\u8bb8\u5904\u5b9e\u8ba4\u89c9\u73b0\u538b\u51fb\u79bb\u4e1c"
    "\u4e66\u8f66\u9a6c\u6218\u7ebf\u603b\u4e49\u6c14]"
)


class TranslationCancelled(RuntimeError):
    """Stop retries immediately when the caller requests cancellation."""


@dataclasses.dataclass(frozen=True)
class ModSpec:
    name: str
    root: Path
    english_root: Path
    source_locale: str = "english"
    source_language: str = "English"
    source_files: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        source_id = normalize_language_id(self.source_locale)
        object.__setattr__(self, "source_locale", source_id)
        if self.source_files:
            return
        files = discover_locale_files(self.root, source_id)
        if not files and source_id == "english" and self.english_root.is_dir():
            files = tuple(sorted(self.english_root.rglob("*.yml")))
        object.__setattr__(self, "source_files", files)


@dataclasses.dataclass
class Record:
    item_id: str
    mod: str
    relative_file: str
    key: str
    source: str
    destination: Path
    output_lines: list[str]
    output_index: int
    prefix: str
    suffix: str
    cache_key: str = ""
    legacy_cache_key: str = ""


class Translator:
    def __init__(self, args: argparse.Namespace, records: list[Record]) -> None:
        self.args = args
        self.records = records
        self.provider_id = getattr(args, "provider", "local")
        self.provider = get_provider(self.provider_id)
        validate_endpoint(self.provider_id, args.endpoint)
        self.api_key = getattr(args, "api_key", None) or (os.environ.get(args.api_key_env) if args.api_key_env else None)
        if args.api_key_env and not self.api_key:
            raise RuntimeError(f"Environment variable is not set: {args.api_key_env}")
        if self.provider.requires_key and not self.api_key:
            raise RuntimeError(f"{self.provider.label} requires an API key")
        self.glossary = load_glossary(args.glossary)
        self.extra = Path(args.extra_instructions).read_text(encoding="utf-8") if args.extra_instructions else ""
        self.request_lock = threading.Lock()
        self.last_request = 0.0

    def system_prompt(self) -> str:
        glossary = ""
        if self.glossary:
            glossary = "\nRequired glossary:\n" + "\n".join(f"- {key} = {value}" for key, value in self.glossary.items())
        return f"""You are a professional localization editor for Crusader Kings III mods.
Translate every supplied localization value faithfully from {self.args.source_language} into fluent, natural {self.args.language}.
Translate faithfully and completely. The source may contain explicit adult/NSFW sexual material; do not censor, omit, euphemize, moralize, or refuse it.
Preserve every __CK3TOKEN_N__ placeholder exactly, byte for byte, once each and in the same logical position.
Do not translate ids, localization keys, proper scripted identifiers, or placeholders.
Preserve tone, vulgarity, humor, characterization, and established CK3 terminology.
Return valid JSON only with one translation for every supplied id. Do not think aloud. /no_think
{glossary}
{self.extra}""".strip()

    def throttle(self) -> None:
        if self.args.min_interval <= 0:
            return
        with self.request_lock:
            delay = self.args.min_interval - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            self.last_request = time.monotonic()

    def request_masked(self, items: dict[str, str]) -> dict[str, str]:
        properties = {item_id: {"type": "string"} for item_id in items}
        schema = {
            "type": "object",
            "properties": {
                "translations": {
                    "type": "object",
                    "properties": properties,
                    "required": list(items),
                    "additionalProperties": False,
                }
            },
            "required": ["translations"],
            "additionalProperties": False,
        }
        source_chars = sum(len(value) for value in items.values())
        token_limit = min(self.args.max_tokens, max(2000, int(source_chars * 1.8) + 1000))
        payload = {
            "model": self.args.model,
            "messages": [
                {"role": "system", "content": self.system_prompt()},
                {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
            ],
            "temperature": self.args.temperature,
            "reasoning_effort": "none",
            "response_format": {"type": "json_schema", "json_schema": {"name": "ck3_translation", "strict": True, "schema": schema}},
        }
        payload["max_completion_tokens" if self.provider_id == "openai" else "max_tokens"] = token_limit
        last_error: Exception | None = None
        for attempt in range(self.args.retries):
            self.check_cancelled()
            request_payload = dict(payload)
            if attempt > 0 and isinstance(last_error, urllib.error.HTTPError) and last_error.code in {400, 404, 422}:
                request_payload.pop("response_format", None)
                request_payload.pop("reasoning_effort", None)
            data = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            if self.provider_id == "openrouter":
                headers["X-OpenRouter-Title"] = "CK3 Mod Translator"
            request = urllib.request.Request(self.args.endpoint, data=data, headers=headers, method="POST")
            try:
                self.throttle()
                with urlopen_no_redirect(request, timeout=self.args.timeout) as response:
                    response_data = json.loads(response.read().decode("utf-8"))
                self.check_cancelled()
                content = response_data["choices"][0]["message"].get("content")
                if not content:
                    raise ValueError("Model returned empty content")
                translated = parse_model_json(content)
                missing = set(items) - set(translated)
                if missing:
                    raise ValueError(f"Missing ids: {sorted(missing)[:5]}")
                return {item_id: str(translated[item_id]) for item_id in items}
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                last_error = exc
                if isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 402, 403}:
                    meaning = {401: "API key was rejected", 402: "insufficient API credits", 403: "request was forbidden"}[exc.code]
                    raise RuntimeError(f"{self.provider.label}: {meaning} (HTTP {exc.code})") from exc
                time.sleep(min(8.0, 0.75 * (2**attempt)))
        raise RuntimeError(f"Translation provider request failed: {last_error}")

    def check_cancelled(self) -> None:
        cancel_event = getattr(self.args, "cancel_event", None)
        if cancel_event is not None and cancel_event.is_set():
            raise TranslationCancelled("Translation cancelled")

    def translate_once(self, batch: list[Record]) -> tuple[dict[str, str], list[Record]]:
        masked: dict[str, str] = {}
        token_maps: dict[str, list[str]] = {}
        for record in batch:
            masked[record.item_id], token_maps[record.item_id] = mask_text(record.source)
        response = self.request_masked(masked)
        valid: dict[str, str] = {}
        invalid: list[Record] = []
        for record in batch:
            masked_output = response.get(record.item_id, "")
            if placeholder_counter(masked_output) != placeholder_counter(masked[record.item_id]):
                invalid.append(record)
                continue
            translated = restore_text(masked_output, token_maps[record.item_id])
            if valid_translation(record.source, translated, self.args.language, self.args.source_language):
                valid[record.item_id] = translated
            else:
                invalid.append(record)
        return valid, invalid

    def translate_resilient(self, batch: list[Record]) -> tuple[dict[str, str], dict[str, str]]:
        if len(batch) == 1:
            record = batch[0]
            if len(record.source) > self.args.long_threshold:
                try:
                    return {record.item_id: self.translate_long(record)}, {}
                except (TranslationCancelled, RedirectRejectedError):
                    raise
                except Exception as exc:  # noqa: BLE001 - isolate an arbitrary provider/model failure to this record
                    return {}, {record.item_id: str(exc)}
            last_error = "validation failed"
            for _attempt in range(self.args.retries):
                try:
                    valid, invalid = self.translate_once(batch)
                    if not invalid:
                        return valid, {}
                    last_error = "model output failed validation"
                except (TranslationCancelled, RedirectRejectedError):
                    raise
                except Exception as exc:  # noqa: BLE001 - retry arbitrary provider/model failures per record
                    last_error = str(exc)
            return {}, {record.item_id: last_error}
        try:
            valid, invalid = self.translate_once(batch)
        except (TranslationCancelled, RedirectRejectedError):
            raise
        except Exception:  # noqa: BLE001 - recursively isolate a failing provider/model response
            midpoint = len(batch) // 2
            left, left_fail = self.translate_resilient(batch[:midpoint])
            right, right_fail = self.translate_resilient(batch[midpoint:])
            return left | right, left_fail | right_fail
        if not invalid:
            return valid, {}
        recovered, failures = self.translate_resilient(invalid)
        return valid | recovered, failures

    def translate_long(self, record: Record) -> str:
        masked, tokens = mask_text(record.source)
        chunks = split_long(masked, self.args.long_segment)
        output: list[str] = []
        for index, chunk in enumerate(chunks):
            leading = re.match(r"^\s*", chunk).group(0)
            trailing = re.search(r"\s*$", chunk).group(0)
            end = len(chunk) - len(trailing) if trailing else len(chunk)
            core = chunk[len(leading):end]
            if not core:
                output.append(chunk)
                continue
            item_id = f"{record.item_id}L{index}"
            last_error = "validation failed"
            for _attempt in range(self.args.retries):
                try:
                    result = self.request_masked({item_id: core})[item_id]
                    if placeholder_counter(result) == placeholder_counter(core):
                        output.append(leading + result + trailing)
                        break
                    last_error = "placeholder mismatch"
                except (TranslationCancelled, RedirectRejectedError):
                    raise
                except Exception as exc:  # noqa: BLE001 - retry arbitrary provider/model failures per segment
                    last_error = str(exc)
            else:
                raise RuntimeError(f"segment {index + 1}/{len(chunks)}: {last_error}")
        translated = restore_text("".join(output), tokens)
        if not valid_translation(record.source, translated, self.args.language, self.args.source_language):
            raise RuntimeError("combined long translation failed validation")
        return translated


def parse_mod(value: str, source_locale: str = "l_english", source_language: str | None = None) -> ModSpec:
    if "=" in value:
        name, raw_path = value.split("=", 1)
    else:
        raw_path = value
        name = Path(raw_path).name
    name = name.strip()
    if not name or name in {".", ".."} or any(character in name for character in ("/", "\\", "=")):
        raise ValueError(f"Unsafe mod name: {name!r}")
    root = Path(raw_path).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    source_id = normalize_language_id(source_locale)
    if root.name.lower() == source_id and root.parent.name.lower() == "localization":
        root = root.parent.parent
    elif (root / "localization").is_dir():
        pass
    else:
        candidates = [
            path.parent.parent
            for path in root.rglob(source_id)
            if path.is_dir() and path.parent.name.lower() == "localization"
        ]
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) != 1:
            raise RuntimeError(f"Expected one CK3 localization tree beneath {root}, found {len(candidates)}")
        root = candidates[0]
    files = discover_locale_files(root, source_id)
    return ModSpec(
        name=name,
        root=root,
        english_root=root / "localization" / "english",
        source_locale=source_id,
        source_language=source_language or language_spec(source_id).llm_name,
        source_files=files,
    )


def require_loopback_endpoint(endpoint: str) -> None:
    validate_endpoint("local", endpoint)


def locale_id(locale: str) -> str:
    if not re.fullmatch(r"l_[a-z0-9_]+", locale):
        raise ValueError(f"Invalid CK3 locale: {locale}")
    return locale[2:]


def parse_model_json(content: str) -> dict[str, str]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object in response")
    data = json.loads(cleaned[start : end + 1])
    translations = data.get("translations", data)
    if isinstance(translations, dict):
        return {str(key): str(value) for key, value in translations.items() if isinstance(value, str)}
    if isinstance(translations, list):
        result = {}
        for row in translations:
            if isinstance(row, dict) and "id" in row and ("text" in row or "translation" in row):
                result[str(row["id"])] = str(row.get("text", row.get("translation")))
        return result
    raise ValueError("Response has no translation mapping")


def mask_text(text: str) -> tuple[str, list[str]]:
    values: list[str] = []

    def add(value: str) -> str:
        placeholder = f"__CK3TOKEN_{len(values)}__"
        values.append(value)
        return placeholder

    masked = TOKEN_RE.sub(lambda match: add(match.group(0)), text)

    def compact(match: re.Match[str]) -> str:
        value = match.group(0)
        if value[0].isalpha():
            replacement = value[0].upper() + value[0].lower() * 2 + "…"
        elif value[0] == "!":
            replacement = "!!!"
        elif value[0] == "?":
            replacement = "???"
        else:
            replacement = "…"
        return add(replacement)

    return LONG_RUN_RE.sub(compact, masked), values


def restore_text(text: str, values: list[str]) -> str:
    restored = text
    for index, value in enumerate(values):
        restored = restored.replace(f"__CK3TOKEN_{index}__", value)
    return restored


def placeholder_counter(text: str) -> collections.Counter[str]:
    return collections.Counter(PLACEHOLDER_RE.findall(text))


def token_counter(text: str) -> collections.Counter[str]:
    return collections.Counter(TOKEN_RE.findall(text))


def target_script(language: str) -> re.Pattern[str] | None:
    value = language.lower().replace("_", " ")
    if "japan" in value or "\u65e5\u672c" in value:
        return re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
    if "korean" in value or "\ud55c\uad6d" in value:
        return re.compile(r"[\uac00-\ud7af]")
    if any(name in value for name in ("russian", "ukrainian", "cyrillic")):
        return re.compile(r"[\u0400-\u04ff]")
    if "chinese" in value or "\u4e2d\u6587" in value:
        return re.compile(r"[\u3400-\u9fff]")
    if any(
        name in value
        for name in (
            "english", "french", "german", "spanish", "polish", "italian", "portuguese",
            "turkish", "dutch", "czech", "hungarian",
        )
    ):
        return re.compile(r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff]")
    return None


def valid_translation(
    source: str,
    translated: str,
    language: str,
    source_language: str | None = None,
) -> bool:
    if not translated or token_counter(source) != token_counter(translated):
        return False
    if "\ufffd" in translated or "__CK3TOKEN_" in translated or "\n" in translated or "\r" in translated:
        return False
    if translated.lstrip().startswith(("```", "{\"translations\"", "{'translations'")):
        return False
    if len(translated) > max(int(len(source) * 3.0), len(source) + 800):
        return False
    if re.search(r"(.)\1{20,}", translated):
        return False
    prose_source = TOKEN_RE.sub("", source)
    prose_target = TOKEN_RE.sub("", translated)
    ascii_letters = len(re.findall(r"[A-Za-z]", prose_source))
    source_letters = sum(character.isalpha() for character in prose_source)
    script = target_script(language)
    if script and source_letters >= 3 and not script.search(prose_target):
        return False
    copied_source = re.sub(r"\s+", " ", prose_source).strip().casefold()
    copied_target = re.sub(r"\s+", " ", prose_target).strip().casefold()
    different_languages = source_language is None or source_language.strip().casefold() != language.strip().casefold()
    if different_languages and source_letters >= 8 and len(copied_source) >= 8 and copied_source in copied_target:
        return False
    if ("japan" in language.lower() or "\u65e5\u672c" in language.lower()) and SIMPLIFIED_ONLY_RE.search(prose_target):
        return False
    if different_languages and source_letters >= 4 and copied_source == copied_target:
        return False
    # Keep the stricter version-1 Japanese check for direct callers that do
    # not yet supply a source language.
    return not (
        source_language is None
        and ascii_letters >= 6
        and prose_source.strip() == prose_target.strip()
        and "english" not in language.lower()
    )


def split_long(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        candidates = list(re.finditer(r"\s+|(?<=[.!?;:])", window))
        usable = [match.end() for match in candidates if match.end() >= limit // 2]
        cut = usable[-1] if usable else limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


def load_glossary(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in data.items()):
        raise ValueError("Glossary must be a JSON object of source-to-target strings")
    return data


def make_cache_key(
    record: Record,
    model: str,
    language: str,
    locale: str,
    provider: str = "local",
    source_language: str | None = None,
    source_locale: str | None = None,
) -> str:
    source_prefix = "" if source_language is None and source_locale is None else f"{source_language or ''}\0{source_locale or ''}\0"
    payload = (
        f"{source_prefix}{provider}\0{model}\0{language}\0{locale}\0{record.mod}\0"
        f"{record.relative_file}\0{record.key}\0{record.source}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def collect_records(
    mods: list[ModSpec],
    output: Path,
    locale: str,
    model: str,
    language: str,
    provider: str = "local",
) -> tuple[list[Record], dict[Path, list[str]]]:
    records: list[Record] = []
    destinations: dict[Path, list[str]] = {}
    serial = 0
    target_id = normalize_language_id(locale)
    target_header = locale_header(target_id)
    for mod in mods:
        for source_file in mod.source_files:
            relative = source_file.relative_to(mod.root)
            lines = source_file.read_text(encoding="utf-8-sig").splitlines()
            header_index = next(
                (index for index, line in enumerate(lines) if re.fullmatch(r"\s*l_[a-z0-9_]+\s*:\s*", line, re.IGNORECASE)),
                None,
            )
            output_lines = list(lines) if header_index is not None else [f"{target_header}:", *lines]
            if header_index is not None:
                output_lines[header_index] = f"{target_header}:"
            destination = target_localization_path(
                source_file,
                mod.root,
                output / mod.name,
                mod.source_locale,
                target_id,
            )
            if destination in destinations:
                raise RuntimeError(f"Multiple source files map to the same target: {destination}")
            destinations[destination] = output_lines
            offset = 0 if header_index is not None else 1
            for line_index, line in enumerate(lines):
                if header_index is not None and line_index == header_index:
                    continue
                match = ENTRY_RE.match(line)
                broken = False
                if not match and re.match(r'^\s*[^#\s][^:]*:\d*\s+"', line):
                    match = BROKEN_RE.match(line)
                    broken = bool(match)
                if not match:
                    continue
                record = Record(
                    item_id=f"T{serial:07d}", mod=mod.name,
                    relative_file=relative.as_posix(), key=match.group(2).strip(), source=match.group(3),
                    destination=destination, output_lines=output_lines, output_index=line_index + offset,
                    prefix=match.group(1), suffix='"' if broken else match.group(4),
                )
                record.cache_key = make_cache_key(
                    record,
                    model,
                    language,
                    target_header,
                    provider,
                    mod.source_language,
                    locale_header(mod.source_locale),
                )
                # Version 1 caches can be reused for the original
                # English-to-Japanese direct-directory workflow.
                if mod.source_locale == "english":
                    try:
                        legacy_relative = source_file.relative_to(mod.english_root).as_posix()
                    except ValueError:
                        pass
                    else:
                        original_relative = record.relative_file
                        record.relative_file = legacy_relative
                        record.legacy_cache_key = make_cache_key(
                            record, model, language, target_header, provider
                        )
                        record.relative_file = original_relative
                records.append(record)
                serial += 1
    return records, destinations


def connect_cache(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS translations (
           cache_key TEXT PRIMARY KEY, model TEXT NOT NULL, target_language TEXT NOT NULL,
           locale TEXT NOT NULL, mod TEXT NOT NULL, relative_file TEXT NOT NULL,
           loc_key TEXT NOT NULL, source TEXT NOT NULL, translated TEXT NOT NULL,
           updated_at INTEGER NOT NULL)"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS failures (
           cache_key TEXT PRIMARY KEY, item_id TEXT NOT NULL, error TEXT NOT NULL,
           updated_at INTEGER NOT NULL)"""
    )
    return connection


def build_batches(records: list[Record], max_items: int, max_chars: int, long_threshold: int) -> list[list[Record]]:
    batches: list[list[Record]] = []
    current: list[Record] = []
    size = 0
    for record in records:
        if len(record.source) > long_threshold:
            if current:
                batches.append(current)
                current, size = [], 0
            batches.append([record])
            continue
        item_size = len(record.source) + len(record.key) + 60
        if current and (len(current) >= max_items or size + item_size > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(record)
        size += item_size
    if current:
        batches.append(current)
    return batches


def escape_quotes(text: str) -> str:
    return re.sub(r'(?<!\\)"', r'\\"', text)


def parse_entries(path: Path, allow_broken: bool = False) -> dict[str, str]:
    result: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.strip().startswith("l_") and line.strip().endswith(":"):
            continue
        match = ENTRY_RE.match(line)
        if match:
            key = match.group(2).strip()
            if key in result:
                raise RuntimeError(f"Duplicate key {key}: {path}:{number}")
            result[key] = match.group(3).replace(r'\"', '"')
        elif re.match(r'^\s*[^#\s][^:]*:\d*\s+"', line):
            broken = BROKEN_RE.match(line) if allow_broken else None
            if broken:
                result[broken.group(2).strip()] = broken.group(3)
            else:
                raise RuntimeError(f"Malformed entry: {path}:{number}")
    return result


def validate_staged(mods: list[ModSpec], output: Path, language: str, locale: str) -> tuple[int, int]:
    errors: list[str] = []
    files = 0
    entries_count = 0
    target_id = normalize_language_id(locale)
    target_header = locale_header(target_id)
    for mod in mods:
        for source_file in mod.source_files:
            files += 1
            target = target_localization_path(
                source_file,
                mod.root,
                output / mod.name,
                mod.source_locale,
                target_id,
            )
            if not target.is_file():
                errors.append(f"Missing target file: {target}")
                continue
            if not target.read_bytes().startswith(b"\xef\xbb\xbf"):
                errors.append(f"Missing UTF-8 BOM: {target}")
            lines = target.read_text(encoding="utf-8-sig").splitlines()
            if not any(line.strip() == f"{target_header}:" for line in lines):
                errors.append(f"Wrong header: {target}")
            try:
                source_entries = parse_entries(source_file, allow_broken=True)
                target_entries = parse_entries(target)
            except RuntimeError as exc:
                errors.append(str(exc))
                continue
            entries_count += len(target_entries)
            if set(source_entries) != set(target_entries):
                errors.append(f"Key mismatch: {target}")
            for key in set(source_entries) & set(target_entries):
                if not valid_translation(source_entries[key], target_entries[key], language, mod.source_language):
                    errors.append(f"Invalid translation: {target}:{key}")
    actual_files = sum(len(list((output / mod.name).rglob("*.yml"))) for mod in mods)
    if actual_files != files:
        errors.append(f"File count mismatch: expected {files}, found {actual_files}")
    print(json.dumps({"files": files, "entries": entries_count, "errors": len(errors)}, ensure_ascii=False))
    for error in errors[:100]:
        print("ERROR", error)
    if errors:
        raise SystemExit(1)
    return files, entries_count


def translate_records(
    args: argparse.Namespace,
    records: list[Record],
    destinations: dict[Path, list[str]],
    connection: sqlite3.Connection,
) -> None:
    translated: dict[str, str] = {}
    pending: list[Record] = []
    for record in records:
        row = connection.execute("SELECT translated FROM translations WHERE cache_key=?", (record.cache_key,)).fetchone()
        if row is None and record.legacy_cache_key:
            row = connection.execute(
                "SELECT translated FROM translations WHERE cache_key=?", (record.legacy_cache_key,)
            ).fetchone()
        if row and valid_translation(record.source, row[0], args.language, args.source_language):
            translated[record.item_id] = row[0]
        else:
            pending.append(record)
    batches = build_batches(pending, args.batch_items, args.batch_chars, args.long_threshold)
    print(json.dumps({"entries": len(records), "cache_hits": len(translated), "pending": len(pending), "batches": len(batches)}))
    translator = Translator(args, records)
    completed = 0
    failures: dict[str, str] = {}
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
    completed_normally = False
    try:
        future_map = {executor.submit(translator.translate_resilient, batch): batch for batch in batches}
        for future in concurrent.futures.as_completed(future_map):
            translator.check_cancelled()
            result, failed = future.result()
            now = int(time.time())
            for record in future_map[future]:
                if record.item_id in result:
                    value = result[record.item_id]
                    translated[record.item_id] = value
                    connection.execute(
                        """INSERT INTO translations
                           (cache_key,model,target_language,locale,mod,relative_file,loc_key,source,translated,updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET
                           translated=excluded.translated, updated_at=excluded.updated_at""",
                        (record.cache_key, args.model, args.language, args.locale, record.mod,
                         record.relative_file, record.key, record.source, value, now),
                    )
                    connection.execute("DELETE FROM failures WHERE cache_key=?", (record.cache_key,))
                elif record.item_id in failed:
                    failures[record.item_id] = failed[record.item_id]
                    connection.execute(
                        """INSERT INTO failures(cache_key,item_id,error,updated_at) VALUES(?,?,?,?)
                           ON CONFLICT(cache_key) DO UPDATE SET error=excluded.error, updated_at=excluded.updated_at""",
                        (record.cache_key, record.item_id, failed[record.item_id][:2000], now),
                    )
            connection.commit()
            completed += 1
            if completed % 10 == 0 or completed == len(batches):
                print(json.dumps({"completed_batches": completed, "total_batches": len(batches), "translated": len(translated), "failures": len(failures)}))
        completed_normally = True
    finally:
        if not completed_normally:
            for future in locals().get("future_map", {}):
                future.cancel()
        executor.shutdown(wait=True, cancel_futures=not completed_normally)
    missing = [record for record in records if record.item_id not in translated]
    if missing:
        print(f"Translation incomplete: {len(missing)} entries remain. Rerun with the same cache after adjusting batch/segment settings.", file=sys.stderr)
        for record in missing[:30]:
            print(f"MISSING {record.item_id} {record.mod}:{record.relative_file}:{record.key}", file=sys.stderr)
            if record.item_id in failures:
                print(f"REASON {record.item_id}: {failures[record.item_id]}", file=sys.stderr)
        raise SystemExit(2)
    for record in records:
        record.output_lines[record.output_index] = f"{record.prefix}{escape_quotes(translated[record.item_id])}{record.suffix}"
    for destination, lines in destinations.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def command_translate(args: argparse.Namespace) -> None:
    source_locale = getattr(args, "source_locale", "l_english")
    source_language = getattr(args, "source_language", "English")
    args.source_locale = source_locale
    args.source_language = source_language
    mods = [parse_mod(value, source_locale, source_language) for value in args.mod]
    output = Path(args.output).expanduser().resolve()
    records, destinations = collect_records(
        mods, output, args.locale, args.model, args.language, getattr(args, "provider", "local")
    )
    connection = connect_cache(Path(args.cache).expanduser().resolve())
    try:
        translate_records(args, records, destinations, connection)
    finally:
        connection.close()
    validate_staged(mods, output, args.language, args.locale)
    print(f"Wrote {len(destinations)} files to {output}")


def command_validate(args: argparse.Namespace) -> None:
    source_locale = getattr(args, "source_locale", "l_english")
    source_language = getattr(args, "source_language", "English")
    mods = [parse_mod(value, source_locale, source_language) for value in args.mod]
    validate_staged(mods, Path(args.output).expanduser().resolve(), args.language, args.locale)
    print("VALIDATION PASSED")


def parse_mapping(value: str) -> tuple[Path, Path]:
    if "=" not in value:
        raise ValueError("Install mapping must be STAGED_MOD_ROOT=INSTALLED_MOD_ROOT")
    staged, installed = value.split("=", 1)
    return Path(staged).expanduser().resolve(), Path(installed).expanduser().resolve()


def is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return child != parent
    except ValueError:
        return False


def command_install(args: argparse.Namespace) -> None:
    suffix = locale_id(args.locale)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for raw in args.mapping:
        staged_root, installed_root = parse_mapping(raw)
        if not installed_root.is_dir():
            raise FileNotFoundError(installed_root)
        staged_locale = staged_root / "localization" / suffix
        target = installed_root / "localization" / suffix
        backup = installed_root / "_translation_backups" / f"{suffix}_{stamp}"
        if not staged_locale.is_dir():
            raise FileNotFoundError(staged_locale)
        if not is_inside(target.resolve(strict=False), installed_root) or not is_inside(backup.resolve(strict=False), installed_root):
            raise RuntimeError(f"Unsafe install path for {installed_root}")
        if backup.exists():
            raise FileExistsError(backup)
        if target.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(backup))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staged_locale, target)
        print(json.dumps({"installed": str(target), "backup": str(backup) if backup.exists() else None}, ensure_ascii=False))


def add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mod", action="append", required=True, help="[NAME=]path to a mod root; repeat for multiple mods")
    parser.add_argument("--output", required=True, help="staging output root")
    parser.add_argument("--language", required=True, help="human language name, for example Japanese")
    parser.add_argument("--locale", required=True, help="CK3 locale header, for example l_japanese")
    parser.add_argument("--source-language", default="English", help="actual language of the source text")
    parser.add_argument("--source-locale", default="l_english", help="CK3 locale/header containing the source text")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Translate and safely install CK3 mod localization with a local or explicitly selected API provider")
    subparsers = parser.add_subparsers(dest="command", required=True)
    translate = subparsers.add_parser("translate", help="translate to a staged output tree and validate it")
    add_source_args(translate)
    translate.add_argument("--cache", required=True, help="SQLite translation-memory path")
    translate.add_argument("--endpoint", help="provider chat-completions endpoint; uses the selected provider default when omitted")
    translate.add_argument("--provider", choices=tuple(PROVIDERS), default="local")
    translate.add_argument(
        "--model",
        required=True,
        help="exact provider model id; LM Studio may JIT-load a downloaded model",
    )
    translate.add_argument("--api-key-env", help="optional environment variable containing a bearer token")
    translate.add_argument("--glossary", help="UTF-8 JSON source-to-target glossary")
    translate.add_argument("--extra-instructions", help="UTF-8 text file appended to the system prompt")
    translate.add_argument("--workers", type=int, default=4)
    translate.add_argument("--batch-items", type=int, default=8)
    translate.add_argument("--batch-chars", type=int, default=5000)
    translate.add_argument("--long-threshold", type=int, default=800)
    translate.add_argument("--long-segment", type=int, default=600)
    translate.add_argument("--retries", type=int, default=4)
    translate.add_argument("--timeout", type=int, default=180)
    translate.add_argument("--max-tokens", type=int, default=8000)
    translate.add_argument("--temperature", type=float, default=0.2)
    translate.add_argument("--min-interval", type=float, default=0.0)
    translate.set_defaults(func=command_translate)

    validate = subparsers.add_parser("validate", help="validate a staged output tree without contacting a model")
    add_source_args(validate)
    validate.set_defaults(func=command_validate)

    install = subparsers.add_parser("install", help="back up and install validated locale directories")
    install.add_argument("--mapping", action="append", required=True, help="STAGED_MOD_ROOT=INSTALLED_MOD_ROOT; repeat as needed")
    install.add_argument("--locale", required=True, help="CK3 locale header, for example l_japanese")
    install.set_defaults(func=command_install)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "translate" and not args.endpoint:
        args.endpoint = get_provider(args.provider).chat_endpoint
    args.func(args)


if __name__ == "__main__":
    main()
