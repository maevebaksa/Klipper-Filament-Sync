# Filament-sync unified 2.0.1

One installer for standard Klipper and RatOS. The bundled frontend is based on
Mainsail 2.19.0. Compilation moves to GitHub Actions; printer hosts download the
compiled web assets through Moonraker. No Node.js, npm, Vite, TypeScript, or build
cache is needed on the printer for this update path.

Source and release worker: https://github.com/maevebaksa/Klipper-Filament-Sync

Read BUILD-STATUS.md for this archive’s build identity.

## Install or migrate from 1.x

Run as your normal printer user while the printer is idle:

```bash
cd ~ && tar -xzf filament-sync-unified-2.0.1.tar.gz && cd filament-sync-unified-2.0.1 && bash install.sh --with-updates
```

The installer stops and disables the old `filament-sync-mainsail-guard.timer`,
stops its running service and child processes, and replaces the old builder with
a harmless compatibility stub. It moves any legacy
`printer_data/config/tool-filament-sync/mainsail_source/cache` directory to
`~/.cache/filament-sync/retired-...`. It preserves the contents, including
symlinks. A cache already outside the config tree is left alone. This addresses
the overlapping inotify-root warning caused by the old compiler source tree.

Restart Moonraker once while idle to discard its old watches/warnings:

```bash
sudo systemctl restart moonraker
```

Run `RESTART` in Mainsail to load macro changes, then hard-refresh your browser.

```bash
~/printer_data/config/tool-filament-sync/verify.sh
```

RatOS is detected from its installed native load/unload macro files. The same
existing temperature wrappers and standard Orbiter integrations are retained.
The installer preserves `config.json`, the Klipper SAVE_CONFIG boundary, and
RatOS-generated configuration. It creates an initial frontend backup under
`~/mainsail-patch-backups/`. Host paths come from the current user's account,
including installations using a renamed printer username.

Optional path overrides: `MAINSAIL_DIR`, `KLIPPERSCREEN_DIR`, and
`RATOS_CONFIGURATOR_DIR`. The standard config root is `~/printer_data/config`.

The offline bundle installs only over a recognized same-or-older Mainsail
version. It never replaces a frontend already configured for cloud updates.
Until the cloud repository is configured, a normal upstream Mainsail update can
remove the custom panel. The retired builder will not restore or recompile it.

## GitHub worker

The worker lives in `maevebaksa/Klipper-Filament-Sync`. Its Actions workflow
publishes compiled releases used by every printer. For a fork, copy this source,
enable Actions, and pass the fork’s repository to `setup_updates.py`.

The workflow checks the latest official stable Mainsail release every six hours.
It resolves a source commit, checks out that exact upstream tag, verifies that
the tag did not move, applies the patch with `git apply --check`, installs the
locked dependencies, runs lint, unit tests, an explicit TypeScript check, and
the production build. A failure publishes nothing. The last successful release
stays available; an incompatible future Mainsail release requires a patch update.

Release tags and titles use `v2.19.0+fs.2.0.1`. Bump `VERSION` when changing this
package or its patch; an already published release is never overwritten. The
ZIP is uploaded first to support Moonraker's asset-selection and rollback
behavior. All assets are uploaded to a draft before publication. An interrupted
draft must be inspected and deleted before a retry; do not publish it manually.

The build job has read-only repository permissions; publication happens in a
separate job with write permission. No printer credentials are stored in GitHub.
Release assets include `mainsail.zip`, the unified installer, the complete patched
Mainsail source, a commit/patch manifest, and SHA256SUMS. The publish job verifies
the checksums. Printer downloads use Moonraker's normal HTTPS ZIP updater;
Moonraker does not independently verify the separate SHA256SUMS file.

GitHub may disable scheduled workflows in inactive public repositories; check
the Actions page if the release feed stops advancing. GitHub schedules are not
guaranteed to run at an exact time.

## Connect each printer to cloud releases

The recommended `install.sh --with-updates` command registers cloud Mainsail
and, when installed, our KlipperScreen plugin with Moonraker automatically.
To configure just the Mainsail updater separately:

```bash
python3 ~/printer_data/config/tool-filament-sync/setup_updates.py --repo maevebaksa/Klipper-Filament-Sync
```

This verifies a compatible public release exists, finds the existing Moonraker
updater that targets your Mainsail directory, and overrides that same updater.
It does not create a second updater for the same path. It preserves existing
`persistent_files` and includes `config.json`. It also updates Mainsail's local
release metadata so Moonraker will use the selected repository instead of
falling back to the official one. Existing config and metadata are backed up.

The setup restarts Moonraker only while the printer is idle and enables the
small `filament-sync-auto-update.timer`. No firmware, Klipper, RatOS Core, or
system-wide updates are requested by this timer.

## Our KlipperScreen plugin updates

The separate `filament-sync-panel` entry appears in Moonraker’s Update Manager.
It tracks this repository’s `main` branch in `~/Klipper-Filament-Sync`, outside
Moonraker’s watched config tree. KlipperScreen loads our panel and menu directly
from its `klipperscreen/` directory. Reinstallation preserves that source.

Enable just our plugin updater:

```bash
python3 ~/printer_data/config/tool-filament-sync/setup_updates.py --plugin-only
```

Or configure both UI components:

```bash
python3 ~/printer_data/config/tool-filament-sync/setup_updates.py --repo maevebaksa/Klipper-Filament-Sync --klipperscreen-plugin
```

A small `filament-sync-panel.service` reloads the running screen after either a
manual or scheduled plugin update. Setup adds only that service to Moonraker’s
existing `printer_data/moonraker.asvc` list. It does not alter trusted clients,
API keys, or authentication code. No upstream KlipperScreen update is requested;
its separate, user-owned updater is retained if present. No dependency-install
scripts run for a plugin update. The repository contains the unified installer
source too, but updating this checkout does not reinstall the bridge or macros.

This corrects the 2.0.0 candidate’s mistaken upstream KlipperScreen target.
The new scheduler blocks that old target even before setup is rerun. Setup
removes obsolete upstream entries only from our own generated configuration.
An old invalid API key remains a separate Switchwire configuration issue.

For non-default locations pass `--mainsail-dir` or `--klipperscreen-dir` to setup,
matching the paths used for installation. `--moonraker-url` accepts a local
Moonraker address; `--api-key-file` accepts an existing private key file if local
trusted access is unavailable.

## Scheduling and failures

The timer checks about every six hours with up to twenty minutes of jitter,
starting at least fifteen minutes after boot. It reads Moonraker's cached update
status. Setup enables Moonraker auto-refresh and requests a six-hour refresh
interval for the selected UI components, retaining Moonraker's existing refresh
window (normally overnight). Discovery can therefore wait for that window. A reboot alone
does not cause compilation or reinstallation.

Updates require Klipper ready, print state standby/complete/cancelled,
`idle_timeout` state Idle, and all heater targets zero. Busy, printing, paused,
unreachable, unauthorized, invalid, or modified installations are skipped.
Checks are repeated just before installation, and Moonraker serializes updates
and rejects print-time requests. These checks are not a reservation preventing
a person from starting a print at the same instant; avoid starting jobs while
an update is being installed. Only one UI component updates per timer invocation.

An attempted remote version/hash is recorded before installation in
`~/.local/state/filament-sync/updates.json`. Failed, interrupted, and already
attempted targets are not repeatedly retried. A newer remote target can be
attempted on a later timer tick. Automatic Mainsail downgrades are blocked.

Inspect:

```bash
systemctl status filament-sync-auto-update.timer --no-pager
journalctl -u filament-sync-auto-update.service -n 60 --no-pager
cat ~/.local/state/filament-sync/updates.json
```

After resolving a failure, explicitly retry while idle:

```bash
python3 ~/printer_data/config/tool-filament-sync/auto_update.py --settings ~/printer_data/config/tool-filament-sync/auto-update.json --retry
```

Disable automatic UI updates while retaining manual Moonraker updates:

```bash
sudo systemctl disable --now filament-sync-auto-update.timer
```

Moonraker provides manual rollback for previously installed cloud releases.
The initial migration from an official frontend has a local backup instead;
the new repository has no matching release for that pre-migration version.
Do not use forced git recovery on the plugin checkout to resolve a modified checkout
without reviewing and saving its local changes.

## References

- [Official Mainsail v2.19.0](https://github.com/mainsail-crew/mainsail/releases/tag/v2.19.0)
- [Moonraker frontend updater configuration](https://moonraker.readthedocs.io/en/latest/configuration/#web-type-front-end-configuration)
- [Moonraker update API](https://moonraker.readthedocs.io/en/latest/external_api/update_manager/)
- [Official KlipperScreen update-manager setup](https://klipperscreen.readthedocs.io/en/latest/Installation/#moonraker-configuration)
- [GitHub scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
