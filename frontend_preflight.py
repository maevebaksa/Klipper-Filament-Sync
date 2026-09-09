#!/usr/bin/env python3
"""Only permit the offline 2.19.0 bundle on a known, no-newer frontend."""
from pathlib import Path
import re
import sys
import zipfile


def compatible(root, bundle):
    version = Path(root) / ".version"
    if not version.exists():
        return False
    pattern = r"v?(\d+)\.(\d+)\.(\d+)(?:\+fs\.[\w.]+)?"
    match = re.fullmatch(pattern, version.read_text().strip())
    with zipfile.ZipFile(bundle) as archive:
        target = re.fullmatch(pattern, archive.read(".version").decode().strip())
    return bool(match and target and tuple(map(int, match.groups())) <= tuple(map(int, target.groups())))


if __name__ == "__main__":
    raise SystemExit(0 if compatible(sys.argv[1], sys.argv[2]) else 1)
