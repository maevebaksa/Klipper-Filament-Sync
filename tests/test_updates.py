import copy
import json
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from auto_update import run
from frontend_preflight import compatible
from migrate_cache import migrate
from setup_updates import configuration, section_for
import setup_updates


class FakeAPI:
    def __init__(self):
        self.state = "standby"
        self.heater = 0
        self.busy = False
        self.posts = []
        self.fail = False
        self.info = {"is_valid": True, "configured_type": "git_repo", "current_hash": "old", "remote_hash": "new"}

    def call(self, path, data=None):
        if path == "/server/info":
            return {"klippy_state": "ready"}
        if path == "/printer/objects/query?print_stats&idle_timeout&heaters":
            return {"status": {"print_stats": {"state": self.state}, "idle_timeout": {"state": "Idle"},
                               "heaters": {"available_heaters": ["extruder"]}}}
        if path.startswith("/printer/objects/query?"):
            return {"status": {"extruder": {"target": self.heater}}}
        if path == "/machine/update/status":
            return {"busy": self.busy, "version_info": {"filament-sync": copy.deepcopy(self.info)}}
        if path == "/machine/update/client":
            self.posts.append(data)
            if self.fail:
                raise TimeoutError()
            self.info["current_hash"] = "new"
            if self.info["configured_type"] == "web":
                self.info["version"] = self.info["remote_version"]
            return "ok"
        raise AssertionError(path)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.api = FakeAPI()
        self.settings = {"clients": [{"name": "filament-sync", "type": "git_repo", "role": "filament_panel"}]}
        self.state = self.root / "state.json"

    def test_legacy_upstream_settings_are_blocked(self):
        self.settings["clients"] = [{"name": "filament-sync", "type": "git_repo"}]
        run(self.api, self.settings, self.state)
        self.assertEqual(self.api.posts, [])
        self.assertFalse(self.state.exists())

    def test_idle_update_once(self):
        run(self.api, self.settings, self.state)
        run(self.api, self.settings, self.state)
        self.assertEqual(self.api.posts, [{"name": "filament-sync"}])
        self.assertEqual(json.loads(self.state.read_text())["filament-sync"]["status"], "installed")

    def test_print_paused_and_heater_defer(self):
        for state, heater in [("printing", 0), ("paused", 0), ("standby", 200), ("unknown", 0)]:
            self.api.state, self.api.heater = state, heater
            run(self.api, self.settings, self.state)
        self.assertEqual(self.api.posts, [])

    def test_dirty_invalid_and_busy_defer(self):
        for key in ("is_dirty", "corrupt", "warnings", "anomalies"):
            self.api.info[key] = True
            run(self.api, self.settings, self.state)
            self.api.info.pop(key)
        self.api.busy = True
        run(self.api, self.settings, self.state)
        self.assertEqual(self.api.posts, [])

    def test_auth_error_does_not_write(self):
        with patch.object(self.api, "call", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                run(self.api, self.settings, self.state)
        self.assertFalse(self.state.exists())

    def test_timeout_not_retried_after_restart(self):
        self.api.fail = True
        with self.assertRaises(RuntimeError):
            run(self.api, self.settings, self.state)
        self.api.fail = False
        run(self.api, self.settings, self.state)
        self.assertEqual(len(self.api.posts), 1)
        run(self.api, self.settings, self.state, retry=True)
        self.assertEqual(len(self.api.posts), 2)

    def test_pending_request_not_retried(self):
        self.state.write_text(json.dumps({"filament-sync": {"target": "new", "status": "pending"}}))
        run(self.api, self.settings, self.state)
        self.assertEqual(self.api.posts, [])

    def test_cache_move_preserves_files_and_symlinks(self):
        config = self.root / "printer_data/config"
        cache = config / "tool-filament-sync/mainsail_source/cache"
        cache.mkdir(parents=True)
        (cache / "valuable.txt").write_text("saved")
        (cache / "link").symlink_to("valuable.txt")
        moved = migrate(config, self.root)
        self.assertFalse(cache.exists())
        self.assertEqual((moved / "valuable.txt").read_text(), "saved")
        self.assertTrue((moved / "link").is_symlink())
        self.assertIsNone(migrate(config, self.root))

    def test_cache_move_rejects_watched_destination(self):
        config = self.root / "printer_data/config"
        (config / "tool-filament-sync/mainsail_source/cache").mkdir(parents=True)
        (self.root / ".cache").symlink_to(config, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            migrate(config, self.root)

    def test_include_override_and_custom_updater_name(self):
        (self.root / "base.conf").write_text("[update_manager ui]\ntype: web\npath: ~/mainsail\nrepo: upstream/repo\n")
        main = self.root / "moonraker.conf"
        main.write_text("[include base.conf]\n[update_manager ui]\nrepo: patched/repo\n")
        config = configuration(main)
        self.assertEqual(config["update_manager ui"]["repo"], "patched/repo")
        self.assertEqual(section_for(config, self.root / "mainsail", self.root, "mainsail"), "update_manager ui")
        config["update_manager duplicate"] = {"path": "~/mainsail"}
        with self.assertRaises(ValueError):
            section_for(config, self.root / "mainsail", self.root, "mainsail")

    def test_never_downgrade_newer_or_unknown_frontend(self):
        bundle = self.root / "mainsail.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr(".version", "v2.19.0")
        for version, expected in [("v2.20.0", False), ("unknown", False), ("v2.18.2", True), ("v2.19.0", True)]:
            (self.root / ".version").write_text(version)
            self.assertEqual(compatible(self.root, bundle), expected)

    def test_installer_never_enables_legacy_builder(self):
        installer = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
        self.assertNotIn("enable --now filament-sync-mainsail-guard.timer", installer)
        self.assertNotIn("start filament-sync-mainsail-guard.service", installer)
        self.assertLess(installer.index('sudo systemctl stop'), installer.index('migrate_cache.py'))

    def test_web_repo_and_version_checks(self):
        self.settings = {"clients": [{"name": "filament-sync", "type": "web", "repo": "owner/ui"}]}
        self.api.info = {"is_valid": True, "configured_type": "web", "owner": "owner", "repo_name": "ui",
                         "version": "v2.20.0", "remote_version": "v2.19.0+fs.2.0.0"}
        run(self.api, self.settings, self.state)
        self.assertEqual(self.api.posts, [])
        self.api.info["version"] = "v2.19.0"
        self.api.info["repo_name"] = "wrong"
        run(self.api, self.settings, self.state)
        self.assertEqual(self.api.posts, [])
        self.api.info["repo_name"] = "ui"
        run(self.api, self.settings, self.state)
        self.assertEqual(len(self.api.posts), 1)

    def test_cloud_and_ks_setup_preserves_existing_config(self):
        config = self.root / "printer_data/config"
        install = config / "tool-filament-sync"
        install.mkdir(parents=True)
        (install / "auto_update.py").write_text("# installed")
        allowed = self.root / "printer_data/moonraker.asvc"
        allowed.write_text("KlipperScreen\ncrowsnest\n")
        (install / "auto-update.json").write_text(json.dumps({"clients": [{"name": "KlipperScreen", "type": "git_repo"}]}))
        (install / "cloud-updates.conf").write_text("[update_manager KlipperScreen]\ntype: git_repo\npath: ~/KlipperScreen\n")
        managed = config / "vendor.conf"
        original = "[update_manager ui]\ntype: web\npath: ~/mainsail\nrepo: mainsail-crew/mainsail\npersistent_files:\n  config.json\n  .theme\n"
        managed.write_text(original)
        main = config / "moonraker.conf"
        main.write_text("[include vendor.conf]\n[authorization]\ntrusted_clients: 127.0.0.1\n")
        frontend = self.root / "mainsail"
        frontend.mkdir()
        (frontend / "index.html").write_text("frontend")
        (frontend / ".version").write_text("v2.19.0")
        ks = self.root / "KlipperScreen"
        (ks / "panels").mkdir(parents=True)
        plugin = self.root / "Klipper-Filament-Sync"
        panel = plugin / "klipperscreen"
        panel.mkdir(parents=True)
        (panel / "maeve_filaments.py").write_text("# plugin")
        (panel / "menu.conf").write_text("# menu")
        (config / "KlipperScreen.conf").write_text(f"[include {install}/klipperscreen/menu.conf]\n[main]\n# untouched\n")
        with patch.object(Path, "home", return_value=self.root), \
             patch.object(setup_updates.os, "geteuid", return_value=1000), \
             patch.object(setup_updates.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="renameduser")), \
             patch.object(setup_updates, "API", return_value=self.api), \
             patch.object(setup_updates, "release_exists", return_value={}), \
             patch.object(setup_updates.subprocess, "check_output", return_value="https://github.com/owner/ui.git\n"), \
             patch.object(setup_updates.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
             patch("sys.argv", ["setup_updates.py", "--repo", "owner/ui", "--klipperscreen"]):
            setup_updates.main()
            setup_updates.main()
        self.assertEqual((ks / "panels/maeve_filaments.py").resolve(), panel / "maeve_filaments.py")
        self.assertEqual((config / "KlipperScreen.conf").read_text(), f"[include {panel}/menu.conf]\n[main]\n# untouched\n")
        self.assertEqual(allowed.read_text(), "KlipperScreen\ncrowsnest\nfilament-sync-panel\n")
        self.assertNotIn("update_manager KlipperScreen", configuration(main))
        self.assertEqual(managed.read_text(), original)
        self.assertEqual(main.read_text().count("[include tool-filament-sync/cloud-updates.conf]"), 1)
        merged = configuration(main)
        self.assertEqual(merged["update_manager ui"]["repo"], "owner/ui")
        self.assertIn(".theme", merged["update_manager ui"]["persistent_files"])
        self.assertEqual(merged["update_manager filament-sync-panel"]["path"], str(plugin))
        self.assertEqual(merged["authorization"]["trusted_clients"], "127.0.0.1")
        meta = json.loads((frontend / "release_info.json").read_text())
        self.assertEqual(meta["project_owner"], "owner")
        self.assertEqual(meta["version"], "v2.19.0")
        settings = json.loads((install / "auto-update.json").read_text())
        self.assertEqual({c["name"] for c in settings["clients"]}, {"ui", "filament-sync-panel"})


if __name__ == "__main__":
    unittest.main()
