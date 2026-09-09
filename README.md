# Klipper Filament Sync — unified 2.0.2

One filament-metadata integration for standard Klipper and RatOS. It adds the
filament controls to Mainsail and, when installed, KlipperScreen; synchronizes
material, color, nozzle temperature, and bed temperature with Moonraker
`lane_data`; and retains tool mapping for multi-tool printers while disabling it
for single-tool printers.

The Mainsail frontend is based on official Mainsail 2.19.0 and compiled by
GitHub Actions. Printer hosts download the tested frontend through Moonraker—no
Node.js, npm, Vite, TypeScript compilation, or frontend source cache runs on a
printer.

## Install

Run as the normal printer user while the printer is idle:

```bash
cd ~ && \
curl -fL 'https://github.com/maevebaksa/Klipper-Filament-Sync/releases/download/v2.19.0%2Bfs.2.0.2/filament-sync-unified-2.0.2.tar.gz' -o filament-sync-unified-2.0.2.tar.gz && \
tar -xzf filament-sync-unified-2.0.2.tar.gz && \
bash filament-sync-unified-2.0.2/install.sh --with-updates
```

Run `RESTART` in Mainsail, then hard-refresh the browser. Verify with:

```bash
~/printer_data/config/tool-filament-sync/verify.sh
```

The installer detects RatOS from its native load/unload macro files and selects
the matching integration. It preserves `config.json`, RatOS-generated files,
PID and other data below Klipper's `SAVE_CONFIG` boundary, and the currently
configured physical-tool count. It uses the current account's real home path,
including renamed printer users.

## Updates

Installation creates two Moonraker entries as applicable:

- The existing Mainsail updater is redirected to this repository's compiled
  releases while retaining its updater name and `persistent_files`.
- **filament-sync-panel** tracks only our KlipperScreen panel and menu from
  `~/Klipper-Filament-Sync`. It does not update upstream KlipperScreen.

The small scheduler checks about every six hours, with startup delay and jitter.
It installs at most one UI update per run and requires Klipper ready, print state
idle/complete/cancelled, `idle_timeout` Idle, every heater target zero, and
Moonraker's updater idle. Invalid, dirty, modified, anomalous, unknown, failed,
or downgrade candidates are skipped. It never requests Klipper, firmware,
RatOS Core, operating-system, or full-system updates.

GitHub checks official stable Mainsail every six hours. A new frontend is
published only after the patch applies cleanly and Mainsail lint, unit tests,
TypeScript checking, production compilation, archive validation, installer
tests, and checksums pass. If a future Mainsail release is incompatible, the
last successful release remains installed.

Inspect automatic checks with:

```bash
systemctl status filament-sync-auto-update.timer --no-pager
journalctl -u filament-sync-auto-update.service -n 60 --no-pager
```

To disable automatic checks, run
`sudo systemctl disable --now filament-sync-auto-update.timer`.
Manual Moonraker updates remain available.

Installation automatically disables the old local frontend compiler and moves
its cache outside Moonraker's watched configuration folder, resolving the
reboot compilation and overlapping-watch warning.

## Filament behavior

- `SET_TOOL_FILAMENT` stores material, color, nozzle temperature, and bed
  temperature for each physical tool.
- `FILAMENT_SYNC_STATUS` reports the saved metadata.
- RatOS installations retain native sensor callbacks and private motion
  routines; public load/unload entry points use the saved filament temperature
  when `TEMP` is omitted.
- Supported standard Orbiter configurations receive the corresponding saved
  temperature integration.
- KlipperScreen's panel is loaded from the independently updated plugin checkout
  and reapplied when KlipperScreen starts.
- Mainsail preserves the Drake tool-mapping workflow on toolchangers and avoids
  presenting tool mapping for one-filament machines.

## Credits

Special thanks to **Sarah** for
the original Mainsail toolchanger and tool-mapping work that this integration
builds upon.

This project also relies on Klipper by Kevin O'Connor and contributors,
Moonraker by Eric Callahan and contributors, and Mainsail by the Mainsail crew.

## Validation and source

Release 2.0.2 passed 15 installer/updater tests and all 46 upstream Mainsail unit
tests, plus lint, TypeScript, production-build, archive-integrity, and checksum
gates. See `BUILD-STATUS.md` and the release assets for exact source identities.

- [Source repository](https://github.com/maevebaksa/Klipper-Filament-Sync)
- [Latest release](https://github.com/maevebaksa/Klipper-Filament-Sync/releases/latest)
- [Moonraker update-manager configuration](https://moonraker.readthedocs.io/en/latest/configuration/#update-manager)
