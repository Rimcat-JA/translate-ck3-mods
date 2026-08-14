from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def add_bytes(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--guide", required=True)
    parser.add_argument("--version", default="1.0.0")
    args = parser.parse_args()
    executable = Path(args.exe).resolve()
    guide = Path(args.guide).resolve()
    if not executable.is_file() or not guide.is_file():
        raise FileNotFoundError("Executable or guide is missing")
    digest = sha256(executable)
    checksum = executable.with_suffix(".sha256.txt")
    checksum.write_text(f"{digest}  {executable.name}\n", encoding="ascii", newline="\n")
    archive_path = executable.parent / f"CK3_Japanese_Mod_Maker_v{args.version}.zip"
    prefix = f"CK3_Japanese_Mod_Maker_v{args.version}"
    with zipfile.ZipFile(archive_path, "w") as archive:
        add_bytes(archive, f"{prefix}/{executable.name}", executable.read_bytes())
        add_bytes(archive, f"{prefix}/使い方.txt", guide.read_bytes())
        add_bytes(archive, f"{prefix}/{checksum.name}", checksum.read_bytes())
    print(f"Release ZIP: {archive_path}")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
