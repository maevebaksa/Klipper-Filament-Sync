#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -eq 0 ]]; then
    target_user="${SUDO_USER:-root}"
else
    target_user="$(id -un)"
fi
user_home="$(getent passwd "${target_user}" | cut -d: -f6)"
panel_path="${user_home}/KlipperScreen/panels/maeve_filaments.py"
config_root="${user_home}/printer_data/config"
install_root="${config_root}/tool-filament-sync"
printer_config="${config_root}/printer.cfg"
panel_root="${install_root}/klipperscreen"
if [[ -f ${install_root}/klipperscreen-panel-root ]]; then
    panel_root="$(cat "${install_root}/klipperscreen-panel-root")"
fi

if [[ -f ${user_home}/printer_data/config/KlipperScreen.conf ]]; then
    klipperscreen_config="${user_home}/printer_data/config/KlipperScreen.conf"
elif [[ -f ${user_home}/.config/KlipperScreen/KlipperScreen.conf ]]; then
    klipperscreen_config="${user_home}/.config/KlipperScreen/KlipperScreen.conf"
elif [[ -f ${user_home}/KlipperScreen/KlipperScreen.conf ]]; then
    klipperscreen_config="${user_home}/KlipperScreen/KlipperScreen.conf"
else
    klipperscreen_config=""
fi

echo "Bridge service:"
systemctl --no-pager --full status filament-sync.service 2>&1 | sed -n '1,12p' || true
echo
echo "KlipperScreen panel link:"
if [[ ! -d ${user_home}/KlipperScreen/panels ]]; then
    echo "KlipperScreen is not installed; skipped"
elif [[ -e ${panel_path} || -L ${panel_path} ]]; then
    ls -l "${panel_path}"
else
    echo "MISSING: ${panel_path}"
fi
echo
echo "KlipperScreen menu source:"
sed -n '1,20p' "${panel_root}/menu.conf"
echo
echo "Active KlipperScreen configuration include:"
if [[ -n ${klipperscreen_config} ]]; then
    if ! grep -Fn "${panel_root}/menu.conf" "${klipperscreen_config}"; then
        echo "MISSING include in ${klipperscreen_config}"
    fi
else
    echo "No user KlipperScreen.conf was found"
fi
echo
echo "Detected physical-tool count:"
curl -fsS 'http://127.0.0.1:7125/printer/objects/query?save_variables' | \
    python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("result",{}).get("status",{}).get("save_variables",{}).get("variables",{}).get("filament_tool_count","not saved yet"))'
echo
echo "Moonraker lane_data:"
curl -fsS 'http://127.0.0.1:7125/server/database/item?namespace=lane_data'
echo
echo
echo "Legacy local-build timer (must be disabled):"
systemctl is-enabled filament-sync-mainsail-guard.timer 2>/dev/null || true
echo "Cloud/KlipperScreen auto-update timer (optional):"
systemctl --no-pager --full status filament-sync-auto-update.timer 2>&1 | sed -n '1,10p' || true
if [[ -f ${user_home}/.local/state/filament-sync/updates.json ]]; then
    cat "${user_home}/.local/state/filament-sync/updates.json"
fi
if [[ -f ${user_home}/mainsail/.version ]]; then
    echo "Installed Mainsail version: $(cat "${user_home}/mainsail/.version")"
fi
if [[ -f ${install_root}/mainsail_frontend/build-state.json ]]; then
    python3 - "${install_root}/mainsail_frontend/build-state.json" <<'PY'
import json
import sys

state = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"Frontend patch state: {state.get('status', 'unknown')} for {state.get('version', 'unknown')}")
if state.get("error"):
    print(f"Historical 1.x build error (local builds are retired): {state['error']}")
PY
fi
echo
echo "Klipper SAVE_CONFIG boundary safety:"
repair_options=(
    --check
    --include '[include tool-filament-sync/filament-sync.cfg]'
    --variables-file "${config_root}/variables.cfg"
)
if grep -RqsE '^[[:space:]]*\[save_variables\][[:space:]]*($|#)' \
    --include='*.cfg' "${config_root}"; then
    repair_options+=(--external-save-variables)
fi
python3 "${install_root}/repair_printer_config.py" "${repair_options[@]}" \
    "${printer_config}" && echo "OK: filament-sync include is above SAVE_CONFIG" || \
    echo "NEEDS REPAIR: rerun install.sh before restarting Klipper"
echo
if grep -Fq '[include ratos-load-unload.cfg]' "${install_root}/platform-integration.cfg"; then
    echo "Platform integration: RatOS"
    if grep -Fq 'RATOS_FILAMENT_SYNC_OVERRIDE_V1' "${install_root}/ratos-load-unload.cfg"; then
        echo "OK: saved temperatures feed RatOS LOAD_FILAMENT and UNLOAD_FILAMENT"
    else
        echo "MISSING: rerun install.sh"
    fi
    exit 0
fi

echo "Platform integration: standard Klipper"
echo "Orbiter saved-temperature integration:"
orbiter_matches=()
while IFS= read -r -d '' path; do
    orbiter_matches+=("${path}")
done < <(
    find "${config_root}" -type f -name '*.cfg' \
        ! -path "${install_root}/*" \
        ! -name '*.before-filament-sync-*' \
        -exec grep -lZ 'FILAMENT_SYNC_PROFILE_TEMP_BEGIN' {} + 2>/dev/null
)
if [[ ${#orbiter_matches[@]} -gt 0 ]]; then
    for orbiter_config in "${orbiter_matches[@]}"; do
        marker_count="$(grep -Fc 'FILAMENT_SYNC_PROFILE_TEMP_BEGIN' "${orbiter_config}" || true)"
        if [[ ${marker_count} == 2 ]]; then
            if grep -Fq 'FILAMENT_SYNC_PROFILE_TEMP_V2' "${orbiter_config}"; then
                echo "OK: stock Orbiter always-heat load/unload integration in ${orbiter_config}"
            else
                echo "OK: saved load/unload temperatures in ${orbiter_config}"
            fi
        else
            echo "MISSING or partial: expected two integration markers in ${orbiter_config}"
        fi
    done
else
    echo "No patched stock or KTC Orbiter sensor configuration found"
fi
