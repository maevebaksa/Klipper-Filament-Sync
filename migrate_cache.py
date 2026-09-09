#!/usr/bin/env python3
"""Move retired compiler caches out of Moonraker's watched config root."""
import argparse
from pathlib import Path
import shutil
import tempfile


def migrate(config, home):
    config, home = Path(config).resolve(), Path(home).resolve()
    cache = config / "tool-filament-sync/mainsail_source/cache"
    if not cache.exists() and not cache.is_symlink():
        return None
    destination = home / ".cache/filament-sync"
    if destination.resolve() == config or config in destination.resolve().parents:
        raise RuntimeError("Cache destination is inside the watched config root")
    destination.mkdir(parents=True, exist_ok=True)
    retired = Path(tempfile.mkdtemp(prefix="retired-", dir=destination)) / "cache"
    shutil.move(str(cache), str(retired))
    print(f"Retired compiler cache moved to {retired}")
    return retired


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-root", required=True)
    parser.add_argument("--home", required=True)
    args = parser.parse_args()
    migrate(args.config_root, args.home)
