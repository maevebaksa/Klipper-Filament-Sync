#!/usr/bin/env python3
"""Idempotently connect Orbiter load/unload to saved physical-tool temperatures."""

import argparse
import os
import re
import tempfile
from pathlib import Path


MARKER = "FILAMENT_SYNC_PROFILE_TEMP_BEGIN"
END_MARKER = "FILAMENT_SYNC_PROFILE_TEMP_END"
VERSION_MARKER = "FILAMENT_SYNC_PROFILE_TEMP_V2"

OLD_LOAD = """    {% if desired_target >= sensor.filament_load_min_temp|float %}
      {% set load_target = desired_target %}
    {% else %}
      {% set load_target = sensor.filament_load_temp|float %}
    {% endif %}
"""

NEW_LOAD = """    # FILAMENT_SYNC_PROFILE_TEMP_BEGIN
    {% set saved_filament = printer.save_variables.variables %}
    {% set profile_temps = saved_filament.filament_nozzle_temps|default([]) %}
    {% set profile_materials = saved_filament.filament_temp_materials|default([]) %}
    {% set profile_types = saved_filament.filament_types|default([]) %}
    {% set profile_valid = profile_temps|length > tn and profile_materials|length > tn and profile_types|length > tn and profile_materials[tn]|string|upper == profile_types[tn]|string|upper %}
    {% set profile_temp = profile_temps[tn]|float if profile_valid else 0 %}
    # Preserve an active print target; otherwise use the physical tool profile.
    {% if desired_target >= sensor.filament_load_min_temp|float %}
      {% set load_target = desired_target %}
    {% elif profile_temp >= sensor.filament_load_min_temp|float %}
      {% set load_target = profile_temp %}
    {% else %}
      {% set load_target = sensor.filament_load_temp|float %}
    {% endif %}
    # FILAMENT_SYNC_PROFILE_TEMP_END
"""

OLD_UNLOAD = """    {% if desired_target >= sensor.filament_unload_temp|float %}
      {% set unload_target = desired_target %}
    {% else %}
      {% set unload_target = sensor.filament_unload_temp|float %}
    {% endif %}
"""

NEW_UNLOAD = """    # FILAMENT_SYNC_PROFILE_TEMP_BEGIN
    {% set saved_filament = printer.save_variables.variables %}
    {% set profile_temps = saved_filament.filament_nozzle_temps|default([]) %}
    {% set profile_materials = saved_filament.filament_temp_materials|default([]) %}
    {% set profile_types = saved_filament.filament_types|default([]) %}
    {% set profile_valid = profile_temps|length > tn and profile_materials|length > tn and profile_types|length > tn and profile_materials[tn]|string|upper == profile_types[tn]|string|upper %}
    {% set profile_temp = profile_temps[tn]|float if profile_valid else 0 %}
    # Preserve an active print target; otherwise use the physical tool profile.
    {% if desired_target >= sensor.filament_unload_temp|float %}
      {% set unload_target = desired_target %}
    {% elif profile_temp >= sensor.filament_load_min_temp|float %}
      {% set unload_target = profile_temp %}
    {% else %}
      {% set unload_target = sensor.filament_unload_temp|float %}
    {% endif %}
    # FILAMENT_SYNC_PROFILE_TEMP_END
"""

SENSOR_DEFINITION = re.compile(
    r"^(?P<indent>[ \t]*){%\s*set\s+sensor\s*=\s*printer\['gcode_macro _SENSOR_VARIABLES'\]\s*%}[ \t]*$",
    re.MULTILINE,
)


def stock_profile_block(indent: str, target: str, fallback: str) -> str:
    return f"""{indent}# FILAMENT_SYNC_PROFILE_TEMP_BEGIN
{indent}# FILAMENT_SYNC_PROFILE_TEMP_V2
{indent}{{% set saved_filament = printer.save_variables.variables %}}
{indent}{{% set profile_temps = saved_filament.filament_nozzle_temps|default([]) %}}
{indent}{{% set profile_materials = saved_filament.filament_temp_materials|default([]) %}}
{indent}{{% set profile_types = saved_filament.filament_types|default([]) %}}
{indent}{{% set profile_valid = profile_temps|length > 0 and profile_materials|length > 0 and profile_types|length > 0 and profile_materials[0]|string|upper == profile_types[0]|string|upper %}}
{indent}{{% set profile_temp = profile_temps[0]|float if profile_valid else 0 %}}
{indent}{{% set user_temp = printer.extruder.target|float %}}
{indent}{{% if user_temp >= sensor.filament_load_min_temp|float %}}
{indent}  {{% set {target} = user_temp %}}
{indent}{{% elif profile_temp >= sensor.filament_load_min_temp|float %}}
{indent}  {{% set {target} = profile_temp %}}
{indent}{{% else %}}
{indent}  {{% set {target} = {fallback} %}}
{indent}{{% endif %}}
{indent}# FILAMENT_SYNC_PROFILE_TEMP_END"""


def normalize_old_stock_patch(source: str) -> str:
    block_pattern = re.compile(
        rf"^[ \t]*# {MARKER}[^\n]*\n.*?^[ \t]*# {END_MARKER}[^\n]*(?:\n)?",
        re.MULTILINE | re.DOTALL,
    )
    normalized, count = block_pattern.subn("", source)
    if count != 2:
        raise SystemExit("The older stock Orbiter integration could not be upgraded safely")
    return normalized.replace("filament_sync_load_target", "sensor.filament_load_temp").replace(
        "filament_sync_unload_target", "sensor.filament_unload_temp"
    )


def patch_stock_section(source: str, macro: str, target: str, fallback: str) -> str:
    section_pattern = re.compile(
        rf"^\[gcode_macro\s+{re.escape(macro)}\][^\n]*\n.*?(?=^\[|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = section_pattern.search(source)
    if match is None:
        raise ValueError(f"stock Orbiter macro {macro} was not found")
    section = match.group(0)
    if fallback not in section:
        raise ValueError(f"stock Orbiter macro {macro} has an unsupported temperature structure")
    sensor_match = SENSOR_DEFINITION.search(section)
    if sensor_match is None:
        raise ValueError(f"stock Orbiter macro {macro} has no sensor definition")

    updated = section.replace(fallback, target)
    heat_condition = re.compile(
        r"^(?P<indent>[ \t]*){%\s*if\s+.*printer\.extruder\.can_extrude.*$",
        re.MULTILINE,
    )
    updated, condition_count = heat_condition.subn(
        r"\g<indent>{% if true %} # Always command and wait for the selected filament temperature",
        updated,
        count=1,
    )
    if condition_count != 1:
        raise ValueError(f"stock Orbiter macro {macro} has no supported heater condition")
    if macro.lower() == "filament_unload":
        heater_off = re.compile(r"^(?P<indent>[ \t]*)M104\s+S0\s+T0.*$", re.IGNORECASE | re.MULTILINE)
        updated, restore_count = heater_off.subn(
            r"\g<indent>SET_HEATER_TEMPERATURE HEATER=extruder TARGET={user_temp} # restore pre-unload target",
            updated,
            count=1,
        )
        if restore_count != 1:
            raise ValueError("stock Orbiter unload macro has no supported heater restore command")
    sensor_match = SENSOR_DEFINITION.search(updated)
    assert sensor_match is not None
    block = stock_profile_block(sensor_match.group("indent"), target, fallback)
    updated = updated[: sensor_match.end()] + "\n" + block + updated[sensor_match.end() :]
    return source[: match.start()] + updated + source[match.end() :]


def patch_so3(source: str) -> str:
    """Patch the two temperature paths, preserving native callbacks and motion.

    SO3 Tn activates a motor; SELECT_PH(n+1) moves the carriage. Autoload
    deliberately keeps Tn, allowing the manufacturer's docked-head loading.
    """
    ids = re.findall(r"^\[gcode_macro _SENSOR_VARIABLES_(\d+)\]", source, re.M)
    if len(ids) != 1:
        raise ValueError("expected one SO3 sensor configuration per file")
    tool = int(ids[0])
    extruder = "extruder" + (str(tool) if tool else "")
    for operation in ("load", "unload"):
        pattern = re.compile(
            rf"^\[gcode_macro _filament_{operation}_{tool}\][^\n]*\n.*?(?=^\[|\Z)",
            re.M | re.S,
        )
        match = pattern.search(source)
        if match is None:
            raise ValueError(f"missing SO3 {operation} macro")
        section = match.group()
        sensor = f"{{% set sensor = printer['gcode_macro _SENSOR_VARIABLES_{tool}'] %}}"
        fallback = f"sensor.filament_{operation}_temp"
        target = f"filament_sync_{operation}_target"
        if section.count(sensor) != 1:
            raise ValueError("unsupported SO3 sensor reference")
        for command in (f"SET_HEATER_TEMPERATURE HEATER={extruder} TARGET={{{fallback}}}",
                        f"TEMPERATURE_WAIT SENSOR={extruder} MINIMUM={{{fallback}}}"):
            if section.count(command) != 1:
                raise ValueError("unsupported SO3 heater command")
        section = section.replace(fallback, target)
        condition = re.compile(rf"^[ \t]*{{% if .*printer\.{extruder}\.can_extrude.*$", re.M)
        section, count = condition.subn("      {% if true %} # Wait for the selected profile temperature", section)
        if count != 1:
            raise ValueError("unsupported SO3 heater condition")
        block = stock_profile_block("    ", target, fallback)
        block = block.replace(" > 0", f" > {tool}").replace("[0]", f"[{tool}]")
        block = block.replace("printer.extruder.target", f"printer.{extruder}.target")
        block += f"""
    {{% if {target} > printer.configfile.settings.{extruder}.max_temp|float %}}
      {{ action_raise_error('Filament temperature exceeds {extruder} max_temp') }}
    {{% endif %}}"""
        section = section.replace(sensor, sensor + "\n" + block)
        if operation == "unload":
            off = f"SET_HEATER_TEMPERATURE HEATER={extruder} TARGET=0"
            if section.count(off) != 1:
                raise ValueError("unsupported SO3 heater restore")
            section = section.replace(off, f"SET_HEATER_TEMPERATURE HEATER={extruder} TARGET={{user_temp}}")
        source = source[:match.start()] + section + source[match.end():]
    return source


def patch_source(source: str) -> str:
    if re.search(r"^\[gcode_macro _SENSOR_VARIABLES_\d+\]", source, re.M):
        try:
            return patch_so3(source)
        except ValueError as exc:
            raise SystemExit(f"Unsupported Smart Orbiter 3 configuration; no changes made: {exc}") from exc
    if source.count(OLD_LOAD) == 1 and source.count(OLD_UNLOAD) == 1:
        return source.replace(OLD_LOAD, NEW_LOAD).replace(OLD_UNLOAD, NEW_UNLOAD)
    try:
        updated = patch_stock_section(
            source,
            "filament_load",
            "filament_sync_load_target",
            "sensor.filament_load_temp",
        )
        return patch_stock_section(
            updated,
            "filament_unload",
            "filament_sync_unload_target",
            "sensor.filament_unload_temp",
        )
    except ValueError as exc:
        raise SystemExit(
            "Orbiter macros differ from the supported stock OrbiterSensor.cfg "
            f"and Maeve/KTC sensor files; no changes were made: {exc}"
        ) from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    source = args.path.read_text(encoding="utf-8")
    if source.count(VERSION_MARKER) == 2 and source.count(MARKER) == 2 and source.count(END_MARKER) == 2:
        return
    if source.count(MARKER) == 2 and source.count(END_MARKER) == 2:
        if "filament_sync_load_target" not in source or "filament_sync_unload_target" not in source:
            return
        source = normalize_old_stock_patch(source)
    if MARKER in source or END_MARKER in source:
        raise SystemExit("Orbiter temperature integration is only partially present")
    updated = patch_source(source)
    if args.check:
        return

    fd, temporary = tempfile.mkstemp(prefix=args.path.name + ".", dir=args.path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, args.path.stat().st_mode)
        os.replace(temporary, args.path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    main()
