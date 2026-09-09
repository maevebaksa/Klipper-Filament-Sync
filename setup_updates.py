#!/usr/bin/env python3
"""Configure cloud Mainsail releases and/or idle-only KlipperScreen updates."""
import argparse
import configparser
import glob
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen

from auto_update import API, atomic_json, is_idle
from panel_integration import point_panel


def configuration(path, excluded=None, stack=()):
    path = Path(path).resolve()
    if path == excluded:
        return {}
    if path in stack:
        raise ValueError(f"Circular Moonraker include: {path}")
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(path.read_text())
    result = {}
    for name in parser.sections():
        if name.startswith("include "):
            pattern = name[len("include "):].strip()
            for child in sorted(glob.glob(str(path.parent / pattern))):
                for section, options in configuration(child, excluded, stack + (path,)).items():
                    result.setdefault(section, {}).update(options)
        else:
            result.setdefault(name, {}).update(dict(parser[name]))
    return result


def section_for(config, path, home, default):
    matches = []
    for name, options in config.items():
        if not name.startswith("update_manager ") or not options.get("path"):
            continue
        value = options["path"]
        expanded = str(home) + value[1:] if value.startswith("~/") else value
        if Path(expanded).resolve() == path.resolve():
            matches.append(name)
    if len(matches) > 1:
        raise ValueError(f"Multiple Moonraker updaters target {path}; resolve them before setup")
    section = matches[0] if matches else f"update_manager {default}"
    if not matches and section in config:
        raise ValueError(f"{section} already targets a different path")
    return section


def release_exists(repo):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repo):
        raise ValueError("Use a GitHub owner/repository name")
    request = Request(f"https://api.github.com/repos/{repo}/releases/latest",
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "filament-sync-2.0"})
    with urlopen(request, timeout=20) as response:
        release = json.load(response)
    if (release.get("draft") or release.get("prerelease")
            or not re.fullmatch(r"v\d+\.\d+\.\d+\+fs\.\d+\.\d+\.\d+", release.get("tag_name", ""))
            or release.get("name") != release.get("tag_name")
            or not any(asset["name"] == "00-mainsail.zip" for asset in release.get("assets", []))):
        raise ValueError("Repository has no compatible published filament-sync release")
    return release


def write_with_backup(path, text):
    path = Path(path)
    if path.exists() and path.read_text() == text:
        return
    if path.exists():
        shutil.copy2(path, str(path) + f".before-filament-sync-{time.time_ns()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="Public GitHub repository containing compiled releases")
    parser.add_argument("--klipperscreen", "--klipperscreen-plugin", action="store_true", help="Update our filament panel only")
    parser.add_argument("--plugin-only", action="store_true", help="Configure only our KlipperScreen plugin")
    parser.add_argument("--mainsail-dir", type=Path, default=Path.home() / "mainsail")
    parser.add_argument("--klipperscreen-dir", type=Path, default=Path.home() / "KlipperScreen")
    parser.add_argument("--moonraker-url", default="http://127.0.0.1:7125")
    parser.add_argument("--api-key-file", type=Path)
    args = parser.parse_args()
    if args.plugin_only:
        args.klipperscreen = True
    if os.geteuid() == 0:
        parser.error("Run as your normal printer user; this script invokes sudo for systemd only")
    if not args.repo and not args.klipperscreen:
        parser.error("Specify --repo and/or --klipperscreen")
    home = Path.home()
    config_root = home / "printer_data/config"
    install_root = config_root / "tool-filament-sync"
    moonraker = config_root / "moonraker.conf"
    override = install_root / "cloud-updates.conf"
    settings_path = install_root / "auto-update.json"
    if not (install_root / "auto_update.py").exists():
        parser.error("Install unified 2.0 first")
    api = API(args.moonraker_url, args.api_key_file)
    if not is_idle(api):
        parser.error("Wait until the printer is idle with all heater targets off")
    if api.call("/machine/update/status").get("busy", True):
        parser.error("Wait for the existing Moonraker update to finish")
    config = configuration(moonraker)
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {"clients": []}
    clients = {client["name"]: client for client in settings["clients"]
               if client["type"] != "git_repo" or client.get("role") == "filament_panel"}
    generated = override.read_text() if override.exists() else "# Managed by filament-sync setup_updates.py\n"
    managed = configparser.ConfigParser(interpolation=None, strict=False)
    managed.read_string(generated)
    # Remove only the obsolete upstream updater sections generated by 2.0.0.
    # User-owned upstream updater configuration outside this file is retained.
    for client in settings["clients"]:
        if client["type"] == "git_repo" and client.get("role") != "filament_panel":
            managed.remove_section("update_manager " + client["name"])
    if not managed.has_section("update_manager"):
        managed.add_section("update_manager")
    managed.set("update_manager", "enable_auto_refresh", "True")
    metadata = None
    if args.repo and not args.plugin_only:
        release_exists(args.repo)  # No config mutation before a real release exists.
        root = args.mainsail_dir.resolve()
        if not (root / "index.html").exists() or not (root / ".version").exists():
            parser.error("Mainsail root needs index.html and .version")
        section = section_for(config, root, home, "mainsail")
        existing = config.get(section, {})
        if existing.get("type", "web") != "web":
            parser.error("Mainsail updater is not type web; migrate that updater manually first")
        if not managed.has_section(section):
            managed.add_section(section)
        for key, value in {"type": "web", "channel": "stable", "repo": args.repo, "path": str(root), "refresh_interval": "6"}.items():
            managed.set(section, key, value)
        persistent = existing.get("persistent_files", "").replace(",", "\n").splitlines()
        persistent = list(dict.fromkeys(["config.json"] + [value.strip() for value in persistent if value.strip()]))
        managed.set(section, "persistent_files", "\n" + "\n".join(persistent))
        owner, repo_name = args.repo.split("/")
        # Moonraker falls back to release_info.json's repository if it disagrees
        # with the config, so both must be migrated together. Preserve version.
        metadata_path = root / "release_info.json"
        metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
        metadata.update(project_owner=owner, project_name=repo_name,
                        version=(root / ".version").read_text().strip(), asset_name="00-mainsail.zip")
        name = section.split(" ", 1)[1]
        clients[name] = {"name": name, "type": "web", "repo": args.repo}
    if args.klipperscreen:
        root = args.klipperscreen_dir.resolve()
        if not (root / "panels").is_dir():
            parser.error("KlipperScreen panels directory not found")
        repo = args.repo or next((c["repo"] for c in clients.values() if c.get("repo")), "maevebaksa/Klipper-Filament-Sync")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repo):
            parser.error("Invalid plugin repository")
        plugin_root = home / "Klipper-Filament-Sync"
        origin = f"https://github.com/{repo}.git"
        if not plugin_root.exists():
            subprocess.run(["git", "clone", "--branch", "main", origin, str(plugin_root)], check=True)
        else:
            remote = subprocess.check_output(["git", "-C", str(plugin_root), "remote", "get-url", "origin"], text=True).strip()
            if re.sub(r"\.git$", "", remote.rstrip("/")) != re.sub(r"\.git$", "", origin):
                parser.error("Existing ~/Klipper-Filament-Sync has a different origin; no files were overwritten")
        panel_root = plugin_root / "klipperscreen"
        for filename in ("maeve_filaments.py", "menu.conf"):
            if not (panel_root / filename).is_file():
                parser.error("Repository does not contain the filament panel; publish the prepared source first")
        section = section_for(config, plugin_root, home, "filament-sync-panel")
        if section != "update_manager filament-sync-panel":
            parser.error("Plugin checkout already has a different updater name; retain one filament-sync-panel entry")
        allowed_path = home / "printer_data/moonraker.asvc"
        if not allowed_path.is_file():
            parser.error("Moonraker's printer_data/moonraker.asvc is missing; start/update Moonraker before plugin setup")
        allowed = allowed_path.read_text()
        unit = "KlipperScreen"
        if subprocess.run(["systemctl", "cat", unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            unit = "klipperscreen"
            subprocess.run(["systemctl", "cat", unit], stdout=subprocess.DEVNULL, check=True)
        managed[section] = {
            "type": "git_repo", "channel": "dev", "primary_branch": "main", "path": str(plugin_root),
            "origin": origin, "managed_services": "filament-sync-panel", "refresh_interval": "6",
        }
        name = section.split(" ", 1)[1]
        clients[name] = {"name": name, "type": "git_repo", "role": "filament_panel", "repo": repo}
    # All preflight checks succeeded. Back up and append an override after
    # existing includes; do not edit RatOS-managed or upstream config files.
    import io
    stream = io.StringIO()
    managed.write(stream)
    write_with_backup(override, stream.getvalue())
    include = "[include tool-filament-sync/cloud-updates.conf]"
    original = moonraker.read_text()
    if include not in original.splitlines():
        write_with_backup(moonraker, original.rstrip() + "\n\n" + include + "\n")
    if metadata is not None:
        write_with_backup(metadata_path, json.dumps(metadata, indent=2) + "\n")
    settings.update(clients=list(clients.values()), moonraker_url=args.moonraker_url,
                    state_path=str(home / ".local/state/filament-sync/updates.json"))
    if args.api_key_file:
        settings["api_key_file"] = str(args.api_key_file.resolve())
    atomic_json(settings_path, settings)
    if args.klipperscreen:
        point_panel(home, install_root, root, panel_root)
        # Moonraker permits a git updater to restart only its own named
        # service (or klipper/moonraker). This tiny relay reloads the screen
        # after both manual and automatic plugin updates, without upgrading it.
        relay = f"[Unit]\nDescription=Reload KlipperScreen filament panel\n\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/bin/systemctl try-restart {unit}.service\n"
        if "filament-sync-panel" not in [line.strip().replace(".service", "") for line in allowed.splitlines()]:
            write_with_backup(allowed_path, allowed.rstrip() + "\nfilament-sync-panel\n")
        dropin = f"[Service]\nExecStartPre=/usr/bin/ln -sfn {panel_root}/maeve_filaments.py {root}/panels/maeve_filaments.py\n"
        with tempfile.TemporaryDirectory() as temporary:
            relay_path = Path(temporary) / "filament-sync-panel.service"
            relay_path.write_text(relay)
            subprocess.run(["sudo", "install", "-m", "644", str(relay_path), "/etc/systemd/system/filament-sync-panel.service"], check=True)
            path = Path(temporary) / "filament-sync.conf"
            path.write_text(dropin)
            subprocess.run(["sudo", "install", "-d", "-m", "755", f"/etc/systemd/system/{unit}.service.d"], check=True)
            subprocess.run(["sudo", "install", "-m", "644", str(path), f"/etc/systemd/system/{unit}.service.d/filament-sync.conf"], check=True)
    username = pwd.getpwuid(os.getuid()).pw_name
    service = f"""[Unit]
Description=Check idle filament-sync and KlipperScreen updates
After=moonraker.service network-online.target

[Service]
Type=oneshot
User={username}
ExecStart=/usr/bin/python3 {install_root}/auto_update.py --settings {settings_path}
Nice=15
IOSchedulingClass=idle
TimeoutStartSec=20min
MemoryMax=96M
"""
    timer = """[Unit]
Description=Check for idle UI updates every six hours

[Timer]
OnBootSec=15min
OnUnitInactiveSec=6h
RandomizedDelaySec=20min

[Install]
WantedBy=timers.target
"""
    with tempfile.TemporaryDirectory() as temporary:
        for suffix, contents in (("service", service), ("timer", timer)):
            path = Path(temporary) / ("filament-sync-auto-update." + suffix)
            path.write_text(contents)
            subprocess.run(["sudo", "install", "-m", "644", str(path), "/etc/systemd/system/" + path.name], check=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    if not is_idle(api) or api.call("/machine/update/status").get("busy", True):
        print("Configuration saved. Printer became busy; restart Moonraker and enable filament-sync-auto-update.timer when idle.")
        return
    subprocess.run(["sudo", "systemctl", "restart", "moonraker.service"], check=True)
    if args.klipperscreen:
        subprocess.run(["sudo", "systemctl", "restart", unit + ".service"], check=True)
    subprocess.run(["sudo", "systemctl", "enable", "--now", "filament-sync-auto-update.timer"], check=True)
    print("Configured idle-only UI updates. First check is after boot delay/jitter; no local Mainsail compilation.")


if __name__ == "__main__":
    main()
