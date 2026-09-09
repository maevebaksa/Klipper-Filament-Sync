#!/usr/bin/env python3
"""Install the integrated filament-sync Mainsail frontend."""

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path


FILAMENT_NAVIGATION_HREF = "/filament-sync/"


def atomic_json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_binary_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def safe_archive_name(name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(f"Unsafe path in Mainsail archive: {name}")
    return relative


def install_frontend(archive_path: Path, mainsail_root: Path) -> bool:
    """Restore the bundled frontend when its index differs; preserve config.json."""
    with zipfile.ZipFile(archive_path) as archive:
        try:
            expected_index = archive.read("index.html")
        except KeyError as exc:
            raise SystemExit(f"Mainsail archive has no index.html: {archive_path}") from exc
        current_index = mainsail_root / "index.html"
        current_digest = hashlib.sha256(current_index.read_bytes()).digest() if current_index.is_file() else b""
        expected_digest = hashlib.sha256(expected_index).digest()
        if current_digest == expected_digest:
            return False

        mainsail_root.mkdir(parents=True, exist_ok=True)
        deferred_index = None
        for member in archive.infolist():
            relative = safe_archive_name(member.filename)
            if not relative.parts or member.is_dir() or relative == Path("config.json"):
                continue
            if relative == Path("index.html"):
                deferred_index = archive.read(member)
                continue
            atomic_binary_write(mainsail_root / relative, archive.read(member))
        if deferred_index is None:
            raise SystemExit(f"Mainsail archive has no index.html: {archive_path}")
        atomic_binary_write(current_index, deferred_index)
        return True


def remove_filament_navigation(path: Path) -> None:
    if not path.exists():
        return
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Cannot parse existing {path}: {exc}") from exc
    if not isinstance(loaded, list):
        raise SystemExit(f"Existing {path} must contain a JSON array")
    entries = [
        entry
        for entry in loaded
        if not (
            isinstance(entry, dict)
            and entry.get("href") == FILAMENT_NAVIGATION_HREF
        )
    ]
    if entries != loaded:
        atomic_json_write(path, entries)


def remove_overlay_link(link: Path, target: Path) -> None:
    if link.is_symlink() and link.resolve() == target.resolve():
        link.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mainsail-root", required=True, type=Path)
    parser.add_argument("--config-root", required=True, type=Path)
    parser.add_argument("--overlay-root", required=True, type=Path)
    parser.add_argument("--frontend-zip", type=Path)
    parser.add_argument("--source-version")
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--patch-file", type=Path)
    args = parser.parse_args()

    if args.frontend_zip:
        if not args.frontend_zip.is_file():
            raise SystemExit(f"Bundled Mainsail frontend not found: {args.frontend_zip}")
        if install_frontend(args.frontend_zip, args.mainsail_root):
            print("Installed the bundled filament-sync Mainsail frontend")
    if not (args.mainsail_root / "index.html").is_file():
        raise SystemExit(f"Mainsail web root not found: {args.mainsail_root}")
    remove_overlay_link(args.mainsail_root / "filament-sync", args.overlay_root)
    remove_filament_navigation(args.config_root / ".theme" / "navi.json")
    if args.state_file and args.source_version:
        index_path = args.mainsail_root / "index.html"
        patch_digest = ""
        if args.patch_file and args.patch_file.is_file():
            patch_digest = hashlib.sha256(args.patch_file.read_bytes()).hexdigest()
        atomic_json_write(
            args.state_file,
            {
                "status": "success",
                "version": (args.mainsail_root / ".version").read_text().strip(),
                "index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
                "patch_sha256": patch_digest,
            },
        )


if __name__ == "__main__":
    main()
