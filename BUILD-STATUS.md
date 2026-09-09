# Unified 2.0.1 validation

Source release for maevebaksa/Klipper-Filament-Sync. Published installer archives
replace this file with the GitHub Actions build and upstream commit identity.

Local installer and scheduler tests cover standard/RatOS fresh and repeat
installs, renamed-user paths, cache migration, SAVE_CONFIG preservation,
plugin-source retention, updater registration, legacy upstream-target blocking,
idle gating, and failed-update retry suppression. No physical printer has been
changed or tested by this build.

The frontend patch was previously validated on official Mainsail 2.19.0 commit
5fb9e77fb9f4e60cf0725d9dc7f57cf7b84bbd70, including lint, all 46 upstream unit
tests, TypeScript checking and production compilation. GitHub Actions reruns
these gates before it publishes a release. Source checkout alone is not an
installer: download the release archive containing the compiled frontend.
