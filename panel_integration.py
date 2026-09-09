#!/usr/bin/env python3
"""Point KlipperScreen at the independently managed filament-panel source."""
from pathlib import Path
import shutil
import time


def point_panel(home, install_root, ks_root, panel_root):
    home, install_root, ks_root, panel_root = map(Path, (home, install_root, ks_root, panel_root))
    for name in ("maeve_filaments.py", "menu.conf"):
        if not (panel_root / name).is_file():
            raise ValueError(f"Missing plugin file: {panel_root / name}")
    candidates = [home / "printer_data/config/KlipperScreen.conf",
                  home / ".config/KlipperScreen/KlipperScreen.conf", ks_root / "KlipperScreen.conf"]
    config = next((path for path in candidates if path.is_file()), candidates[0])
    original = config.read_text() if config.exists() else ""
    marker = install_root / "klipperscreen-panel-root"
    old_roots = {str(install_root / "klipperscreen"), str(panel_root)}
    if marker.exists():
        old_roots.add(marker.read_text().strip())
    old_includes = {f"[include {root}/menu.conf]" for root in old_roots}
    lines = original.splitlines(keepends=True)
    body = "".join(line for line in lines if line.strip() not in old_includes)
    updated = f"[include {panel_root}/menu.conf]\n" + body
    if updated != original:
        config.parent.mkdir(parents=True, exist_ok=True)
        if config.exists():
            shutil.copy2(config, str(config) + f".before-filament-sync-{time.time_ns()}")
        config.write_text(updated)
    link = ks_root / "panels/maeve_filaments.py"
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        shutil.copy2(link, str(link) + f".before-filament-sync-{time.time_ns()}")
        link.unlink()
    link.symlink_to(panel_root / "maeve_filaments.py")
    marker.write_text(str(panel_root) + "\n")


if __name__ == "__main__":
    import sys
    point_panel(*sys.argv[1:])
