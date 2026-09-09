#!/usr/bin/env python3
"""Cloud-only release builder. Never installed or executed on printer hosts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "mainsail-crew/mainsail"


def github(path):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "filament-sync-builder"}
    if os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    with urlopen(Request("https://api.github.com/" + path, headers=headers), timeout=30) as response:
        return json.load(response)


def plan(repo, upstream_tag):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid repository")
    latest = github(f"repos/{UPSTREAM}/releases/latest")["tag_name"]
    source_tag = upstream_tag or latest
    if not re.fullmatch(r"v\d+\.\d+\.\d+", source_tag):
        raise ValueError("Only stable upstream version tags are supported")
    version = (ROOT / "VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid package VERSION")
    release_tag = f"{source_tag}+fs.{version}"
    exists = False
    try:
        release = github(f"repos/{repo}/releases/tags/{release_tag}")
        if release["draft"]:
            raise RuntimeError("An incomplete draft exists; inspect and delete that draft before retrying")
        exists = True
    except HTTPError as error:
        if error.code != 404:
            raise
    commit = github(f"repos/{UPSTREAM}/commits/{source_tag}")["sha"]
    values = {"skip": str(exists).lower(), "source_tag": source_tag,
              "source_sha": commit, "release_tag": release_tag,
              "latest": str(source_tag == latest).lower()}
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
    print(json.dumps(values))


def command(args, cwd, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def build(repo, source_tag, source_sha, output):
    if not re.fullmatch(r"v\d+\.\d+\.\d+", source_tag) or not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError("Invalid upstream tag/commit")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid release repository")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = (ROOT / "VERSION").read_text().strip()
    release_tag = f"{source_tag}+fs.{version}"
    patch = ROOT / "mainsail_source/filament-sync.patch"
    with tempfile.TemporaryDirectory(prefix="filament-sync-cloud-") as temporary:
        work = Path(temporary)
        source = work / "source"
        command(["git", "clone", "--depth", "1", "--branch", source_tag,
                 f"https://github.com/{UPSTREAM}.git", str(source)], work)
        actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        if actual != source_sha:
            raise RuntimeError("Upstream tag moved between planning and checkout")
        command(["git", "apply", "--check", str(patch)], source)
        command(["git", "apply", str(patch)], source)
        environment = dict(os.environ, CYPRESS_INSTALL_BINARY="0", NODE_OPTIONS="--max-old-space-size=6144")
        # No writer credential or GitHub token is passed to dependency scripts.
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN"):
            environment.pop(key, None)
        command(["npm", "ci", "--no-audit", "--no-fund"], source, environment)
        command(["npm", "run", "lint"], source, environment)
        command(["npm", "run", "test:unit"], source, environment)
        command(["node_modules/.bin/tsc", "--noEmit"], source, environment)
        command(["npm", "run", "build"], source, environment)
        dist = source / "dist"
        if not (dist / "index.html").is_file():
            raise RuntimeError("Build produced no index.html")
        owner, name = repo.split("/")
        (dist / ".version").write_text(release_tag + "\n")
        (dist / "release_info.json").write_text(json.dumps({
            "project_owner": owner, "project_name": name,
            "version": release_tag, "asset_name": "mainsail.zip",
        }, indent=2) + "\n")
        manifest = {"upstream": UPSTREAM, "upstream_tag": source_tag, "upstream_commit": actual,
                    "package_version": version, "release": release_tag,
                    "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest()}
        (dist / "filament-sync-build.json").write_text(json.dumps(manifest, indent=2) + "\n")
        shutil.copy2(source / "LICENSE", dist / "MAINSail-LICENSE.txt")
        frontend = output / "mainsail.zip"
        with zipfile.ZipFile(frontend, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(dist.rglob("*")):
                if path.is_file() and path.name != "mainsail.zip":
                    archive.write(path, path.relative_to(dist).as_posix())
        with zipfile.ZipFile(frontend) as archive:
            if archive.testzip() is not None:
                raise RuntimeError("Frontend archive integrity check failed")
        # Include complete corresponding patched frontend source (GPL-3.0).
        source_archive = output / "mainsail-patched-source.tar.gz"
        with tarfile.open(source_archive, "w:gz") as archive:
            for path in source.iterdir():
                if path.name not in (".git", "node_modules", "dist"):
                    archive.add(path, arcname="mainsail-source/" + path.name)
        package = work / f"filament-sync-unified-{version}"
        shutil.copytree(ROOT, package, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "node_modules", "dist", "release-output", "*.tar.gz"))
        (package / "mainsail_frontend").mkdir(parents=True, exist_ok=True)
        shutil.copy2(frontend, package / "mainsail_frontend/mainsail.zip")
        shutil.copy2(source_archive, package / "mainsail_source/mainsail-patched-source.tar.gz")
        (package / "BUILD-STATUS.md").write_text(
            f"Built and validated by GitHub Actions for {release_tag}.\nUpstream commit: {actual}\n")
        with tarfile.open(output / f"filament-sync-unified-{version}.tar.gz", "w:gz") as archive:
            archive.add(package, arcname=package.name)
        (output / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        with (output / "SHA256SUMS").open("w") as sums:
            for path in sorted(output.iterdir()):
                if path.is_file() and path.name != "SHA256SUMS":
                    sums.write(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "build"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--upstream-tag", default="")
    parser.add_argument("--source-sha")
    parser.add_argument("--output", default="release-output")
    args = parser.parse_args()
    if args.mode == "plan":
        plan(args.repo, args.upstream_tag)
    else:
        build(args.repo, args.upstream_tag, args.source_sha, args.output)
