#!/usr/bin/env python3
"""Package current installer source with a validated, offline frontend build."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def package(validation, destination):
    manifest = json.loads((validation / "build-manifest.json").read_text())
    patch_digest = hashlib.sha256((ROOT / "mainsail_source/filament-sync.patch").read_bytes()).hexdigest()
    if manifest["patch_sha256"] != patch_digest:
        raise RuntimeError("Validated build does not match current frontend patch")
    version = (ROOT / "VERSION").read_text().strip()
    destination.mkdir(parents=True, exist_ok=True)
    result = destination / f"filament-sync-unified-{version}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="filament-sync-package-") as temporary:
        staging = Path(temporary) / f"filament-sync-unified-{version}"
        shutil.copytree(ROOT, staging, ignore=shutil.ignore_patterns(".git", "__pycache__", "release-output"))
        bundle = staging / "mainsail_frontend/mainsail.zip"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(validation / ("00-mainsail.zip" if (validation / "00-mainsail.zip").exists() else "mainsail.zip")) as source, zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as target:
            for entry in source.infolist():
                if entry.filename in (".version", "release_info.json"):
                    continue
                target.writestr(entry, source.read(entry))
            # Do not ship a made-up live repository from the worker simulation.
            target.writestr(".version", manifest["upstream_tag"] + "\n")
            target.writestr("release_info.json", json.dumps({
                "project_owner": "mainsail-crew", "project_name": "mainsail",
                "version": manifest["upstream_tag"], "asset_name": "mainsail.zip",
            }))
        with zipfile.ZipFile(bundle) as archive:
            if archive.testzip() is not None:
                raise RuntimeError("Invalid frontend ZIP")
        shutil.copy2(validation / "mainsail-patched-source.tar.gz", staging / "mainsail_source/mainsail-patched-source.tar.gz")
        for path in staging.glob("*.sh"):
            path.chmod(0o755)
        for path in staging.glob("*.py"):
            path.chmod(0o755)
        with tarfile.open(result, "w:gz") as archive:
            archive.add(staging, arcname=staging.name)
    digest = hashlib.sha256(result.read_bytes()).hexdigest()
    result.with_name(result.name + ".sha256").write_text(digest + "  " + result.name + "\n")
    print(json.dumps({"path": str(result), "sha256": digest, "size": result.stat().st_size}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    package(args.validation_dir.resolve(), args.output_root.resolve())
