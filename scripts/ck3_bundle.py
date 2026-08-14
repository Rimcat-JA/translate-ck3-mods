#!/usr/bin/env python3
"""Deterministically combine staged CK3 localizations into one distributable mod.

This module never contacts a network service or an LLM.  Its input must already
have been translated and validated by ck3_localize.py.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
import zipfile
from pathlib import Path

ENTRY_RE = re.compile(r'^(\s*([^#\s][^:]*):\d*\s+")(.*)("\s*(?:#.*)?)$')
SAFE_ID_RE = re.compile(r"[^a-z0-9._-]+")


@dataclasses.dataclass(frozen=True)
class Source:
    name: str
    root: Path
    slug: str


@dataclasses.dataclass(frozen=True)
class Entry:
    key: str
    value: str
    raw_line: str
    source: Source
    relative_file: str
    line: int


def locale_id(locale: str) -> str:
    if not re.fullmatch(r"l_[a-z0-9_]+", locale):
        raise ValueError(f"Invalid CK3 locale: {locale}")
    return locale[2:]


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    slug = SAFE_ID_RE.sub("-", normalized).strip("-._")
    return slug or hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def parse_source(raw: str) -> Source:
    if "=" in raw:
        name, path = raw.split("=", 1)
    else:
        path = raw
        name = Path(path).name
    root = Path(path).expanduser().resolve()
    if not name.strip():
        raise ValueError(f"Empty source name: {raw}")
    if not root.is_dir():
        raise FileNotFoundError(root)
    return Source(name.strip(), root, slugify(name.strip()))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_source(source: Source, locale: str) -> tuple[list[Entry], list[dict[str, object]]]:
    folder = source.root / "localization" / locale_id(locale)
    if not folder.is_dir():
        raise FileNotFoundError(f"Missing staged locale directory: {folder}")
    entries: list[Entry] = []
    files: list[dict[str, object]] = []
    paths = sorted(folder.rglob("*.yml"), key=lambda path: path.as_posix().casefold())
    if not paths:
        raise RuntimeError(f"No localization files in {folder}")
    for path in paths:
        raw = path.read_bytes()
        if not raw.startswith(b"\xef\xbb\xbf"):
            raise RuntimeError(f"Missing UTF-8 BOM: {path}")
        lines = raw.decode("utf-8-sig").splitlines()
        if not lines or lines[0].strip() != f"{locale}:":
            raise RuntimeError(f"Wrong locale header: {path}")
        relative = path.relative_to(source.root).as_posix()
        count = 0
        for number, line in enumerate(lines[1:], 2):
            match = ENTRY_RE.match(line)
            if match:
                entries.append(Entry(match.group(2).strip(), match.group(3), line, source, relative, number))
                count += 1
            elif re.match(r'^\s*[^#\s][^:]*:\d*\s+"', line):
                raise RuntimeError(f"Malformed localization entry: {path}:{number}")
        files.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest(), "entries": count})
    return entries, files


def select_entries(entries: list[Entry], policy: str) -> tuple[list[Entry], list[dict[str, object]]]:
    selected: dict[str, Entry] = {}
    collisions: list[dict[str, object]] = []
    for entry in entries:
        previous = selected.get(entry.key)
        if previous is None:
            selected[entry.key] = entry
            continue
        identical = previous.value == entry.value
        collision = {
            "key": entry.key,
            "identical": identical,
            "kept_source": previous.source.name,
            "other_source": entry.source.name,
        }
        if identical:
            collisions.append(collision)
            continue
        if policy == "error":
            raise RuntimeError(
                f"Conflicting key {entry.key!r}: {previous.source.name}/{previous.relative_file}:{previous.line} "
                f"and {entry.source.name}/{entry.relative_file}:{entry.line}. "
                "Set collision_policy to first or last only after reviewing the conflict."
            )
        if policy == "last":
            selected[entry.key] = entry
            collision["kept_source"] = entry.source.name
        collisions.append(collision)
    return list(selected.values()), collisions


def quote_descriptor(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def descriptor_text(name: str, version: str, supported: str | None, dependencies: list[str], path: str | None) -> str:
    lines = [f'name="{quote_descriptor(name)}"', f'version="{quote_descriptor(version)}"', 'tags={', '    "Translation"', '}']
    if supported:
        lines.append(f'supported_version="{quote_descriptor(supported)}"')
    if dependencies:
        lines.append("dependencies={")
        lines.extend(f'    "{quote_descriptor(dependency)}"' for dependency in dependencies)
        lines.append("}")
    if path:
        lines.append(f'path="{quote_descriptor(path)}"')
    return "\n".join(lines) + "\n"


def write_utf8_bom(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))


def write_deterministic_zip(path: Path, mod_root: Path, launcher: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        items = [(launcher, launcher.name), *((item, (Path(mod_root.name) / item.relative_to(mod_root)).as_posix()) for item in mod_root.rglob("*") if item.is_file())]
        for item, relative in sorted(items, key=lambda pair: pair[1].casefold()):
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, item.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def validate_built(mod_root: Path, launcher: Path, archive: Path | None, locale: str, expected: dict[str, str]) -> None:
    if not (mod_root / "descriptor.mod").is_file() or not launcher.is_file():
        raise RuntimeError("Descriptor generation failed")
    actual: dict[str, str] = {}
    for path in sorted((mod_root / "localization" / locale_id(locale)).glob("*.yml")):
        if not path.read_bytes().startswith(b"\xef\xbb\xbf"):
            raise RuntimeError(f"Missing BOM in built file: {path}")
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        if not lines or lines[0].strip() != f"{locale}:":
            raise RuntimeError(f"Wrong header in built file: {path}")
        for number, line in enumerate(lines[1:], 2):
            match = ENTRY_RE.match(line)
            if match:
                key = match.group(2).strip()
                if key in actual:
                    raise RuntimeError(f"Duplicate key in built mod: {key}")
                actual[key] = match.group(3)
            elif re.match(r'^\s*[^#\s][^:]*:\d*\s+"', line):
                raise RuntimeError(f"Malformed built entry: {path}:{number}")
    if actual != expected:
        raise RuntimeError("Built localization content does not match selected input entries")
    if archive:
        expected_names = {launcher.name, *(f"{mod_root.name}/{p.relative_to(mod_root).as_posix()}" for p in mod_root.rglob("*") if p.is_file())}
        with zipfile.ZipFile(archive) as handle:
            if set(handle.namelist()) != expected_names or handle.testzip() is not None:
                raise RuntimeError("ZIP verification failed")


def move_existing(destination: Path, bundle_id: str, targets: list[Path]) -> Path | None:
    existing = [path for path in targets if path.exists()]
    if not existing:
        return None
    stamp = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    backup = destination / "_bundle_backups" / f"{bundle_id}_{stamp}"
    counter = 1
    while backup.exists():
        backup = destination / "_bundle_backups" / f"{bundle_id}_{stamp}_{counter}"
        counter += 1
    backup.mkdir(parents=True)
    for path in existing:
        shutil.move(str(path), str(backup / path.name))
    return backup


def build_bundle(args: argparse.Namespace) -> dict[str, object]:
    locale_id(args.locale)
    sources = [parse_source(raw) for raw in args.source]
    if len({source.name.casefold() for source in sources}) != len(sources):
        raise ValueError("Source names must be unique")
    destination = Path(args.destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    bundle_id = slugify(args.bundle_id)
    if bundle_id != args.bundle_id:
        raise ValueError(f"bundle-id must already be filesystem-safe; suggested value: {bundle_id}")

    all_entries: list[Entry] = []
    source_manifest: list[dict[str, object]] = []
    for source in sources:
        entries, files = read_source(source, args.locale)
        all_entries.extend(entries)
        source_manifest.append({"name": source.name, "files": files, "entries": len(entries)})
    selected, collisions = select_entries(all_entries, args.collision_policy)

    dependencies = list(dict.fromkeys([*(source.name for source in sources if args.source_dependencies), *args.dependency]))
    metadata: dict[str, str] = {}
    for raw in args.metadata:
        if "=" not in raw:
            raise ValueError(f"Metadata must be KEY=VALUE: {raw}")
        key, value = raw.split("=", 1)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
            raise ValueError(f"Unsafe metadata key: {key}")
        metadata[key] = value

    # Include the ZIP even when this run disables ZIP output, so a stale archive
    # from a previous run is backed up rather than left beside the new bundle.
    targets = [destination / bundle_id, destination / f"{bundle_id}.mod", destination / f"{bundle_id}.zip"]
    if any(path.exists() for path in targets) and not args.overwrite:
        raise FileExistsError(f"Bundle already exists under {destination}; use --overwrite to back it up first")

    temp_parent = Path(tempfile.mkdtemp(prefix=f".{bundle_id}-", dir=destination))
    try:
        mod_root = temp_parent / bundle_id
        locale_root = mod_root / "localization" / locale_id(args.locale)
        locale_root.mkdir(parents=True)
        grouped: dict[str, list[Entry]] = {source.name: [] for source in sources}
        for entry in selected:
            grouped[entry.source.name].append(entry)
        output_files: list[dict[str, object]] = []
        for index, source in enumerate(sources):
            entries = grouped[source.name]
            if not entries:
                continue
            output = locale_root / f"{index:03d}_{source.slug}_l_{locale_id(args.locale)}.yml"
            write_utf8_bom(output, f"{args.locale}:\n" + "\n".join(entry.raw_line for entry in entries) + "\n")
            output_files.append({"path": output.relative_to(mod_root).as_posix(), "entries": len(entries), "sha256": sha256_file(output)})

        (mod_root / "descriptor.mod").write_text(
            descriptor_text(args.bundle_name, args.version, args.supported_version, dependencies, None), encoding="utf-8", newline="\n"
        )
        output_files.append({"path": "descriptor.mod", "sha256": sha256_file(mod_root / "descriptor.mod")})
        manifest = {
            "schema": 1,
            "bundle": {"id": bundle_id, "name": args.bundle_name, "version": args.version, "supported_version": args.supported_version},
            "language": args.language,
            "locale": args.locale,
            "collision_policy": args.collision_policy,
            "counts": {"input_entries": len(all_entries), "unique_entries": len(selected), "collisions": len(collisions)},
            "dependencies": dependencies,
            "sources": source_manifest,
            "outputs": output_files,
            "collisions": collisions,
            "metadata": metadata,
        }
        (mod_root / "bundle-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        launcher_temp = temp_parent / f"{bundle_id}.mod"
        launcher_temp.write_text(
            descriptor_text(args.bundle_name, args.version, args.supported_version, dependencies, f"mod/{bundle_id}"), encoding="utf-8", newline="\n"
        )
        archive_temp = temp_parent / f"{bundle_id}.zip"
        if args.zip:
            write_deterministic_zip(archive_temp, mod_root, launcher_temp)
        validate_built(mod_root, launcher_temp, archive_temp if args.zip else None, args.locale, {entry.key: entry.value for entry in selected})

        backup = move_existing(destination, bundle_id, targets) if args.overwrite else None
        os.replace(mod_root, destination / bundle_id)
        os.replace(launcher_temp, destination / f"{bundle_id}.mod")
        if args.zip:
            os.replace(archive_temp, destination / f"{bundle_id}.zip")
        report = {
            "bundle_root": str(destination / bundle_id),
            "launcher": str(destination / f"{bundle_id}.mod"),
            "zip": str(destination / f"{bundle_id}.zip") if args.zip else None,
            "backup": str(backup) if backup else None,
            **manifest["counts"],
        }
        print(json.dumps(report, ensure_ascii=False))
        return report
    finally:
        shutil.rmtree(temp_parent, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Combine translated CK3 localization into one mod without using an LLM")
    parser.add_argument("--source", action="append", required=True, help="[NAME=]staged translated mod root; repeat as needed")
    parser.add_argument("--destination", required=True, help="directory receiving the bundle folder, .mod file, and ZIP")
    parser.add_argument("--bundle-name", required=True)
    parser.add_argument("--bundle-id", required=True, help="safe lowercase filesystem id")
    parser.add_argument("--language", required=True)
    parser.add_argument("--locale", required=True, help="for example l_japanese")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--supported-version")
    parser.add_argument("--dependency", action="append", default=[])
    parser.add_argument("--source-dependencies", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--collision-policy", choices=("error", "first", "last"), default="error")
    parser.add_argument("--metadata", action="append", default=[], help="safe KEY=VALUE stored in the manifest")
    parser.add_argument("--zip", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--overwrite", action="store_true", help="back up existing artifacts, then replace them")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    build_bundle(args)


if __name__ == "__main__":
    main()
