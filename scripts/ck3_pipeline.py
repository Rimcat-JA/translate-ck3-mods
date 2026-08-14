#!/usr/bin/env python3
"""Run local-only CK3 translation, then deterministic single-mod packaging."""
from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path


def require_loopback(endpoint: str) -> None:
    parsed = urllib.parse.urlparse(endpoint)
    host = (parsed.hostname or "").lower()
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host == "localhost"
    if parsed.scheme not in {"http", "https"} or not is_loopback:
        raise ValueError("translation.endpoint must be a loopback local-LLM URL (localhost, 127.x.x.x, or ::1)")


def resolve_path(base: Path, value: str) -> str:
    path = Path(value).expanduser()
    return str((base / path).resolve() if not path.is_absolute() else path.resolve())


def append_option(command: list[str], config: dict[str, object], key: str, flag: str | None = None) -> None:
    value = config.get(key)
    if value is not None:
        command.extend([flag or f"--{key.replace('_', '-')}", str(value)])


def load_config(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("translation"), dict) or not isinstance(data.get("bundle"), dict):
        raise TypeError("Config must contain translation and bundle objects")
    return data


def build_commands(config_path: Path) -> tuple[list[str], list[str]]:
    config = load_config(config_path)
    base = config_path.parent.resolve()
    translation = config["translation"]
    bundle = config["bundle"]
    assert isinstance(translation, dict) and isinstance(bundle, dict)
    required_translation = ("mods", "staging_output", "cache", "language", "locale", "endpoint", "model")
    required_bundle = ("destination", "id", "name")
    missing = [f"translation.{key}" for key in required_translation if key not in translation]
    missing += [f"bundle.{key}" for key in required_bundle if key not in bundle]
    if missing:
        raise ValueError("Missing config fields: " + ", ".join(missing))
    require_loopback(str(translation["endpoint"]))
    mods = translation["mods"]
    if not isinstance(mods, list) or not mods:
        raise ValueError("translation.mods must be a non-empty array")

    script_root = Path(__file__).resolve().parent
    translate = [sys.executable, str(script_root / "ck3_localize.py"), "translate"]
    staged_sources: list[str] = []
    staging = Path(resolve_path(base, str(translation["staging_output"])))
    for item in mods:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("path"), str):
            raise TypeError("Each translation.mods item must contain string name and path")
        name = item["name"]
        if not name or name in {".", ".."} or any(character in name for character in ("/", "\\", "=")):
            raise ValueError(f"Unsafe translation mod name: {name!r}")
        source_path = resolve_path(base, item["path"])
        translate.extend(["--mod", f"{name}={source_path}"])
        staged_sources.append(f"{name}={staging / name}")
    translate.extend([
        "--output", str(staging),
        "--cache", resolve_path(base, str(translation["cache"])),
        "--language", str(translation["language"]),
        "--locale", str(translation["locale"]),
        "--endpoint", str(translation["endpoint"]),
        "--model", str(translation["model"]),
    ])
    for key in ("workers", "batch_items", "batch_chars", "long_threshold", "long_segment", "retries", "timeout", "max_tokens", "temperature", "min_interval", "api_key_env"):
        append_option(translate, translation, key)
    for key in ("glossary", "extra_instructions"):
        if translation.get(key) is not None:
            translate.extend([f"--{key.replace('_', '-')}", resolve_path(base, str(translation[key]))])

    package = [
        sys.executable, str(script_root / "ck3_bundle.py"),
        "--destination", resolve_path(base, str(bundle["destination"])),
        "--bundle-id", str(bundle["id"]),
        "--bundle-name", str(bundle["name"]),
        "--language", str(translation["language"]),
        "--locale", str(translation["locale"]),
        "--metadata", "translation_engine=local-openai-compatible",
        "--metadata", f"translation_model={translation['model']}",
    ]
    for source in staged_sources:
        package.extend(["--source", source])
    for key, flag in (("version", "--version"), ("supported_version", "--supported-version"), ("collision_policy", "--collision-policy")):
        append_option(package, bundle, key, flag)
    dependencies = bundle.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(value, str) for value in dependencies):
        raise ValueError("bundle.dependencies must be an array of strings")
    for dependency in dependencies:
        package.extend(["--dependency", dependency])
    package.append("--source-dependencies" if bundle.get("source_dependencies", True) else "--no-source-dependencies")
    package.append("--zip" if bundle.get("zip", True) else "--no-zip")
    if bundle.get("overwrite", False):
        package.append("--overwrite")
    return translate, package


def redacted(command: list[str]) -> list[str]:
    result = list(command)
    if "--api-key-env" in result:
        index = result.index("--api-key-env")
        result[index + 1] = "<environment-variable-name>"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate CK3 mods using only a local LLM, then combine them deterministically")
    parser.add_argument("--config", required=True, help="UTF-8 JSON pipeline config")
    parser.add_argument("--dry-run", action="store_true", help="validate config and print commands without running them")
    args = parser.parse_args()
    config_path = Path(args.config).expanduser().resolve()
    translate, package = build_commands(config_path)
    if args.dry_run:
        print(json.dumps({"translation": redacted(translate), "bundle": package}, ensure_ascii=False, indent=2))
        return
    subprocess.run(translate, check=True)
    subprocess.run(package, check=True)


if __name__ == "__main__":
    main()
