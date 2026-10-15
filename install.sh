#!/usr/bin/env bash
set -euo pipefail

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

with_updates=0
if [[ ${1:-} == --with-updates && $# -eq 1 ]]; then
    with_updates=1
elif [[ $# -gt 0 ]]; then
    echo "Usage: ./install.sh [--with-updates]" >&2
    exit 1
fi

if [[ ${EUID} -eq 0 ]]; then
    target_user="${SUDO_USER:-}"
    if [[ -z ${target_user} || ${target_user} == root ]]; then
        echo "Run this installer as the normal printer user, not directly as root." >&2
        exit 1
    fi
else
    target_user="$(id -un)"
fi

user_home="$(getent passwd "${target_user}" | cut -d: -f6)"
if [[ -z ${user_home} || ! -d ${user_home} ]]; then
    echo "Could not determine the home directory for ${target_user}." >&2
    exit 1
fi

config_root="${user_home}/printer_data/config"
printer_config="${config_root}/printer.cfg"
install_root="${config_root}/tool-filament-sync"
klipperscreen_root="${KLIPPERSCREEN_DIR:-${user_home}/KlipperScreen}"
mainsail_root="${MAINSAIL_DIR:-${user_home}/mainsail}"
ratos_root="${RATOS_CONFIGURATOR_DIR:-${user_home}/ratos-configurator}"

copy_file() {
    local mode="$1"
    local source_path="$2"
    local destination_path="$3"
    if [[ "$(readlink -f "${source_path}")" == "$(readlink -m "${destination_path}")" ]]; then
        chmod "${mode}" "${destination_path}"
    else
        install -m "${mode}" "${source_path}" "${destination_path}"
    fi
}

if [[ ! -f ${printer_config} ]]; then
    echo "Missing ${printer_config}." >&2
    exit 1
fi
# Stop the timer first, then its whole service cgroup (npm, Vite and tsc).
for unit in filament-sync-mainsail-guard.timer filament-sync-mainsail-guard.service; do
    if systemctl cat "${unit}" >/dev/null 2>&1; then
        if [[ ${unit} == *.timer ]]; then
            sudo systemctl disable --now "${unit}"
        else
            sudo systemctl stop "${unit}"
        fi
    fi
done
if systemctl cat filament-sync-auto-update.service >/dev/null 2>&1; then
    sudo systemctl stop filament-sync-auto-update.service
fi
python3 "${source_root}/migrate_cache.py" --config-root "${config_root}" --home "${user_home}"

ratos_enabled=0
if [[ -f ${ratos_root}/configuration/macros/load_filament.cfg && \
      -f ${ratos_root}/configuration/macros/unload_filament.cfg ]]; then
    ratos_enabled=1
    echo "Detected RatOS; enabling RatOS-native load/unload integration"
else
    echo "Detected a standard Klipper installation"
fi

klipperscreen_enabled=0
if [[ -d ${klipperscreen_root}/panels ]]; then
    klipperscreen_enabled=1
else
    echo "KlipperScreen not found at ${klipperscreen_root}; skipped the touch panel"
fi

install -d -m 755 \
    "${install_root}/klipperscreen" \
    "${install_root}/mainsail" \
    "${install_root}/mainsail_frontend" \
    "${install_root}/systemd"
copy_file 644 "${source_root}/filament-sync.cfg" "${install_root}/filament-sync.cfg"
copy_file 644 "${source_root}/ratos-load-unload.cfg" "${install_root}/ratos-load-unload.cfg"
copy_file 644 "${source_root}/platform-mainline.cfg" "${install_root}/platform-mainline.cfg"
copy_file 644 "${source_root}/platform-ratos.cfg" "${install_root}/platform-ratos.cfg"
if [[ ${ratos_enabled} -eq 1 ]]; then
    copy_file 644 "${source_root}/platform-ratos.cfg" "${install_root}/platform-integration.cfg"
else
    copy_file 644 "${source_root}/platform-mainline.cfg" "${install_root}/platform-integration.cfg"
fi
copy_file 755 "${source_root}/filament_sync_bridge.py" "${install_root}/filament_sync_bridge.py"
copy_file 644 "${source_root}/klipperscreen/maeve_filaments.py" "${install_root}/klipperscreen/maeve_filaments.py"
copy_file 644 "${source_root}/klipperscreen/menu.conf" "${install_root}/klipperscreen/menu.conf"
copy_file 644 "${source_root}/systemd/filament-sync.service.in" "${install_root}/systemd/filament-sync.service.in"
copy_file 644 "${source_root}/systemd/klipperscreen-dropin.conf.in" "${install_root}/systemd/klipperscreen-dropin.conf.in"
copy_file 644 "${source_root}/systemd/mainsail-guard.service.in" "${install_root}/systemd/mainsail-guard.service.in"
copy_file 644 "${source_root}/systemd/mainsail-guard.timer.in" "${install_root}/systemd/mainsail-guard.timer.in"
copy_file 644 "${source_root}/mainsail/index.html" "${install_root}/mainsail/index.html"
copy_file 644 "${source_root}/mainsail/style.css" "${install_root}/mainsail/style.css"
copy_file 644 "${source_root}/mainsail/app.js" "${install_root}/mainsail/app.js"
copy_file 644 "${source_root}/mainsail_frontend/mainsail.zip" "${install_root}/mainsail_frontend/mainsail.zip"
install -d -m 755 "${install_root}/mainsail_source"
copy_file 644 "${source_root}/mainsail_source/filament-sync.patch" "${install_root}/mainsail_source/filament-sync.patch"
copy_file 644 "${source_root}/README.md" "${install_root}/README.md"
copy_file 755 "${source_root}/orbiter_temperature_patch.py" "${install_root}/orbiter_temperature_patch.py"
copy_file 755 "${source_root}/repair_printer_config.py" "${install_root}/repair_printer_config.py"
copy_file 755 "${source_root}/install.sh" "${install_root}/install.sh"
copy_file 755 "${source_root}/install_mainsail_overlay.py" "${install_root}/install_mainsail_overlay.py"
copy_file 755 "${source_root}/rebuild_mainsail.py" "${install_root}/rebuild_mainsail.py"
copy_file 755 "${source_root}/migrate_cache.py" "${install_root}/migrate_cache.py"
copy_file 755 "${source_root}/auto_update.py" "${install_root}/auto_update.py"
copy_file 755 "${source_root}/setup_updates.py" "${install_root}/setup_updates.py"
copy_file 755 "${source_root}/panel_integration.py" "${install_root}/panel_integration.py"
copy_file 755 "${source_root}/frontend_preflight.py" "${install_root}/frontend_preflight.py"
copy_file 644 "${source_root}/VERSION" "${install_root}/VERSION"
copy_file 755 "${source_root}/verify.sh" "${install_root}/verify.sh"

timestamp="$(date +%Y%m%d-%H%M%S)"
printer_backup="${printer_config}.before-filament-sync-${timestamp}"
backup_printer_config() {
    if [[ ! -e ${printer_backup} ]]; then
        install -m 644 "${printer_config}" "${printer_backup}"
    fi
}

klipper_include='[include tool-filament-sync/filament-sync.cfg]'
printer_repair_options=(
    --include "${klipper_include}"
    --variables-file "${config_root}/variables.cfg"
)
if grep -RqsE '^[[:space:]]*\[save_variables\][[:space:]]*($|#)' \
    --include='*.cfg' "${config_root}"; then
    printer_repair_options+=(--external-save-variables)
fi
if ! "${install_root}/repair_printer_config.py" --check "${printer_repair_options[@]}" "${printer_config}"; then
    backup_printer_config
    "${install_root}/repair_printer_config.py" "${printer_repair_options[@]}" "${printer_config}"
fi

orbiter_config=""
if [[ ${ratos_enabled} -eq 0 ]]; then
orbiter_candidates=(
    "${config_root}/toolchanger/filament/orbiter-smart-sensors-ktce.cfg"
    "${config_root}/OrbiterSensor.cfg"
    "${config_root}/Orbiter2_SmartSensor.cfg"
)
while IFS= read -r -d '' candidate; do
    orbiter_candidates+=("${candidate}")
done < <(
    find "${config_root}" -type f \
        \( -iname 'OrbiterSensor.cfg' -o -iname 'Orbiter2_SmartSensor.cfg' -o -iname '*orbiter*smart*sensor*.cfg' -o -iname 'so3_[0-9]*.cfg' \) \
        ! -path "${install_root}/*" \
        ! -name '*.before-filament-sync-*' \
        ! -name '*.bak' \
        -print0
)
for candidate in "${orbiter_candidates[@]}"; do
    if [[ -f ${candidate} ]] && "${install_root}/orbiter_temperature_patch.py" --check "${candidate}" 2>/dev/null; then
        orbiter_config="${candidate}"
        if grep -Fq 'FILAMENT_SYNC_PROFILE_TEMP_V2' "${orbiter_config}" || \
            { grep -Fq 'FILAMENT_SYNC_PROFILE_TEMP_BEGIN' "${orbiter_config}" && \
              ! grep -Fq 'filament_sync_load_target' "${orbiter_config}"; }; then
            echo "Orbiter saved-temperature integration is already installed in ${orbiter_config}"
        else
            install -m 644 "${orbiter_config}" "${orbiter_config}.before-filament-sync-${timestamp}"
            "${install_root}/orbiter_temperature_patch.py" "${orbiter_config}"
            echo "Integrated saved filament temperatures into ${orbiter_config}"
        fi
    fi
done
if [[ -z ${orbiter_config} ]]; then
    echo "No supported Orbiter sensor file found; skipped temperature integration"
fi
else
    echo "RatOS owns the filament sensor callbacks; no Orbiter sensor file was edited"
fi

panel_root="${install_root}/klipperscreen"
if [[ -f ${install_root}/klipperscreen-panel-root ]]; then
    saved_panel_root="$(cat "${install_root}/klipperscreen-panel-root")"
    if [[ -f ${saved_panel_root}/maeve_filaments.py && -f ${saved_panel_root}/menu.conf ]]; then
        panel_root="${saved_panel_root}"
    fi
fi
if [[ ${klipperscreen_enabled} -eq 1 ]]; then
    python3 "${install_root}/panel_integration.py" "${user_home}" "${install_root}" "${klipperscreen_root}" "${panel_root}"
fi

mainsail_enabled=0
if [[ -f ${mainsail_root}/index.html ]]; then
    mainsail_enabled=1
    original_backup_marker="${install_root}/.mainsail-original-backed-up"
    if [[ ! -e ${original_backup_marker} ]]; then
        mainsail_backup_root="${user_home}/mainsail-patch-backups/${timestamp}"
        install -d -m 755 "${mainsail_backup_root}"
        cp -a "${mainsail_root}" "${mainsail_backup_root}/mainsail"
        : > "${original_backup_marker}"
        echo "Backed up the existing Mainsail frontend to ${mainsail_backup_root}/mainsail"
    fi
    # Never replace an existing cloud-managed frontend during reinstall.
    if [[ -f ${install_root}/cloud-updates.conf ]] && grep -qE '^repo[[:space:]]*[:=]' "${install_root}/cloud-updates.conf"; then
        echo "Cloud updater already configured; retained the installed frontend"
    elif python3 "${source_root}/frontend_preflight.py" "${mainsail_root}" "${install_root}/mainsail_frontend/mainsail.zip"; then
    "${install_root}/install_mainsail_overlay.py" \
        --mainsail-root "${mainsail_root}" \
        --config-root "${config_root}" \
        --overlay-root "${install_root}/mainsail" \
        --frontend-zip "${install_root}/mainsail_frontend/mainsail.zip" \
        --source-version "v2.19.0" \
        --state-file "${install_root}/mainsail_frontend/build-state.json" \
        --patch-file "${install_root}/mainsail_source/filament-sync.patch"
    else
        echo "Retained existing frontend; configure cloud releases for the next compatible update"
    fi
else
    echo "Mainsail web root not found at ${mainsail_root}; skipped the integrated dashboard frontend"
fi

service_tmp="$(mktemp)"
dropin_tmp="$(mktemp)"
guard_service_tmp="$(mktemp)"
guard_timer_tmp="$(mktemp)"
trap 'rm -f "${service_tmp}" "${dropin_tmp}" "${guard_service_tmp}" "${guard_timer_tmp}"' EXIT
sed \
    -e "s|@@USER@@|${target_user}|g" \
    -e "s|@@INSTALL_ROOT@@|${install_root}|g" \
    "${install_root}/systemd/filament-sync.service.in" > "${service_tmp}"

if [[ ${klipperscreen_enabled} -eq 1 ]]; then
    ks_unit="KlipperScreen.service"
    if ! systemctl cat "${ks_unit}" >/dev/null 2>&1; then
        if systemctl cat klipperscreen.service >/dev/null 2>&1; then
            ks_unit="klipperscreen.service"
        else
            echo "Could not find the KlipperScreen systemd service; skipped its restart and symlink guard"
            klipperscreen_enabled=0
        fi
    fi
fi

if [[ ${klipperscreen_enabled} -eq 1 ]]; then
sed \
    -e "s|@@INSTALL_ROOT@@|${install_root}|g" \
    -e "s|@@PANEL_ROOT@@|${panel_root}|g" \
    -e "s|@@KLIPPERSCREEN_ROOT@@|${klipperscreen_root}|g" \
    "${install_root}/systemd/klipperscreen-dropin.conf.in" > "${dropin_tmp}"
fi

sed \
    -e "s|@@USER@@|${target_user}|g" \
    -e "s|@@INSTALL_ROOT@@|${install_root}|g" \
    -e "s|@@MAINSAIL_ROOT@@|${mainsail_root}|g" \
    -e "s|@@CONFIG_ROOT@@|${config_root}|g" \
    "${install_root}/systemd/mainsail-guard.service.in" > "${guard_service_tmp}"
cp "${install_root}/systemd/mainsail-guard.timer.in" "${guard_timer_tmp}"

sudo install -m 644 "${service_tmp}" /etc/systemd/system/filament-sync.service
if [[ ${klipperscreen_enabled} -eq 1 ]]; then
sudo install -d -m 755 "/etc/systemd/system/${ks_unit}.d"
sudo install -m 644 "${dropin_tmp}" "/etc/systemd/system/${ks_unit}.d/filament-sync.conf"
fi
if [[ ${mainsail_enabled} -eq 1 ]]; then
    sudo install -m 644 "${guard_service_tmp}" /etc/systemd/system/filament-sync-mainsail-guard.service
    sudo install -m 644 "${guard_timer_tmp}" /etc/systemd/system/filament-sync-mainsail-guard.timer
fi
sudo systemctl daemon-reload
sudo systemctl enable --now filament-sync.service
# Legacy timer stays disabled. setup_updates.py installs the download timer
# only after verifying a real published release repository.
if [[ ${klipperscreen_enabled} -eq 1 ]]; then
    sudo systemctl restart "${ks_unit}"
fi

echo
echo "Installed the update-resistant filament bridge in ${install_root}."
if [[ ${klipperscreen_enabled} -eq 1 ]]; then
    echo "KlipperScreen has been restarted; Tool Filaments should now be visible."
fi
if [[ ${mainsail_enabled} -eq 1 ]]; then
    echo "Mainsail now has dashboard filament editing and the Drake7707 mapping UI."
    echo "Single-filament files bypass tool mapping; multi-filament files retain it."
    echo "Local compilation is disabled. Use setup_updates.py for cloud releases and optional KlipperScreen updates."
fi
if [[ ${ratos_enabled} -eq 1 ]]; then
    echo "RatOS LOAD_FILAMENT and UNLOAD_FILAMENT use the saved profile temperature when TEMP is omitted."
    echo "RatOS sensor callbacks and private motion routines remain unchanged."
else
    echo "Standard Klipper mode is active; supported Orbiter macros use saved profile temperatures."
fi
echo "Run RESTART once in Mainsail to load SET_TOOL_FILAMENT, then run:"
echo "  ${install_root}/verify.sh"
echo "Restart Moonraker while idle to clear any cached inotify overlap warning."

if [[ ${with_updates} -eq 1 ]]; then
    update_options=(--repo maevebaksa/Klipper-Filament-Sync --mainsail-dir "${mainsail_root}" --klipperscreen-dir "${klipperscreen_root}")
    if [[ ${klipperscreen_enabled} -eq 1 ]]; then
        update_options+=(--klipperscreen-plugin)
    fi
    if [[ ${EUID} -eq 0 ]]; then
        sudo -u "${target_user}" -H python3 "${install_root}/setup_updates.py" "${update_options[@]}"
    else
        python3 "${install_root}/setup_updates.py" "${update_options[@]}"
    fi
fi
