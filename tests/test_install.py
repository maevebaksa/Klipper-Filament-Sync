"""Exercise the real installer with isolated home/systemd fixtures."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]

SHIM = '''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
home = pathlib.Path(os.environ["TEST_PRINTER_HOME"])
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (home / "commands.jsonl").open("a") as log:
    log.write(json.dumps([name] + args) + "\\n")
if name == "getent":
    print("testprinter:x:1000:1000::" + str(home) + ":/bin/bash")
elif name == "systemctl":
    pass
elif name == "sudo":
    if args[0] == "install":
        args = [str(home / "system" / arg.lstrip("/")) if arg.startswith("/etc/") else arg for arg in args]
        pathlib.Path(args[-1]).parent.mkdir(parents=True, exist_ok=True)
    raise SystemExit(subprocess.run(args).returncode)
'''


class InstallTests(unittest.TestCase):
    def test_standard_and_ratos_fresh_repeat(self):
        # On non-root CI, install.sh derives the actual login name but getent
        # still supplies only this isolated home. sudo and systemctl are shims.
        for mode in ('standard', 'ratos', 'proforge'):
            ratos = mode == 'ratos'
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                source = home / "installer"
                shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "__pycache__", "release-output"))
                bundle = source / "mainsail_frontend/mainsail.zip"
                if not bundle.exists():
                    bundle.parent.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(bundle, "w") as archive:
                        archive.writestr("index.html", "compiled frontend fixture")
                        archive.writestr(".version", "v2.19.0")
                config = home / "printer_data/config"
                config.mkdir(parents=True)
                boundary = "#*# <---------------------- SAVE_CONFIG ---------------------->\n#*# [extruder]\n#*# pid_Kp = 23.45\n"
                (config / "printer.cfg").write_text("[extruder]\nmax_temp: 300\n\n" + boundary)
                (config / "RatOS.cfg").write_text("# generated baseline must remain unchanged\n")
                if mode in ('proforge', 'ratos'):
                    for fixture in (ROOT / 'tests/fixtures/proforge5').glob('so3_*.cfg'):
                        shutil.copy2(fixture, config / fixture.name)
                mainsail = home / "mainsail"
                mainsail.mkdir()
                (mainsail / "index.html").write_text("previous frontend")
                (mainsail / ".version").write_text("v2.18.2")
                (mainsail / "config.json").write_text('{"instances": ["my-printer"]}')
                ks = home / "KlipperScreen/panels"
                ks.mkdir(parents=True)
                cache = config / "tool-filament-sync/mainsail_source/cache"
                cache.mkdir(parents=True)
                (cache / "old-source").write_text("retain")
                if ratos:
                    macros = home / "ratos-configurator/configuration/macros"
                    macros.mkdir(parents=True)
                    for name in ("load_filament.cfg", "unload_filament.cfg"):
                        (macros / name).write_text("# RatOS macro presence fixture")
                shims = home / "bin"
                shims.mkdir()
                for name in ("sudo", "systemctl", "getent"):
                    path = shims / name
                    path.write_text(SHIM)
                    path.chmod(0o755)
                env = dict(os.environ, TEST_PRINTER_HOME=str(home), SUDO_USER="testprinter",
                           PATH=str(shims) + ":" + os.environ["PATH"],
                           MAINSAIL_DIR=str(mainsail), KLIPPERSCREEN_DIR=str(ks.parent),
                           RATOS_CONFIGURATOR_DIR=str(home / "ratos-configurator"))
                for iteration in range(2):
                    if iteration == 1:
                        panel = home / "Klipper-Filament-Sync/klipperscreen"
                        panel.mkdir(parents=True)
                        (panel / "maeve_filaments.py").write_text("# newer cloud plugin")
                        (panel / "menu.conf").write_text("# newer menu")
                        from panel_integration import point_panel
                        point_panel(home, config / "tool-filament-sync", ks.parent, panel)
                    result = subprocess.run(["bash", str(source / "install.sh")], env=env, text=True,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    self.assertEqual(result.returncode, 0, result.stdout)
                text = (config / "printer.cfg").read_text()
                if ratos:
                    for fixture in (ROOT / 'tests/fixtures/proforge5').glob('so3_*.cfg'):
                        self.assertEqual((config / fixture.name).read_bytes(), fixture.read_bytes())
                if mode == 'proforge':
                    for tool in range(5):
                        sensor = config / f'so3_{tool}.cfg'
                        self.assertEqual(sensor.read_text().count('FILAMENT_SYNC_PROFILE_TEMP_V2'), 2)
                        self.assertEqual(len(list(config.glob(sensor.name + '.before-filament-sync-*'))), 1)
                self.assertTrue(text.endswith(boundary))
                self.assertEqual(text.count("[include tool-filament-sync/filament-sync.cfg]"), 1)
                self.assertEqual((config / "RatOS.cfg").read_text(), "# generated baseline must remain unchanged\n")
                self.assertEqual((mainsail / "config.json").read_text(), '{"instances": ["my-printer"]}')
                platform = (config / "tool-filament-sync/platform-integration.cfg").read_text()
                self.assertEqual("[include ratos-load-unload.cfg]" in platform, ratos)
                self.assertEqual((ks / "maeve_filaments.py").resolve(), panel / "maeve_filaments.py")
                self.assertEqual((ks / "maeve_filaments.py").read_text(), "# newer cloud plugin")
                self.assertEqual((config / "KlipperScreen.conf").read_text().count("[include "), 1)
                dropin = home / "system/etc/systemd/system/KlipperScreen.service.d/filament-sync.conf"
                self.assertIn(str(panel), dropin.read_text())
                self.assertFalse(cache.exists())
                self.assertEqual(len(list((home / ".cache/filament-sync").glob("retired-*/cache/old-source"))), 1)
                commands = [json.loads(line) for line in (home / "commands.jsonl").read_text().splitlines()]
                self.assertNotIn(["systemctl", "enable", "--now", "filament-sync-mainsail-guard.timer"], commands)
                self.assertNotIn(["systemctl", "start", "filament-sync-mainsail-guard.service"], commands)


if __name__ == "__main__":
    unittest.main()
