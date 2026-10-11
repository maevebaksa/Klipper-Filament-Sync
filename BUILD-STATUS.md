# Unified 2.0.4 validation

Source release for maevebaksa/Klipper-Filament-Sync. Published installer archives
replace this file with the GitHub Actions build and upstream commit identity.

Local installer and scheduler tests cover standard/RatOS fresh and repeat
installs, renamed-user paths, cache migration, SAVE_CONFIG preservation,
plugin-source retention, updater registration, legacy upstream-target blocking,
idle gating, and failed-update retry suppression. No physical printer has been
changed or tested by this build.

The frontend patch was previously validated on official Mainsail 2.19.0 commit
5fb9e77fb9f4e60cf0725d9dc7f57cf7b84bbd70, including lint, 59 upstream and calibration unit
tests, TypeScript checking and production compilation. Calibration tests exercise
reload/reconnect status recovery, persisted camera selection, stale response
rejection, physical tool confirmation gating, camera correction controls,
camera clearance and the existing Mainsail
Moonraker socket; no private port or LAN API is used. GitHub Actions reruns
these gates before it publishes a release. Source checkout alone is not an
installer: download the release archive containing the compiled frontend.
