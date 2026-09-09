import logging
import re

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from ks_includes.screen_panel import ScreenPanel


MATERIALS = ["PLA", "PETG", "ASA", "ABS", "TPU", "PCTG", "PA", "PA-CF", "PC", "HIPS", "PVA"]
EMPTY_MATERIALS = {"", "EMPTY", "NONE", "UNKNOWN", "UNSET"}
LANE_KEY = re.compile(r"^tool(\d+)$", re.IGNORECASE)
MAX_TOOL_COUNT = 32
DEFAULT_TEMPS = {
    "PLA": (220, 60),
    "PETG": (245, 80),
    "ASA": (260, 105),
    "ABS": (250, 105),
    "TPU": (225, 50),
    "PCTG": (250, 80),
    "PA": (280, 80),
    "PA-CF": (285, 80),
    "PC": (290, 110),
    "HIPS": (245, 100),
    "PVA": (205, 60),
}


class ColorDot(Gtk.DrawingArea):
    def __init__(self, size=24):
        super().__init__()
        self.set_size_request(size, size)
        self.rgba = Gdk.RGBA()
        self.rgba.parse("#000000")
        self.connect("draw", self._draw)

    def set_color(self, color):
        self.rgba.parse(f"#{str(color).lstrip('#')}")
        self.queue_draw()

    def _draw(self, widget, cr):
        allocation = widget.get_allocation()
        radius = min(allocation.width, allocation.height) / 2 - 1
        center_x = allocation.width / 2
        center_y = allocation.height / 2
        cr.set_source_rgb(1, 1, 1)
        cr.arc(center_x, center_y, radius, 0, 2 * 3.141592653589793)
        cr.fill()
        cr.set_source_rgba(self.rgba.red, self.rgba.green, self.rgba.blue, 1)
        cr.arc(center_x, center_y, max(0, radius - 2), 0, 2 * 3.141592653589793)
        cr.fill()
        return False


class Panel(ScreenPanel):
    def __init__(self, screen, title):
        super().__init__(screen, title or _("Tool Filaments"))
        display = Gdk.Screen.get_default()
        self.is_portrait = bool(display and display.get_height() > display.get_width())
        self.selected_tool = 0
        self.tool_count = 1
        self.lanes = {"tool0": self._empty_lane(0)}
        self.refresh_timer = None
        self.query_in_flight = False
        self.loading_controls = False
        self.controls_dirty = False
        self.pending_saves = {}
        self.selected_material = "PLA"
        self.material_popover = None
        self.color_keyboard_box = None
        self.color_keyboard_entry = None
        self.color_commit_in_progress = False
        self.selected_color = Gdk.RGBA()
        self.selected_color.parse("#000000")
        self.nozzle_temp = 220
        self.bed_temp = 60

        self.local_css = Gtk.CssProvider()
        self.local_css.load_from_data(
            b"""
            #maeve-filament-color {
                background-color: transparent;
                background-image: none;
                border: 0;
                border-bottom: 0.18em solid @color2;
            }
            #maeve-filament-material {
                border: 0;
                border-bottom: 0.18em solid @color1;
            }
            #maeve-color-dialog,
            #maeve-color-dialog box,
            #maeve-color-dialog grid,
            #maeve-color-dialog stack,
            #maeve-color-dialog revealer {
                background-color: #111111;
                color: #FFFFFF;
            }
            #maeve-color-dialog label {
                color: #FFFFFF;
            }
            #maeve-color-dialog entry {
                background-color: #242424;
                color: #FFFFFF;
            }
            #maeve-color-dialog colorswatch#add-color-button,
            #maeve-color-dialog #add-color-button {
                background-color: #242424;
                background-image: none;
                color: #FFFFFF;
                border: 2px solid #6C71C4;
            }
            #maeve-color-dialog colorswatch#add-color-button overlay {
                background-color: #242424;
                background-image: none;
                color: #FFFFFF;
                text-shadow: none;
                -gtk-icon-shadow: none;
            }
            #maeve-color-dialog colorchooser {
                margin: 10px;
            }
            #maeve-color-dialog colorswatch {
                min-width: 46px;
                min-height: 46px;
            }
            #maeve-color-dialog colorswatch.light overlay,
            #maeve-color-dialog colorswatch.light overlay image {
                color: #000000;
                text-shadow: none;
                -gtk-icon-shadow: none;
            }
            #maeve-color-dialog colorswatch.dark overlay,
            #maeve-color-dialog colorswatch.dark overlay image {
                color: #FFFFFF;
                text-shadow: none;
                -gtk-icon-shadow: none;
            }
            #maeve-color-dialog button.maeve-color-action {
                background-color: transparent;
                background-image: none;
            }
            #maeve-color-dialog.maeve-manual-color button.maeve-color-action,
            #maeve-color-dialog.maeve-manual-color button.maeve-color-action:hover,
            #maeve-color-dialog.maeve-manual-color button.maeve-color-action:focus,
            #maeve-color-dialog.maeve-manual-color button.maeve-color-action:checked {
                background-color: transparent;
                background-image: none;
                box-shadow: none;
                text-shadow: none;
                -gtk-icon-shadow: none;
            }
            #maeve-color-dialog button.maeve-color-action box,
            #maeve-color-dialog button.maeve-color-action label,
            #maeve-color-dialog button.maeve-color-action image {
                background-color: transparent;
                background-image: none;
            }
            #maeve-color-dialog button.maeve-color-action.color1:active,
            #maeve-color-dialog button.maeve-color-action.color1.active {
                border-bottom: 0.4em solid @color1;
            }
            #maeve-color-dialog button.maeve-color-action.color2:active,
            #maeve-color-dialog button.maeve-color-action.color2.active {
                border-bottom: 0.4em solid @color2;
            }
            #maeve-color-dialog button.maeve-color-action:active,
            #maeve-color-dialog button.maeve-color-action.active {
                background-color: @active;
                background-image: none;
            }
            #maeve-color-dialog.maeve-manual-color button.maeve-color-action:active,
            #maeve-color-dialog.maeve-manual-color button.maeve-color-action.active {
                background-color: @active;
                background-image: none;
            }
            #maeve-color-dialog button.maeve-color-action:active label,
            #maeve-color-dialog button.maeve-color-action.active label,
            #maeve-color-dialog button.maeve-color-action:active image,
            #maeve-color-dialog button.maeve-color-action.active image {
                color: #FFFFFF;
            }
            #maeve-color-dialog.maeve-manual-color colorchooser {
                margin: 0;
            }
            .maeve-temperature-value {
                background-color: #111111;
                color: #FFFFFF;
                border: 2px solid;
                border-radius: 8px;
                padding: 7px;
            }
            .maeve-temperature-nozzle {
                border-color: @color1;
            }
            .maeve-temperature-bed {
                border-color: @color2;
            }
            button.maeve-temperature-step {
                border: 1px solid #657B83;
                border-radius: 6px;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            self.local_css,
            Gtk.STYLE_PROVIDER_PRIORITY_USER,
        )

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=5 if self.is_portrait else 8,
            vexpand=True,
        )

        self.tool_grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True)
        self.tool_buttons = []
        self.tool_labels = []
        self.tool_color_dots = []
        self._rebuild_tool_buttons()
        root.pack_start(self.tool_grid, False, False, 0)

        editor = Gtk.Grid(
            column_spacing=6 if self.is_portrait else 12,
            row_spacing=6 if self.is_portrait else 10,
            hexpand=True,
        )
        editor.attach(Gtk.Label(label=_("Material"), halign=Gtk.Align.END), 0, 0, 1, 1)
        self.material = Gtk.Button(label=self.selected_material, hexpand=True, vexpand=True)
        self.material.set_name("maeve-filament-material")
        self.material.set_size_request(-1, 48 if self.is_portrait else 56)
        self.material.set_margin_bottom(4)
        self.material.connect("clicked", self.show_material_picker)
        editor.attach(self.material, 1, 0, 2, 1)

        editor.attach(Gtk.Label(label=_("Color"), halign=Gtk.Align.END), 0, 1, 1, 1)
        self.color = Gtk.Button(hexpand=True, vexpand=True)
        color_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        color_content.set_halign(Gtk.Align.CENTER)
        self.color_dot = ColorDot()
        self.color_label = Gtk.Label(use_markup=True)
        color_content.pack_start(self.color_dot, False, False, 0)
        color_content.pack_start(self.color_label, False, False, 0)
        self.color.add(color_content)
        self.color.set_name("maeve-filament-color")
        self.color.set_size_request(-1, 48 if self.is_portrait else 56)
        self.color.set_margin_top(4)
        self.color.set_margin_bottom(4)
        self.color.connect("clicked", self.show_color_picker)
        editor.attach(self.color, 1, 1, 2, 1)
        self.update_color_button()

        editor.attach(Gtk.Label(label=_("Nozzle"), halign=Gtk.Align.END), 0, 2, 1, 1)
        self.nozzle_value = Gtk.Label(label="220 °C", width_chars=7)
        self.nozzle_value.get_style_context().add_class("maeve-temperature-value")
        self.nozzle_value.get_style_context().add_class("maeve-temperature-nozzle")
        editor.attach(self.nozzle_value, 1, 2, 1, 1)
        nozzle_steps = self._temperature_buttons("nozzle")
        editor.attach(nozzle_steps, 2, 2, 1, 1)

        editor.attach(Gtk.Label(label=_("Bed"), halign=Gtk.Align.END), 0, 3, 1, 1)
        self.bed_value = Gtk.Label(label="60 °C", width_chars=7)
        self.bed_value.get_style_context().add_class("maeve-temperature-value")
        self.bed_value.get_style_context().add_class("maeve-temperature-bed")
        editor.attach(self.bed_value, 1, 3, 1, 1)
        bed_steps = self._temperature_buttons("bed")
        editor.attach(bed_steps, 2, 3, 1, 1)
        root.pack_start(editor, False, False, 0)

        actions = Gtk.Grid(column_homogeneous=True)
        save = self._gtk.Button("complete", _("Save"), "color3")
        empty = self._gtk.Button("cancel", _("Empty"), "color2")
        refresh = self._gtk.Button("refresh", _("Refresh"), "color1")
        save.connect("clicked", self.save_tool)
        empty.connect("clicked", self.empty_tool)
        refresh.connect("clicked", self.refresh)
        actions.attach(empty, 0, 0, 1, 1)
        actions.attach(refresh, 1, 0, 1, 1)
        actions.attach(save, 2, 0, 1, 1)
        root.pack_end(actions, False, False, 0)

        self.content.add(root)

        self._update_tool_buttons()
        self._load_selected_tool()

    def _rebuild_tool_buttons(self):
        for child in self.tool_grid.get_children():
            self.tool_grid.remove(child)
        self.tool_buttons = []
        self.tool_labels = []
        self.tool_color_dots = []
        columns = min(self.tool_count, 4)
        for tool in range(self.tool_count):
            button = Gtk.Button(hexpand=True, vexpand=True)
            button_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            button_content.set_halign(
                Gtk.Align.START if self.is_portrait and self.tool_count == 1 else Gtk.Align.CENTER
            )
            button_content.set_valign(Gtk.Align.CENTER)
            if self.is_portrait and self.tool_count == 1:
                button_content.set_margin_start(12)
            color_dot = ColorDot()
            label = Gtk.Label(use_markup=True, justify=Gtk.Justification.CENTER)
            button_content.pack_start(color_dot, False, False, 0)
            button_content.pack_start(label, False, False, 0)
            button.add(button_content)
            button.connect("clicked", self.select_tool, tool)
            self.tool_grid.attach(button, tool % columns, tool // columns, 1, 1)
            self.tool_buttons.append(button)
            self.tool_labels.append(label)
            self.tool_color_dots.append(color_dot)
        rows = (self.tool_count + columns - 1) // columns
        row_height = 58 if self.is_portrait else 70
        self.tool_grid.set_size_request(-1, row_height * rows)
        self.tool_grid.show_all()

    @staticmethod
    def _empty_lane(tool):
        return {"lane": str(tool), "material": "", "color": "#000000"}

    @staticmethod
    def _is_empty_material(value):
        return str(value or "").strip().upper() in EMPTY_MATERIALS

    def _temperature_buttons(self, target):
        grid = Gtk.Grid(column_homogeneous=True, column_spacing=3, hexpand=True)
        for column, delta in enumerate((-10, -5, -1, 1, 5, 10)):
            button = Gtk.Button(label=f"{delta:+d}", hexpand=True)
            button.set_size_request(-1, 36 if self.is_portrait else 42)
            button.get_style_context().add_class("maeve-temperature-step")
            button.connect("clicked", self.adjust_temperature, target, delta)
            columns = 3 if self.is_portrait else 6
            grid.attach(button, column % columns, column // columns, 1, 1)
        return grid

    def adjust_temperature(self, _widget, target, delta):
        self.controls_dirty = True
        if target == "nozzle":
            self.nozzle_temp = max(0, min(350, self.nozzle_temp + delta))
            self.nozzle_value.set_text(f"{self.nozzle_temp} °C")
        else:
            self.bed_temp = max(0, min(150, self.bed_temp + delta))
            self.bed_value.set_text(f"{self.bed_temp} °C")
        self._update_tool_buttons()

    def show_material_picker(self, _widget):
        if self.material_popover is not None:
            self.material_popover.popdown()

        popover = Gtk.Popover(relative_to=self.material)
        popover.set_modal(True)
        popover.set_position(Gtk.PositionType.BOTTOM)
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True, column_spacing=4, row_spacing=4)
        for index, material in enumerate(MATERIALS):
            button = Gtk.Button(label=material, hexpand=True, vexpand=True)
            if material == self.selected_material:
                button.get_style_context().add_class("horizontal_togglebuttons_active")
            button.connect("clicked", self.select_material, material, popover)
            grid.attach(button, index % 3, index // 3, 1, 1)
        popover.add(grid)
        popover.connect("closed", self.material_picker_closed)
        self.material_popover = popover
        popover.show_all()
        popover.popup()

    def select_material(self, _widget, material, popover):
        self.controls_dirty = True
        self.selected_material = material
        self.material.set_label(material)
        if not self.loading_controls and material in DEFAULT_TEMPS:
            nozzle, bed = DEFAULT_TEMPS[material]
            self.nozzle_temp = nozzle
            self.bed_temp = bed
            self.nozzle_value.set_text(f"{nozzle} °C")
            self.bed_value.set_text(f"{bed} °C")
        self._update_tool_buttons()
        popover.popdown()

    def material_picker_closed(self, popover):
        if self.material_popover is popover:
            self.material_popover = None

    def show_color_picker(self, _widget):
        screen = Gdk.Screen.get_default()
        width = int(screen.get_width() * 0.92) if screen is not None else 736
        height = int(screen.get_height() * 0.84) if screen is not None else 403
        dialog = Gtk.Dialog(transient_for=self._screen, modal=True)
        dialog.set_name("maeve-color-dialog")
        dialog.set_decorated(False)
        dialog.set_position(Gtk.WindowPosition.CENTER_ALWAYS)
        dialog.set_default_size(width, height)
        dialog.resize(width, height)
        dialog.set_resizable(False)

        chooser = Gtk.ColorChooserWidget(hexpand=True, vexpand=True)
        chooser.set_use_alpha(False)
        chooser.set_rgba(self.selected_color)
        self.expand_color_palette(chooser)

        actions = Gtk.Grid(column_homogeneous=True, hexpand=True)
        cancel = self._gtk.Button("back", _("Cancel"), "color1")
        select = self._gtk.Button("complete", _("Select"), "color2")
        cancel.get_style_context().add_class("maeve-color-action")
        select.get_style_context().add_class("maeve-color-action")
        cancel.set_size_request(-1, 70)
        select.set_size_request(-1, 70)
        cancel.connect("clicked", self.close_color_picker, dialog)
        select.connect("clicked", self.accept_color_picker, dialog, chooser)
        actions.attach(cancel, 0, 0, 1, 1)
        actions.attach(select, 1, 0, 1, 1)
        content = dialog.get_content_area()
        content.set_spacing(0)
        overlay = Gtk.Overlay(hexpand=True, vexpand=True)
        picker_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        picker_box.pack_start(chooser, True, True, 0)
        picker_box.pack_end(actions, False, False, 0)
        overlay.add(picker_box)

        keyboard_host = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            hexpand=True,
            valign=Gtk.Align.END,
        )
        keyboard_host.set_no_show_all(True)
        keyboard_host.hide()
        overlay.add_overlay(keyboard_host)
        content.pack_start(overlay, True, True, 0)

        chooser.connect(
            "notify::show-editor",
            self.color_picker_mode_changed,
            dialog,
            actions,
            keyboard_host,
        )
        dialog.show_all()
        keyboard_host.hide()
        dialog.present()

    def color_picker_mode_changed(self, chooser, _pspec, dialog, actions, keyboard_host):
        manual = bool(chooser.get_property("show-editor"))
        context = dialog.get_style_context()
        if manual:
            context.add_class("maeve-manual-color")
            actions.set_size_request(-1, 52)
            GLib.idle_add(
                self.configure_manual_color_editor,
                chooser,
                dialog,
                keyboard_host,
            )
        else:
            self.close_manual_keyboard()
            actions.set_size_request(-1, 70)
            context.remove_class("maeve-manual-color")

    def configure_manual_color_editor(self, chooser, dialog, keyboard_host):
        """Connect the manual hex entry without changing the picker geometry."""
        for widget in self.walk_widgets(chooser):
            type_name = widget.__gtype__.name
            if type_name == "GtkEntry" and not getattr(
                widget, "_maeve_hex_keyboard_connected", False
            ):
                widget._maeve_hex_keyboard_connected = True
                widget.set_input_purpose(Gtk.InputPurpose.FREE_FORM)
                widget.set_max_length(7)
                widget.connect(
                    "button-release-event",
                    self.show_manual_hex_keyboard,
                    dialog,
                    keyboard_host,
                )
                widget.connect("changed", self.preserve_hex_prefix)
                widget.connect("focus-out-event", self.manual_hex_focus_out)
        return GLib.SOURCE_REMOVE

    def show_manual_hex_keyboard(self, entry, event, dialog, keyboard_host):
        if event.type != Gdk.EventType.BUTTON_RELEASE:
            return False
        if self.color_keyboard_box is keyboard_host and self._screen.keyboard is not None:
            return False
        self.close_manual_keyboard()
        self.color_keyboard_box = keyboard_host
        self.color_keyboard_entry = entry
        keyboard_host.set_no_show_all(False)
        keyboard_host.show()
        self._screen.show_keyboard(
            entry,
            event,
            box=keyboard_host,
            close_cb=self.close_manual_keyboard,
        )
        GLib.idle_add(self.fit_manual_keyboard, dialog)
        return False

    @staticmethod
    def preserve_hex_prefix(entry):
        if getattr(entry, "_maeve_normalizing_hex", False):
            return
        text = entry.get_text()
        if text.startswith("#"):
            return
        position = entry.get_position()
        entry._maeve_normalizing_hex = True
        try:
            entry.set_text("#" + text.replace("#", ""))
            entry.set_position(max(1, position + 1))
        finally:
            entry._maeve_normalizing_hex = False

    def fit_manual_keyboard(self, dialog):
        if self._screen.keyboard is not None:
            keyboard = self._screen.keyboard.get("box")
            if keyboard is not None:
                screen = Gdk.Screen.get_default()
                height = int(screen.get_height() * 0.50) if screen is not None else 240
                keyboard.set_size_request(dialog.get_allocated_width(), height)
        return GLib.SOURCE_REMOVE

    def manual_hex_focus_out(self, entry, _event):
        if not self.color_commit_in_progress:
            GLib.idle_add(self.close_manual_keyboard, entry)
        return False

    def close_manual_keyboard(self, *_args, **_kwargs):
        entry = _kwargs.get("entry") or self.color_keyboard_entry
        if entry is None and _args and isinstance(_args[0], Gtk.Entry):
            entry = _args[0]
        if entry is not None and not self.color_commit_in_progress:
            self.color_commit_in_progress = True
            try:
                # GtkColorEditor only parses its text on activate/focus-out.
                # The keyboard's down arrow does not move focus, so explicitly
                # activate the hex entry before removing the keyboard.
                entry.emit("activate")
            finally:
                self.color_commit_in_progress = False
        box = self.color_keyboard_box
        if box is not None and self._screen.keyboard is not None:
            self._screen.remove_keyboard(box=box)
        if box is not None:
            box.set_no_show_all(True)
            box.hide()
        self.color_keyboard_box = None
        self.color_keyboard_entry = None
        return GLib.SOURCE_REMOVE

    @classmethod
    def walk_widgets(cls, widget):
        yield widget
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                yield from cls.walk_widgets(child)

    @staticmethod
    def expand_color_palette(chooser):
        for child in chooser.get_children():
            if not isinstance(child, Gtk.Box):
                continue
            for palette_child in child.get_children():
                if isinstance(palette_child, Gtk.Grid):
                    palette_child.set_column_homogeneous(True)
                    palette_child.set_row_homogeneous(True)
                    palette_child.set_hexpand(True)
                    palette_child.set_vexpand(True)
                    palette_child.set_halign(Gtk.Align.FILL)
                    palette_child.set_valign(Gtk.Align.FILL)

    def close_color_picker(self, _widget, dialog):
        self.close_manual_keyboard()
        dialog.destroy()

    def accept_color_picker(self, _widget, dialog, chooser):
        self.close_manual_keyboard()
        self.controls_dirty = True
        self.selected_color = chooser.get_rgba()
        self.update_color_button()
        self._update_tool_buttons()
        dialog.destroy()

    def update_color_button(self):
        color = "{:02X}{:02X}{:02X}".format(
            round(self.selected_color.red * 255),
            round(self.selected_color.green * 255),
            round(self.selected_color.blue * 255),
        )
        self.color_dot.set_color(color)
        self.color_label.set_markup(f"<span>#{color}</span>")

    @staticmethod
    def _response_value(response):
        if not isinstance(response, dict):
            return None
        payload = response.get("result", response)
        if not isinstance(payload, dict):
            return None
        value = payload.get("value")
        return value if isinstance(value, dict) else None

    @staticmethod
    def _normalize_color(value):
        color = str(value or "000000").strip().upper()
        if color.startswith("#"):
            color = color[1:]
        if color.startswith("0X"):
            color = color[2:]
        if len(color) == 8:
            color = color[:6]
        if len(color) != 6 or any(character not in "0123456789ABCDEF" for character in color):
            return "000000"
        return color

    def activate(self):
        self.refresh()
        if self.refresh_timer is None:
            self.refresh_timer = GLib.timeout_add_seconds(5, self.periodic_sync)

    def deactivate(self):
        if self.refresh_timer is not None:
            GLib.source_remove(self.refresh_timer)
            self.refresh_timer = None

    def periodic_sync(self):
        if self.controls_dirty:
            self.save_tool(None)
        else:
            self.refresh()
        return True

    def refresh(self, widget=None):
        if widget is not None and self.controls_dirty:
            self.save_tool(None)
            return True
        if self.query_in_flight:
            return True
        self.query_in_flight = True
        try:
            self._screen._ws.send_method(
                "server.database.get_item",
                {"namespace": "lane_data"},
                self._database_response,
            )
        except Exception as exc:
            self.query_in_flight = False
            logging.exception("Unable to request lane_data: %s", exc)
            self._screen.show_popup_message(_("Unable to request lane_data from Moonraker"), level=2)
        return True

    def _database_response(self, response, *args):
        GLib.idle_add(self._apply_database_response, response)

    def _apply_database_response(self, response):
        self.query_in_flight = False
        value = self._response_value(response)
        if value is None:
            logging.warning("Moonraker returned no lane_data")
            return False
        tool_indices = sorted(
            int(match.group(1))
            for key in value
            if (match := LANE_KEY.fullmatch(str(key))) is not None
            and int(match.group(1)) < MAX_TOOL_COUNT
        )
        detected_count = max(tool_indices, default=0) + 1
        if detected_count != self.tool_count:
            self.tool_count = detected_count
            self.selected_tool = min(self.selected_tool, self.tool_count - 1)
            self.pending_saves = {
                tool: pending
                for tool, pending in self.pending_saves.items()
                if tool < self.tool_count
            }
            self._rebuild_tool_buttons()

        self.lanes = {}
        for tool in range(self.tool_count):
            lane = value.get(f"tool{tool}")
            if isinstance(lane, dict):
                self.lanes[f"tool{tool}"] = lane
            else:
                self.lanes[f"tool{tool}"] = self._empty_lane(tool)

        for tool, expected in list(self.pending_saves.items()):
            lane = self.lanes.get(f"tool{tool}", self._empty_lane(tool))
            if self._lane_matches(lane, expected):
                del self.pending_saves[tool]

        self._update_tool_buttons()
        if not self.controls_dirty and self.selected_tool not in self.pending_saves:
            self._load_selected_tool()
        return False

    def select_tool(self, _widget, tool):
        if self.controls_dirty:
            self.save_tool(None)
        self.selected_tool = tool
        self.controls_dirty = False
        self._update_tool_buttons()
        self._load_selected_tool()

    def _lane_matches(self, lane, expected):
        if expected.get("empty"):
            return (
                self._is_empty_material(lane.get("material"))
                and self._normalize_color(lane.get("color")) == "000000"
            )
        if str(lane.get("material") or "").upper() != expected["material"]:
            return False
        if self._normalize_color(lane.get("color")) != expected["color"]:
            return False
        for key in ("nozzle_temp", "bed_temp"):
            try:
                if int(float(lane.get(key))) != expected[key]:
                    return False
            except (TypeError, ValueError):
                return False
        return True

    def _update_tool_buttons(self):
        for tool in range(self.tool_count):
            lane = self.lanes.get(f"tool{tool}", self._empty_lane(tool))
            if tool == self.selected_tool and self.controls_dirty:
                material = self.selected_material
                color = "{:02X}{:02X}{:02X}".format(
                    round(self.selected_color.red * 255),
                    round(self.selected_color.green * 255),
                    round(self.selected_color.blue * 255),
                )
                changed = " *"
            elif tool in self.pending_saves:
                material = self.pending_saves[tool]["material"] or "EMPTY"
                color = self.pending_saves[tool]["color"]
                changed = ""
            else:
                material = str(lane.get("material") or "EMPTY").upper()
                color = self._normalize_color(lane.get("color"))
                changed = ""
            self.tool_color_dots[tool].set_color(color)
            markup = f"<b>T{tool}</b>\n{GLib.markup_escape_text(material)}{changed}"
            self.tool_labels[tool].set_markup(markup)
            context = self.tool_buttons[tool].get_style_context()
            if tool == self.selected_tool:
                context.add_class("horizontal_togglebuttons_active")
            else:
                context.remove_class("horizontal_togglebuttons_active")

    def _load_selected_tool(self):
        lane = self.lanes.get(f"tool{self.selected_tool}", self._empty_lane(self.selected_tool))
        material = str(lane.get("material") or "").strip().upper()
        is_empty = self._is_empty_material(material)
        self.loading_controls = True
        if not is_empty and material not in MATERIALS:
            material = "PLA"
        self.selected_material = "EMPTY" if is_empty else material
        self.material.set_label(_("EMPTY") if is_empty else material)
        self.selected_color.parse(f"#{self._normalize_color(lane.get('color'))}")
        self.update_color_button()
        defaults = (0, 0) if is_empty else DEFAULT_TEMPS.get(material, (0, 0))
        self.nozzle_temp = int(float(lane.get("nozzle_temp", defaults[0]) or defaults[0]))
        self.bed_temp = int(float(lane.get("bed_temp", defaults[1]) or defaults[1]))
        self.nozzle_value.set_text(f"{self.nozzle_temp} °C")
        self.bed_value.set_text(f"{self.bed_temp} °C")
        self.loading_controls = False
        self.controls_dirty = False

    def save_tool(self, widget):
        tool = self.selected_tool
        material = self.selected_material
        if self._is_empty_material(material):
            self.empty_tool(widget)
            return
        color = "{:02X}{:02X}{:02X}".format(
            round(self.selected_color.red * 255),
            round(self.selected_color.green * 255),
            round(self.selected_color.blue * 255),
        )
        script = (
            f"SET_TOOL_FILAMENT TOOL={tool} MATERIAL={material} "
            f"COLOR={color} NOZZLE_TEMP={self.nozzle_temp} "
            f"BED_TEMP={self.bed_temp} QUIET=1"
        )
        self.pending_saves[tool] = {
            "material": material,
            "color": color,
            "nozzle_temp": self.nozzle_temp,
            "bed_temp": self.bed_temp,
        }
        self.controls_dirty = False
        self._update_tool_buttons()
        self._screen._send_action(widget, "printer.gcode.script", {"script": script})
        GLib.timeout_add_seconds(2, self._refresh_once)

    def empty_tool(self, widget):
        tool = self.selected_tool
        script = f"SET_TOOL_FILAMENT TOOL={tool} EMPTY=1 QUIET=1"
        self.pending_saves[tool] = {
            "material": "",
            "color": "000000",
            "nozzle_temp": 0,
            "bed_temp": 0,
            "empty": True,
        }
        self.selected_material = "EMPTY"
        self.material.set_label(_("EMPTY"))
        self.selected_color.parse("#000000")
        self.update_color_button()
        self.nozzle_temp = 0
        self.bed_temp = 0
        self.nozzle_value.set_text("0 °C")
        self.bed_value.set_text("0 °C")
        self.controls_dirty = False
        self._update_tool_buttons()
        self._screen._send_action(widget, "printer.gcode.script", {"script": script})
        GLib.timeout_add_seconds(2, self._refresh_once)

    def _refresh_once(self):
        self.refresh()
        return False
