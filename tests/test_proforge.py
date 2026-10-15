"""Render real Makertech SO3 templates without sending commands to hardware."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import jinja2

from filament_sync_bridge import detect_tool_count
from orbiter_temperature_patch import patch_source, OLD_LOAD, OLD_UNLOAD, NEW_LOAD, NEW_UNLOAD

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / 'fixtures/proforge5'
ENV = jinja2.Environment(block_start_string='{%', block_end_string='%}',
                         variable_start_string='{', variable_end_string='}', undefined=jinja2.StrictUndefined)


def render(source, tool, operation, profile=245, target=0, material='PLA', tag='PLA'):
    section = re.search(rf'^\[gcode_macro _filament_{operation}_{tool}\][^\n]*\n(.*?)(?=^\[|\Z)', source, re.M | re.S)[1]
    template = section.split('gcode:', 1)[1]
    extruder = 'extruder' + (str(tool) if tool else '')
    variables = dict(filament_types=['PLA'] * tool + [material],
                     filament_temp_materials=['PLA'] * tool + [tag],
                     filament_nozzle_temps=[210] * tool + [profile])
    printer = {'save_variables': {'variables': variables},
               'configfile': {'settings': {extruder: {'max_temp': 300}}},
               extruder: {'target': target, 'can_extrude': target >= 165},
               f'gcode_macro _SENSOR_VARIABLES_{tool}': {
                   'filament_load_temp': 220, 'filament_unload_temp': 185,
                   'filament_load_min_temp': 190, 'enable_beep': False,
                   'nozzle_purge_length': 100, 'nozzle_purge_speed': 300}}
    def fail(message):
        raise ValueError(message)
    return ENV.from_string(template).render(printer=printer, action_raise_error=fail,
                                           **{f'{operation}busy_{tool}': 0})


class ProforgeTests(unittest.TestCase):
    def test_native_autoload_and_button_guards(self):
        for tool in range(5):
            source = patch_source((FIXTURES / f'so3_{tool}.cfg').read_text())
            for operation in ('load', 'unload'):
                template = re.search(rf'^\[gcode_macro _filament_{operation}_init_{tool}\][^\n]*\n(.*?)(?=^\[|\Z)', source, re.M | re.S)[1].split('gcode:', 1)[1]
                for state in ('printing', 'paused', 'standby'):
                    for disabled in (False, True):
                        printer = {'print_stats': {'state': state}, f'gcode_macro _SENSOR_VARIABLES_{tool}': {
                            'disable_autoload': disabled, 'disable_autounload': disabled}}
                        result = ENV.from_string(template).render(printer=printer)
                        invoked = bool(re.search(rf'^\s*_filament_{operation}_{tool}\s*$', result, re.M))
                        self.assertEqual(invoked, state != 'printing' and not disabled)

    def test_stock_orbiter_two_path_remains_supported(self):
        source = """[gcode_macro filament_load]
gcode:
    {% set sensor = printer['gcode_macro _SENSOR_VARIABLES'] %}
    {% if printer.extruder.can_extrude %}
      M109 S{sensor.filament_load_temp}
    {% endif %}
[gcode_macro filament_unload]
gcode:
    {% set sensor = printer['gcode_macro _SENSOR_VARIABLES'] %}
    {% if printer.extruder.can_extrude %}
      M109 S{sensor.filament_unload_temp}
    {% endif %}
    M104 S0 T0
"""
        updated = patch_source(source)
        self.assertEqual(updated.count('FILAMENT_SYNC_PROFILE_TEMP_V2'), 2)
        self.assertIn('M109 S{filament_sync_load_target}', updated)
        self.assertIn('M109 S{filament_sync_unload_target}', updated)

    def test_real_five_head_temperature_matrix(self):
        for tool in range(5):
            source = (FIXTURES / f'so3_{tool}.cfg').read_text()
            updated = patch_source(source)
            extruder = 'extruder' + (str(tool) if tool else '')
            for operation in ('load', 'unload'):
                for profile, target, tag, expected in (
                    (245, 0, 'PLA', 245), (245, 260, 'PLA', 260),
                    (245, 200, 'PLA', 200), (245, 0, 'ABS', 220 if operation == 'load' else 185),
                    (0, 0, 'PLA', 220 if operation == 'load' else 185),
                ):
                    with self.subTest(tool=tool, operation=operation, target=target, tag=tag, profile=profile):
                        result = render(updated, tool, operation, profile=profile, target=target, tag=tag)
                        self.assertIn(f'HEATER={extruder} TARGET={expected}', result)
                        self.assertIn(f'SENSOR={extruder} MINIMUM={expected}', result)
                        self.assertRegex(result, rf'(?m)^\s*T{tool}\s*$')
                        self.assertNotIn('SELECT_PH', result)
                with self.assertRaises(ValueError):
                    render(updated, tool, operation, profile=350)
            # Callback/runout/docking logic outside the two edited macros is unchanged.
            def strip_macros(text):
                return re.sub(rf'^\[gcode_macro _filament_(?:load|unload)_{tool}\][^\n]*\n.*?(?=^\[|\Z)', '', text, flags=re.M | re.S)
            self.assertEqual(strip_macros(source), strip_macros(updated))

    def test_cli_repeat_and_check_do_not_modify(self):
        for tool in range(5):
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / f'so3_{tool}.cfg'
                path.write_bytes((FIXTURES / path.name).read_bytes())
                original = path.read_bytes()
                command = [sys.executable, str(ROOT / 'orbiter_temperature_patch.py')]
                subprocess.run(command + ['--check', str(path)], check=True)
                self.assertEqual(original, path.read_bytes())
                subprocess.run(command + [str(path)], check=True)
                once = path.read_bytes()
                subprocess.run(command + [str(path)], check=True)
                self.assertEqual(once, path.read_bytes())

    def test_unknown_so3_rejected(self):
        source = (FIXTURES / 'so3_0.cfg').read_text().replace('TEMPERATURE_WAIT SENSOR=extruder', 'M109')
        with self.assertRaises(SystemExit):
            patch_source(source)

    def test_legacy_ktc_still_supported(self):
        self.assertEqual(patch_source(OLD_LOAD + OLD_UNLOAD), NEW_LOAD + NEW_UNLOAD)

    def test_tool_count_preserves_existing_printers(self):
        objects = ['gcode_macro DOCK'] + [f'gcode_macro SELECT_PH{i+1}' for i in range(5)]
        objects += [f'gcode_macro T{i}' for i in range(5)]
        for count in range(1, 6):
            self.assertEqual(detect_tool_count({'active_tools': count}, objects), count)
        self.assertEqual(detect_tool_count({'active_tools': 2}, objects + ['tool T0', 'tool T1', 'tool T2']), 3)
        self.assertEqual(detect_tool_count({}, ['extruder']), 1)
        self.assertEqual(detect_tool_count({}, ['extruder', 'extruder1']), 2)
        self.assertEqual(detect_tool_count({}, ['gcode_macro T0', 'gcode_macro T1']), 2)
        self.assertEqual(detect_tool_count({'active_tools': 'bad'}, objects), 5)
