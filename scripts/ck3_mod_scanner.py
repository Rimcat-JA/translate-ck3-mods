#!/usr/bin/env python3
"""Discover, validate, and classify CK3 mods without using an LLM."""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
from collections import Counter
from pathlib import Path

from ck3_languages import (
    AUTO_LANGUAGE_ID,
    LANGUAGES,
    infer_locale_id,
    language_spec,
    normalize_language_id,
)

DESCRIPTOR_VALUE_RE = r'^\s*{key}\s*=\s*"((?:\\.|[^"])*)"'
ENTRY_RE = re.compile(r'^(\s*([^#\s][^:]*):\d*\s+")(.*)("\s*(?:#.*)?)$')
BROKEN_ENTRY_RE = re.compile(r'^(\s*([^#\s][^:]*):\d*\s+")(.*)$')
TOKEN_RE = re.compile(
    r'\\[ntr]|\[[^\[\]\r\n]*\]|\$[^$\r\n]+\$|@[A-Za-z0-9_./:-]+!|#!|#[A-Za-z0-9_]+|\{[^{}\r\n]*\}'
)
RECOGNIZED_CONTENT_DIRS = {
    "common",
    "content_source",
    "data_binding",
    "dlc",
    "dlc_metadata",
    "events",
    "fonts",
    "gfx",
    "gui",
    "history",
    "interface",
    "jomini",
    "localization",
    "map_data",
    "music",
    "sound",
}
MAX_DETECTION_CHARS = 250_000

LATIN_WORDS: dict[str, set[str]] = {
    "english": {
        "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "character", "do",
        "does", "event", "for", "from", "has", "have", "if", "in", "is", "it", "its", "like", "no",
        "not", "of", "on", "only", "or", "other", "realm", "ruler", "same", "some", "than", "that",
        "the", "then", "there", "these", "they", "this", "to", "use", "war", "was", "were", "what",
        "when", "which", "who", "will", "with", "would", "you", "your",
    },
    "french": {
        "le", "la", "les", "de", "des", "du", "et", "est", "vous", "votre", "avec", "pour", "dans",
        "une", "un", "que", "qui", "sur", "ce", "cette", "royaume", "guerre",
    },
    "german": {
        "der", "die", "das", "den", "dem", "des", "und", "ist", "sind", "mit", "für", "von", "zu",
        "ein", "eine", "einer", "auf", "nicht", "euer", "reich", "krieg", "wird",
    },
    "spanish": {
        "el", "la", "los", "las", "de", "del", "y", "es", "son", "con", "para", "por", "una", "un",
        "que", "en", "tu", "reino", "guerra", "esta", "este",
    },
    "italian": {
        "il", "lo", "la", "gli", "le", "di", "del", "e", "è", "con", "per", "una", "un", "che",
        "nel", "non", "tuo", "regno", "guerra", "questo",
    },
    "portuguese": {
        "o", "a", "os", "as", "de", "do", "da", "e", "é", "com", "para", "por", "uma", "um", "que",
        "em", "não", "seu", "reino", "guerra", "esta",
    },
    "polish": {
        "i", "z", "do", "na", "jest", "nie", "się", "że", "dla", "ten", "ta", "twoje", "królestwo",
        "wojna", "przez", "jako", "może",
    },
    "turkish": {
        "ve", "bir", "bu", "için", "ile", "de", "da", "olan", "değil", "senin", "krallık", "savaş",
        "olarak", "daha", "var",
    },
    "dutch": {
        "de", "het", "een", "en", "van", "voor", "met", "is", "zijn", "niet", "dat", "dit", "jouw",
        "rijk", "oorlog", "wordt", "op",
    },
    "czech": {
        "a", "je", "jsou", "se", "pro", "s", "na", "že", "není", "tento", "tvoje", "říše", "válka",
        "jako", "může",
    },
    "hungarian": {
        "és", "egy", "a", "az", "van", "nem", "hogy", "számára", "ezzel", "te", "birodalom", "háború",
        "mint", "lehet",
    },
}

SIMPLIFIED_HINTS = set("这们说从还进发见听经过么给让较种样无间开关门风云电爱亲许处实认觉现压击离东书车马战线总义气")
TRADITIONAL_HINTS = set("這們說從還進發見聽經過麼給讓較種樣無間開關門風雲電愛親許處實認覺現壓擊離東書車馬戰線總義氣")


@dataclasses.dataclass(frozen=True)
class DescriptorInfo:
    path: Path
    name: str
    version: str
    supported_version: str
    path_value: str | None
    archive_value: str | None
    remote_file_id: str | None


@dataclasses.dataclass(frozen=True)
class LocalizationInfo:
    locale_id: str
    stored_as: str
    detected_language_id: str
    detected_language: str
    confidence: float
    files: tuple[Path, ...]
    entry_count: int
    translatable_entries: int
    malformed_entries: int
    character_count: int
    evidence: str

    @property
    def is_linguistic(self) -> bool:
        return self.translatable_entries > 0 and self.detected_language_id != "non_linguistic"


@dataclasses.dataclass(frozen=True)
class ModCandidate:
    candidate_id: str
    root: Path | None
    descriptor: Path | None
    name: str
    version: str
    supported_version: str
    valid: bool
    reason: str
    warnings: tuple[str, ...]
    localizations: tuple[LocalizationInfo, ...]
    origin: str

    @property
    def is_non_linguistic(self) -> bool:
        return self.valid and not any(item.is_linguistic for item in self.localizations)

    def choose_source(self, requested_language_id: str, target_language_id: str) -> LocalizationInfo | None:
        usable = [item for item in self.localizations if item.is_linguistic]
        if not usable:
            return None
        target = normalize_language_id(target_language_id)
        if requested_language_id != AUTO_LANGUAGE_ID:
            requested = normalize_language_id(requested_language_id)
            matches = [item for item in usable if item.detected_language_id == requested]
            if matches:
                return max(matches, key=lambda item: (item.translatable_entries, item.character_count))
            low_confidence = [item for item in usable if item.confidence < 0.5]
            if len(low_confidence) == 1:
                # The explicit source language is the user's correction of
                # uncertain text detection; retain this group's stored locale.
                return low_confidence[0]
            return None
        alternatives = [item for item in usable if item.detected_language_id != target]
        pool = alternatives or usable
        english = [item for item in pool if item.detected_language_id == "english"]
        if english:
            return max(english, key=lambda item: (item.translatable_entries, item.character_count))
        return max(pool, key=lambda item: (item.translatable_entries, item.character_count))


def descriptor_value(text: str, key: str) -> str | None:
    match = re.search(DESCRIPTOR_VALUE_RE.format(key=re.escape(key)), text, flags=re.MULTILINE)
    return match.group(1).replace(r'\"', '"') if match else None


def parse_descriptor(path: Path) -> DescriptorInfo:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return DescriptorInfo(
        path=path.resolve(),
        name=descriptor_value(text, "name") or path.stem,
        version=descriptor_value(text, "version") or "—",
        supported_version=descriptor_value(text, "supported_version") or "—",
        path_value=descriptor_value(text, "path"),
        archive_value=descriptor_value(text, "archive"),
        remote_file_id=descriptor_value(text, "remote_file_id"),
    )


def resolve_descriptor_root(descriptor: DescriptorInfo) -> tuple[Path | None, str | None]:
    if descriptor.archive_value:
        return None, "Archived mods must be extracted before translation."
    if descriptor.path.name.lower() == "descriptor.mod":
        return descriptor.path.parent.resolve(), None
    if not descriptor.path_value:
        return None, "The .mod descriptor has no path entry."
    raw = descriptor.path_value.replace("\\\\", "\\")
    value = Path(raw).expanduser()
    candidates: list[Path]
    if value.is_absolute():
        candidates = [value]
    else:
        candidates = [descriptor.path.parent / value]
        if value.parts and value.parts[0].lower() == "mod":
            candidates.insert(0, descriptor.path.parent.parent / value)
        candidates.append(descriptor.path.parent / value.name)
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_dir():
            return resolved, None
    return None, f"The descriptor path does not exist: {descriptor.path_value}"


def visible_text(value: str) -> str:
    text = TOKEN_RE.sub(" ", value)
    text = re.sub(r"\\[ntr]", " ", text)
    text = re.sub(r"[^\w\u00c0-\u024f\u3040-\u30ff\u3400-\u9fff\u0400-\u04ff\uac00-\ud7af'’-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_text_language(text: str, expected_locale_id: str | None = None) -> tuple[str, float, str]:
    letters = [character for character in text if character.isalpha()]
    if len(letters) < 3:
        return "non_linguistic", 1.0, "no natural-language text"
    total = len(letters)
    kana = len(re.findall(r"[\u3040-\u30ff]", text))
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    hangul = len(re.findall(r"[\uac00-\ud7af]", text))
    cyrillic = len(re.findall(r"[\u0400-\u04ff]", text))
    if kana >= 3 and (kana + cjk) / total >= 0.12:
        return "japanese", min(0.99, 0.72 + (kana + cjk) / max(total, 1) * 0.25), "Japanese kana/CJK script"
    if hangul >= 3 and hangul / total >= 0.12:
        return "korean", min(0.99, 0.72 + hangul / max(total, 1) * 0.25), "Korean Hangul script"
    if cyrillic >= 3 and cyrillic / total >= 0.12:
        ukrainian = len(re.findall(r"[іїєґІЇЄҐ]", text))
        language_id = "ukrainian" if ukrainian >= 2 else "russian"
        return language_id, min(0.99, 0.72 + cyrillic / max(total, 1) * 0.25), "Cyrillic script"
    if cjk >= 3 and cjk / total >= 0.12:
        simplified = sum(character in SIMPLIFIED_HINTS for character in text)
        traditional = sum(character in TRADITIONAL_HINTS for character in text)
        language_id = "traditional_chinese" if traditional > simplified else "simp_chinese"
        return language_id, min(0.96, 0.68 + cjk / max(total, 1) * 0.25), "Chinese CJK script"

    words = re.findall(r"[^\W\d_]+", text.casefold(), flags=re.UNICODE)
    counts = Counter(words)
    scores = {
        language_id: sum(counts[word] for word in vocabulary)
        for language_id, vocabulary in LATIN_WORDS.items()
    }
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_id, best_score = ranked[0]
    second_score = ranked[1][1]
    if best_score >= 2:
        margin = best_score - second_score
        confidence = min(0.96, 0.55 + best_score / max(len(words), 12) * 2.5 + margin * 0.03)
        return best_id, confidence, "Latin-script function words"
    if expected_locale_id:
        expected = normalize_language_id(expected_locale_id)
        if expected in {language.language_id for language in LANGUAGES}:
            return expected, 0.42, "text script plus localization metadata"
    return "english", 0.32, "Latin script; low-confidence English fallback"


def analyze_localization_group(locale_id: str, files: list[Path]) -> LocalizationInfo:
    entry_count = 0
    translatable_entries = 0
    malformed_entries = 0
    character_count = 0
    samples: list[str] = []
    sampled = 0
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = ENTRY_RE.match(line)
            if not match:
                match = BROKEN_ENTRY_RE.match(line)
                if not match:
                    continue
                malformed_entries += 1
            entry_count += 1
            visible = visible_text(match.group(3))
            letters = sum(character.isalpha() for character in visible)
            if letters < 2:
                continue
            translatable_entries += 1
            character_count += len(visible)
            if sampled < MAX_DETECTION_CHARS:
                chunk = visible[: MAX_DETECTION_CHARS - sampled]
                samples.append(chunk)
                sampled += len(chunk)
    detected_id, confidence, evidence = detect_text_language(" ".join(samples), locale_id)
    detected_name = "Non-linguistic" if detected_id == "non_linguistic" else language_spec(detected_id).display_name
    return LocalizationInfo(
        locale_id=locale_id,
        stored_as=f"l_{locale_id}",
        detected_language_id=detected_id,
        detected_language=detected_name,
        confidence=confidence,
        files=tuple(sorted(files, key=lambda path: path.as_posix().casefold())),
        entry_count=entry_count,
        translatable_entries=translatable_entries,
        malformed_entries=malformed_entries,
        character_count=character_count,
        evidence=evidence,
    )


def analyze_localizations(root: Path) -> tuple[tuple[LocalizationInfo, ...], tuple[str, ...]]:
    localization = root / "localization"
    if not localization.is_dir():
        return (), ()
    grouped: dict[str, list[Path]] = {}
    unclassified = 0
    for path in sorted(localization.rglob("*.yml"), key=lambda item: item.as_posix().casefold()):
        locale_id = infer_locale_id(path, localization)
        if locale_id is None:
            unclassified += 1
            continue
        grouped.setdefault(locale_id, []).append(path)
    results = tuple(analyze_localization_group(locale_id, files) for locale_id, files in sorted(grouped.items()))
    warnings_list: list[str] = []
    if unclassified:
        warnings_list.append(f"{unclassified} localization file(s) have no recognizable locale.")
    malformed = sum(item.malformed_entries for item in results)
    if malformed:
        warnings_list.append(f"{malformed} localization entry or entries have a missing closing quote; output will be repaired.")
    warnings = tuple(warnings_list)
    return results, warnings


def looks_like_mod_content(root: Path) -> bool:
    try:
        entries = list(root.iterdir())
    except OSError:
        return False
    return any(entry.is_dir() and entry.name.casefold() in RECOGNIZED_CONTENT_DIRS for entry in entries)


def candidate_key(root: Path | None, descriptor: Path | None) -> str:
    value = str(root or descriptor or "unknown").casefold()
    return value


def invalid_candidate(path: Path, reason: str, origin: str) -> ModCandidate:
    return ModCandidate(
        candidate_id=candidate_key(None, path),
        root=None,
        descriptor=path,
        name=path.stem,
        version="—",
        supported_version="—",
        valid=False,
        reason=reason,
        warnings=(),
        localizations=(),
        origin=origin,
    )


def analyze_mod(root: Path, descriptor_path: Path, origin: str) -> ModCandidate:
    try:
        descriptor = parse_descriptor(descriptor_path)
    except OSError as exc:
        return invalid_candidate(descriptor_path, f"Cannot read descriptor: {exc}", origin)
    resolved = root.resolve()
    internal = resolved / "descriptor.mod"
    if not internal.is_file():
        return ModCandidate(
            candidate_id=candidate_key(resolved, descriptor_path),
            root=resolved,
            descriptor=descriptor_path.resolve(),
            name=descriptor.name,
            version=descriptor.version,
            supported_version=descriptor.supported_version,
            valid=False,
            reason="Missing descriptor.mod inside the mod folder.",
            warnings=(),
            localizations=(),
            origin=origin,
        )
    try:
        internal_info = parse_descriptor(internal)
    except OSError as exc:
        return invalid_candidate(internal, f"Cannot read descriptor.mod: {exc}", origin)
    if not looks_like_mod_content(resolved):
        return ModCandidate(
            candidate_id=candidate_key(resolved, descriptor_path),
            root=resolved,
            descriptor=descriptor_path.resolve(),
            name=descriptor.name or internal_info.name,
            version=descriptor.version if descriptor.version != "—" else internal_info.version,
            supported_version=descriptor.supported_version,
            valid=False,
            reason="The folder has no recognizable CK3 mod content.",
            warnings=(),
            localizations=(),
            origin=origin,
        )
    localizations, warnings = analyze_localizations(resolved)
    return ModCandidate(
        candidate_id=candidate_key(resolved, descriptor_path),
        root=resolved,
        descriptor=descriptor_path.resolve(),
        name=descriptor.name or internal_info.name,
        version=descriptor.version if descriptor.version != "—" else internal_info.version,
        supported_version=(
            descriptor.supported_version if descriptor.supported_version != "—" else internal_info.supported_version
        ),
        valid=True,
        reason="Valid CK3 mod structure.",
        warnings=warnings,
        localizations=localizations,
        origin=origin,
    )


def scan_descriptor(path: Path) -> ModCandidate:
    descriptor_path = path.expanduser().resolve()
    if not descriptor_path.is_file() or descriptor_path.suffix.lower() != ".mod":
        return invalid_candidate(descriptor_path, "Select a CK3 .mod descriptor file.", "descriptor")
    try:
        descriptor = parse_descriptor(descriptor_path)
    except OSError as exc:
        return invalid_candidate(descriptor_path, f"Cannot read descriptor: {exc}", "descriptor")
    root, error = resolve_descriptor_root(descriptor)
    if error or root is None:
        return invalid_candidate(descriptor_path, error or "Cannot resolve the mod folder.", "descriptor")
    return analyze_mod(root, descriptor_path, "descriptor")


def scan_mod_folder(path: Path) -> ModCandidate:
    root = path.expanduser().resolve()
    if not root.is_dir():
        return invalid_candidate(root, "The selected mod folder does not exist.", "folder")
    descriptor = root / "descriptor.mod"
    if not descriptor.is_file():
        return ModCandidate(
            candidate_id=candidate_key(root, None),
            root=root,
            descriptor=None,
            name=root.name,
            version="—",
            supported_version="—",
            valid=False,
            reason="Missing descriptor.mod inside the mod folder.",
            warnings=(),
            localizations=(),
            origin="folder",
        )
    return analyze_mod(root, descriptor, "folder")


def scan_mod_library(path: Path) -> list[ModCandidate]:
    parent = path.expanduser().resolve()
    if not parent.is_dir():
        return [invalid_candidate(parent, "The selected library folder does not exist.", "library")]
    try:
        entries = list(parent.iterdir())
    except OSError as exc:
        return [invalid_candidate(parent, f"Cannot read the selected library folder: {exc}", "library")]
    external_descriptors = sorted(
        (
            item
            for item in entries
            if item.is_file() and item.suffix.casefold() == ".mod" and item.name.casefold() != "descriptor.mod"
        ),
        key=lambda item: item.name.casefold(),
    )
    if not external_descriptors and (parent / "descriptor.mod").is_file():
        return [scan_mod_folder(parent)]
    results: list[ModCandidate] = []
    seen_roots: set[str] = set()
    for descriptor in external_descriptors:
        candidate = scan_descriptor(descriptor)
        key = str(candidate.root).casefold() if candidate.root else candidate.candidate_id
        if key not in seen_roots:
            results.append(candidate)
            seen_roots.add(key)
    for child in sorted((item for item in entries if item.is_dir()), key=lambda item: item.name.casefold()):
        if child.name.startswith("_") or child.name.startswith("."):
            continue
        key = str(child.resolve()).casefold()
        if key in seen_roots:
            continue
        descriptor = child / "descriptor.mod"
        if descriptor.is_file():
            candidate = scan_mod_folder(child)
            results.append(candidate)
            seen_roots.add(key)
        elif looks_like_mod_content(child):
            results.append(scan_mod_folder(child))
            seen_roots.add(key)
    return sorted(results, key=lambda candidate: (not candidate.valid, candidate.name.casefold(), candidate.candidate_id))


def candidate_to_json(candidate: ModCandidate) -> dict[str, object]:
    return {
        "id": candidate.candidate_id,
        "name": candidate.name,
        "root": str(candidate.root) if candidate.root else None,
        "descriptor": str(candidate.descriptor) if candidate.descriptor else None,
        "version": candidate.version,
        "supported_version": candidate.supported_version,
        "valid": candidate.valid,
        "reason": candidate.reason,
        "warnings": list(candidate.warnings),
        "non_linguistic": candidate.is_non_linguistic,
        "localizations": [
            {
                "locale": item.locale_id,
                "stored_as": item.stored_as,
                "detected_language": item.detected_language,
                "detected_language_id": item.detected_language_id,
                "confidence": round(item.confidence, 3),
                "files": len(item.files),
                "entries": item.entry_count,
                "translatable_entries": item.translatable_entries,
                "malformed_entries": item.malformed_entries,
                "evidence": item.evidence,
            }
            for item in candidate.localizations
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover CK3 mods, validate their structure, and detect localization languages")
    parser.add_argument("path", help="CK3 mod library folder, one mod folder, or one .mod descriptor")
    parser.add_argument("--single", action="store_true", help="treat a folder as one mod instead of a library")
    args = parser.parse_args()
    path = Path(args.path)
    if path.suffix.lower() == ".mod":
        results = [scan_descriptor(path)]
    elif args.single:
        results = [scan_mod_folder(path)]
    else:
        results = scan_mod_library(path)
    print(json.dumps([candidate_to_json(candidate) for candidate in results], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
