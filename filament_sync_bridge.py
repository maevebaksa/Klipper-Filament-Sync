#!/usr/bin/env python3
"""Mirror Klipper save_variables filament metadata into Moonraker lane_data."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


LOG = logging.getLogger("filament-sync")
EMPTY_MATERIALS = {"", "EMPTY", "NONE", "UNKNOWN", "UNSET"}
HEX_COLOR = re.compile(r"^[0-9A-F]{6}([0-9A-F]{2})?$")
TOOL_OBJECT = re.compile(r"^tool\s+T(\d+)$", re.IGNORECASE)
TOOL_MACRO = re.compile(r"^gcode_macro\s+T(\d+)$", re.IGNORECASE)
EXTRUDER_OBJECT = re.compile(r"^extruder(\d*)$", re.IGNORECASE)
LANE_KEY = re.compile(r"^tool(\d+)$", re.IGNORECASE)
MAX_TOOL_COUNT = 32


class MoonrakerError(RuntimeError):
    """A retryable Moonraker communication or response error."""


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(512).decode("utf-8", errors="replace")
        raise MoonrakerError(f"HTTP {exc.code} from {path}: {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise MoonrakerError(f"Moonraker request failed for {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise MoonrakerError(f"Moonraker returned a non-object response for {path}")
    if "error" in parsed:
        raise MoonrakerError(f"Moonraker returned an error for {path}: {parsed['error']}")
    return parsed


def list_value(values: Any, index: int, default: Any) -> Any:
    if isinstance(values, (list, tuple)) and index < len(values):
        return values[index]
    return default


def normalize_material(value: Any) -> str:
    material = str(value if value is not None else "").strip().upper()
    return "" if material in EMPTY_MATERIALS else material


def normalize_color(value: Any) -> str:
    color = str(value if value is not None else "").strip().upper()
    if color.startswith("#"):
        color = color[1:]
    if color.startswith("0X"):
        color = color[2:]
    if not HEX_COLOR.fullmatch(color):
        return "#000000"
    return f"#{color}"


def normalize_temperature(value: Any) -> int | float | None:
    try:
        temperature = float(value)
    except (TypeError, ValueError):
        return None
    if temperature <= 0:
        return None
    if temperature.is_integer():
        return int(temperature)
    return round(temperature, 2)


def get_saved_variables(base_url: str) -> dict[str, Any]:
    response = request_json(base_url, "/printer/objects/query?save_variables")
    try:
        variables = response["result"]["status"]["save_variables"]["variables"]
    except (KeyError, TypeError) as exc:
        raise MoonrakerError("Klipper save_variables is not available yet") from exc
    if not isinstance(variables, dict):
        raise MoonrakerError("Klipper save_variables.variables is not an object")
    return variables


def get_printer_objects(base_url: str) -> list[str]:
    response = request_json(base_url, "/printer/objects/list")
    objects = response.get("result", {}).get("objects", [])
    if not isinstance(objects, list):
        raise MoonrakerError("Klipper object list is not available yet")
    return [str(name) for name in objects]


def _count_matching_tools(objects: list[str], pattern: re.Pattern[str]) -> int:
    indices = {
        int(match.group(1) or 0)
        for name in objects
        if (match := pattern.fullmatch(name.strip())) is not None
    }
    if not indices or 0 not in indices:
        return 0
    return min(max(indices) + 1, MAX_TOOL_COUNT)


def detect_tool_count(variables: dict[str, Any], objects: list[str]) -> int:
    # ProForge ships five activation macros even when fewer heads are enabled.
    # Its physical pickup commands use one-based PH numbers, not Tn.
    names = {name.strip().upper() for name in objects}
    if "GCODE_MACRO DOCK" in names and "GCODE_MACRO SELECT_PH1" in names and not any(
        TOOL_OBJECT.fullmatch(name.strip()) for name in objects
    ):
        try:
            active = int(variables.get("active_tools", 0))
        except (TypeError, ValueError):
            active = 0
        if 1 <= active <= 5 and all(
            f"GCODE_MACRO SELECT_PH{tool + 1}" in names for tool in range(active)
        ):
            return active
    # Native KTC tool objects are authoritative. Tn macros cover other
    # toolchangers; extruder/extruder1/... covers ordinary single- and
    # multi-extruder Klipper printers without any toolchanger macros.
    for pattern in (TOOL_OBJECT, TOOL_MACRO, EXTRUDER_OBJECT):
        count = _count_matching_tools(objects, pattern)
        if count:
            return count

    try:
        saved_count = int(variables.get("filament_tool_count", 0))
    except (TypeError, ValueError):
        saved_count = 0
    if 1 <= saved_count <= MAX_TOOL_COUNT:
        return saved_count

    primary_lengths = [
        len(values)
        for values in (
            variables.get("filament_types"),
            variables.get("filament_colors"),
        )
        if isinstance(values, (list, tuple))
    ]
    return min(max(primary_lengths, default=1), MAX_TOOL_COUNT)


def save_tool_count(base_url: str, variables: dict[str, Any], tool_count: int) -> None:
    try:
        current = int(variables.get("filament_tool_count", 0))
    except (TypeError, ValueError):
        current = 0
    if current == tool_count:
        return
    request_json(
        base_url,
        "/printer/gcode/script",
        method="POST",
        payload={"script": f"SAVE_VARIABLE VARIABLE=filament_tool_count VALUE={tool_count}"},
    )
    LOG.info("Detected %d physical tool(s)", tool_count)


def build_lanes(
    variables: dict[str, Any], tool_count: int | None = None
) -> dict[str, dict[str, Any]]:
    materials = variables.get("filament_types", [])
    colors = variables.get("filament_colors", [])
    nozzle_temps = variables.get("filament_nozzle_temps", [])
    bed_temps = variables.get("filament_bed_temps", [])
    temp_materials = variables.get("filament_temp_materials", [])

    if tool_count is None:
        tool_count = max(
            len(materials) if isinstance(materials, (list, tuple)) else 0,
            len(colors) if isinstance(colors, (list, tuple)) else 0,
            1,
        )

    lanes: dict[str, dict[str, Any]] = {}
    for tool in range(tool_count):
        material = normalize_material(list_value(materials, tool, "UNSET"))
        lane: dict[str, Any] = {
            "lane": str(tool),
            "material": material,
            "color": normalize_color(list_value(colors, tool, "000000")),
        }

        tagged_material = normalize_material(list_value(temp_materials, tool, "UNSET"))
        if material and tagged_material == material:
            nozzle = normalize_temperature(list_value(nozzle_temps, tool, 0))
            bed = normalize_temperature(list_value(bed_temps, tool, 0))
            if nozzle is not None:
                lane["nozzle_temp"] = nozzle
            if bed is not None:
                lane["bed_temp"] = bed

        lanes[f"tool{tool}"] = lane
    return lanes


def get_lane_data(base_url: str) -> dict[str, Any]:
    path = f"/server/database/item?namespace={quote('lane_data')}"
    try:
        response = request_json(base_url, path)
    except MoonrakerError as exc:
        if "HTTP 404" in str(exc):
            return {}
        raise
    value = response.get("result", {}).get("value", {})
    return value if isinstance(value, dict) else {}


def write_lane(base_url: str, key: str, value: dict[str, Any]) -> None:
    request_json(
        base_url,
        "/server/database/item",
        method="POST",
        payload={"namespace": "lane_data", "key": key, "value": value},
    )


def delete_lane(base_url: str, key: str) -> None:
    path = (
        f"/server/database/item?namespace={quote('lane_data')}"
        f"&key={quote(key)}"
    )
    request_json(base_url, path, method="DELETE")


def sync_once(base_url: str, requested_tool_count: int = 0) -> int:
    variables = get_saved_variables(base_url)
    objects = get_printer_objects(base_url)
    tool_count = requested_tool_count or detect_tool_count(variables, objects)
    save_tool_count(base_url, variables, tool_count)
    desired = build_lanes(variables, tool_count)
    current = get_lane_data(base_url)
    changed = 0
    for key, lane in desired.items():
        if current.get(key) != lane:
            write_lane(base_url, key, lane)
            changed += 1
            LOG.info(
                "Updated %s: material=%s color=%s nozzle=%s bed=%s",
                key,
                lane.get("material") or "EMPTY",
                lane.get("color"),
                lane.get("nozzle_temp", "unset"),
                lane.get("bed_temp", "unset"),
            )
    for key in current:
        match = LANE_KEY.fullmatch(str(key))
        if match is not None and int(match.group(1)) >= tool_count:
            delete_lane(base_url, str(key))
            changed += 1
            LOG.info("Removed stale %s", key)
    return changed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("MOONRAKER_URL", "http://127.0.0.1:7125"),
        help="Moonraker base URL (default: http://127.0.0.1:7125)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("FILAMENT_SYNC_INTERVAL", "3")),
        help="Reconciliation interval in seconds (default: 3)",
    )
    parser.add_argument(
        "--tool-count",
        type=int,
        default=int(os.environ.get("FILAMENT_SYNC_TOOL_COUNT", "0")),
        help="Override automatic tool detection (default: auto)",
    )
    parser.add_argument("--once", action="store_true", help="Synchronize once and exit")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.interval < 1:
        LOG.error("The synchronization interval must be at least one second")
        return 2
    if args.tool_count < 0 or args.tool_count > MAX_TOOL_COUNT:
        LOG.error(
            "The tool count override must be 0 (auto) or between 1 and %d",
            MAX_TOOL_COUNT,
        )
        return 2

    stopping = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while not stopping:
        try:
            changed = sync_once(args.url, args.tool_count)
            if args.once:
                LOG.info("Synchronization complete; %d lane(s) changed", changed)
                return 0
        except MoonrakerError as exc:
            if args.once:
                LOG.error("%s", exc)
                return 1
            LOG.warning("%s; retrying", exc)
        deadline = time.monotonic() + args.interval
        while not stopping and time.monotonic() < deadline:
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
