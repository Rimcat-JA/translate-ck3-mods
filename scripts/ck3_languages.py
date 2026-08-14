"""CK3 language metadata and localization-file layout helpers."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

AUTO_LANGUAGE_ID = "auto"
LANGUAGE_ID_RE = re.compile(r"[a-z0-9_]+")
HEADER_RE = re.compile(r"^\s*l_([a-z0-9_]+)\s*:\s*$", re.MULTILINE | re.IGNORECASE)
FILE_LOCALE_RE = re.compile(r"_l_([a-z0-9_]+)\.yml$", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class LanguageSpec:
    language_id: str
    display_name: str
    llm_name: str
    aliases: tuple[str, ...] = ()

    @property
    def locale(self) -> str:
        return f"l_{self.language_id}"


LANGUAGES: tuple[LanguageSpec, ...] = (
    LanguageSpec("english", "English", "English"),
    LanguageSpec("japanese", "Japanese", "Japanese"),
    LanguageSpec("french", "French", "French"),
    LanguageSpec("german", "German", "German"),
    LanguageSpec("spanish", "Spanish", "Spanish"),
    LanguageSpec("russian", "Russian", "Russian"),
    LanguageSpec("simp_chinese", "Simplified Chinese", "Simplified Chinese", ("simplified_chinese", "chinese")),
    LanguageSpec("traditional_chinese", "Traditional Chinese", "Traditional Chinese", ("trad_chinese",)),
    LanguageSpec("korean", "Korean", "Korean"),
    LanguageSpec("polish", "Polish", "Polish"),
    LanguageSpec("italian", "Italian", "Italian"),
    LanguageSpec("portuguese", "Portuguese", "Portuguese", ("braz_por", "brazilian_portuguese")),
    LanguageSpec("turkish", "Turkish", "Turkish"),
    LanguageSpec("dutch", "Dutch", "Dutch"),
    LanguageSpec("czech", "Czech", "Czech"),
    LanguageSpec("hungarian", "Hungarian", "Hungarian"),
    LanguageSpec("ukrainian", "Ukrainian", "Ukrainian"),
)

_BY_ID: dict[str, LanguageSpec] = {}
for _language in LANGUAGES:
    _BY_ID[_language.language_id] = _language
    for _alias in _language.aliases:
        _BY_ID[_alias] = _language


def normalize_language_id(value: str) -> str:
    normalized = value.strip().lower().removeprefix("l_")
    normalized = _BY_ID.get(normalized, LanguageSpec(normalized, normalized, normalized)).language_id
    if not normalized or not LANGUAGE_ID_RE.fullmatch(normalized):
        raise ValueError(f"Invalid CK3 language id: {value!r}")
    return normalized


def language_spec(value: str) -> LanguageSpec:
    language_id = normalize_language_id(value)
    known = _BY_ID.get(language_id)
    if known is not None:
        return known
    display = language_id.replace("_", " ").title()
    return LanguageSpec(language_id, display, display)


def locale_header(language_id: str) -> str:
    return f"l_{normalize_language_id(language_id)}"


def read_locale_header(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            sample = handle.read(8192)
    except OSError:
        return None
    match = HEADER_RE.search(sample)
    return normalize_language_id(match.group(1)) if match else None


def infer_locale_id(path: Path, localization_root: Path) -> str | None:
    header = read_locale_header(path)
    if header:
        return header
    filename = FILE_LOCALE_RE.search(path.name)
    if filename:
        return normalize_language_id(filename.group(1))
    try:
        parts = path.relative_to(localization_root).parts[:-1]
    except ValueError:
        return None
    for part in parts:
        normalized = part.lower()
        if normalized in _BY_ID:
            return _BY_ID[normalized].language_id
    return None


def discover_locale_files(mod_root: Path, language_id: str) -> tuple[Path, ...]:
    requested = normalize_language_id(language_id)
    localization = mod_root / "localization"
    if not localization.is_dir():
        return ()
    files = [
        path
        for path in localization.rglob("*.yml")
        if path.is_file() and infer_locale_id(path, localization) == requested
    ]
    return tuple(sorted(files, key=lambda item: item.relative_to(mod_root).as_posix().casefold()))


def target_relative_path(source_file: Path, mod_root: Path, source_language_id: str, target_language_id: str) -> Path:
    localization = mod_root / "localization"
    relative = source_file.relative_to(localization)
    source_id = normalize_language_id(source_language_id)
    target_id = normalize_language_id(target_language_id)
    parts = list(relative.parts)
    for index, part in enumerate(parts[:-1]):
        try:
            if normalize_language_id(part) == source_id:
                parts[index] = f"l_{target_id}" if part.casefold().startswith("l_") else target_id
        except ValueError:
            continue
    name = parts[-1]
    match = FILE_LOCALE_RE.search(name)
    if match and normalize_language_id(match.group(1)) == source_id:
        name = name[: match.start()] + f"_l_{target_id}.yml"
    elif target_id != source_id:
        name = Path(name).stem + f"_l_{target_id}.yml"
    parts[-1] = name
    return Path(*parts)


def target_localization_path(
    source_file: Path,
    mod_root: Path,
    staged_mod_root: Path,
    source_language_id: str,
    target_language_id: str,
) -> Path:
    return staged_mod_root / "localization" / target_relative_path(
        source_file, mod_root, source_language_id, target_language_id
    )
