#!/usr/bin/env python3
"""Idle-only scheduler. Moonraker owns all software installation."""
import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class API:
    def __init__(self, url, key_file=None):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Automatic updates require a local Moonraker URL")
        self.url = url.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        if key_file:
            self.headers["X-Api-Key"] = Path(key_file).read_text().strip()

    def call(self, path, data=None):
        request = Request(self.url + path, headers=self.headers,
                          data=None if data is None else json.dumps(data).encode())
        with urlopen(request, timeout=900 if data is not None else 15) as response:
            value = json.load(response)
        if "error" in value:
            raise RuntimeError("Moonraker rejected the request; inspect its log")
        return value["result"]


def is_idle(api):
    if api.call("/server/info").get("klippy_state") != "ready":
        return False
    status = api.call("/printer/objects/query?print_stats&idle_timeout&heaters")["status"]
    if status.get("print_stats", {}).get("state") not in ("standby", "complete", "cancelled"):
        return False
    if status.get("idle_timeout", {}).get("state") != "Idle":
        return False
    heaters = status.get("heaters", {}).get("available_heaters")
    if not isinstance(heaters, list) or not heaters:
        return False
    targets = api.call("/printer/objects/query?" + urlencode({name: "target" for name in heaters}))["status"]
    return all(name in targets and targets[name].get("target") == 0 for name in heaters)


def run(api, settings, state_path, retry=False):
    if not is_idle(api):
        print("Deferred: printer must be idle with every heater target off")
        return
    # Reuse Moonraker's scheduled refresh instead of fetching every git repo.
    status = api.call("/machine/update/status")
    if status.get("busy", True):
        print("Deferred: Moonraker updater busy")
        return
    state_path = Path(state_path)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    for client in settings["clients"]:
        name = client["name"]
        # Correct the 2.0.0 candidate's mistaken upstream KlipperScreen target,
        # including old settings retained across installation upgrades.
        if client["type"] == "git_repo" and client.get("role") != "filament_panel":
            print(f"Skipped {name}: upstream software auto-updates are disabled")
            continue
        info = status.get("version_info", {}).get(name, {})
        if (info.get("is_valid") is not True or info.get("is_dirty") or info.get("corrupt")
                or info.get("warnings") or info.get("anomalies")):
            print(f"Skipped {name}: missing, invalid, modified, or requires attention")
            continue
        if info.get("configured_type") != client["type"]:
            print(f"Skipped {name}: updater type changed")
            continue
        if client.get("repo"):
            actual = f"{info.get('owner')}/{info.get('repo_name')}"
            if actual.lower() != client["repo"].lower():
                print(f"Skipped {name}: release repository does not match settings")
                continue
        remote = info.get("remote_hash") if client["type"] == "git_repo" else info.get("remote_version")
        current = info.get("current_hash") if client["type"] == "git_repo" else info.get("version")
        if not remote or remote in ("?", "unknown", "not-specified") or remote == current:
            print(f"{name}: no update")
            continue
        if client["type"] == "web":
            pattern = r"v?(\d+)\.(\d+)\.(\d+)(?:\+fs\.(\d+)\.(\d+)\.(\d+))?"
            old, new = re.fullmatch(pattern, current or ""), re.fullmatch(pattern, remote)
            if not old or not new or tuple(int(n or 0) for n in new.groups()) <= tuple(int(n or 0) for n in old.groups()):
                print(f"Skipped {name}: automatic downgrades and unknown versions are disabled")
                continue
        previous = state.get(name, {})
        if not retry and previous.get("target") == remote:
            print(f"Skipped {name}: target already attempted; inspect logs before --retry")
            continue
        if not is_idle(api) or api.call("/machine/update/status").get("busy", True):
            return
        state[name] = {"target": remote, "status": "pending", "time": time.time()}
        atomic_json(state_path, state)
        try:
            # Supported by both older and current Moonraker versions. Always
            # pass a specific name: never request a full printer/system update.
            api.call("/machine/update/client", {"name": name})
            fresh = api.call("/machine/update/status")["version_info"][name]
            installed = fresh.get("current_hash") if client["type"] == "git_repo" else fresh.get("version")
            if installed != remote:
                raise RuntimeError("Installed version did not match requested update")
            state[name]["status"] = "installed"
            print(f"Updated {name} to {remote}")
        except Exception:
            state[name]["status"] = "failed"
            atomic_json(state_path, state)
            raise RuntimeError(f"{name} failed or timed out; inspect Moonraker's log. Automatic retry blocked.") from None
        atomic_json(state_path, state)
        # One component per invocation gives service restarts time to settle.
        return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--retry", action="store_true")
    args = parser.parse_args()
    settings = json.loads(Path(args.settings).read_text())
    state_path = Path(settings["state_path"])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.with_suffix(".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another automatic update check is running")
            return
        run(API(settings["moonraker_url"], settings.get("api_key_file")), settings, state_path, args.retry)


if __name__ == "__main__":
    main()
