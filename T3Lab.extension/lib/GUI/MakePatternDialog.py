# -*- coding: utf-8 -*-
"""
MakePatternDialog
=================
Interactive Vector Hatch Studio dialog for pyRevit & T3Lab.
Provides an interactive WPF drawing canvas, unit module configuration,
and live large-surface tiling preview.
"""

import os
import re
import math
from math import sqrt, pi, sin, cos, degrees

# T3Lab Base Window & Styles
from GUI.WPF_Base import T3WPFWindow

# Core Pattern Engine
from Pattern.patmaker import (
    PatternCompiler,
    make_pattern,
    export_pattern_to_file,
    HAS_REVIT
)

# CLR / WPF Primitives
try:
    import clr
    clr.AddReference("PresentationCore")
    clr.AddReference("PresentationFramework")
    clr.AddReference("WindowsBase")
    from System.Windows import Point, Size, Thickness
    from System.Windows.Shapes import Line as WpfLine, Rectangle as WpfRect
    from System.Windows.Media import SolidColorBrush, Color, DoubleCollection
    from System.Windows.Controls import Canvas
    from Microsoft.Win32 import SaveFileDialog
except Exception:
    pass

# Optional Revit references
if HAS_REVIT:
    from Autodesk.Revit import DB
    from pyrevit import revit


XAML_FILE = os.path.join(os.path.dirname(__file__), "Tools", "MakePattern.xaml")


def parse_float(val, default_val=0.0):
    try:
        return float(re.sub(r'[^\d.-]', '', str(val)))
    except Exception:
        return default_val


class MakePatternDialog(T3WPFWindow):
    def __init__(self):
        T3WPFWindow.__init__(self, XAML_FILE)

        # ── State ────────────────────────────────────────────────────────────
        self.lines = []           # List of [((x1, y1), (x2, y2)), ...] in mm relative to (0,0)
        self.undo_stack = []      # History stack of line lists
        self.redo_stack = []

        # Current drawing interaction
        self.is_drawing = False
        self.start_pt_mm = None
        self.temp_line_shape = None

        # Module properties (mm)
        self.mod_w = 600.0
        self.mod_h = 300.0
        self.shift_ratio = 0.0

        # Snapping & drawing flags
        self.snap_enabled = True
        self.snap_step = 50.0
        self.ortho_enabled = False
        self.ghost_enabled = True
        self.active_tool = "line"
        self.preview_repeat = 5

        # Brushes
        self._brush_grid = SolidColorBrush(Color.FromArgb(50, 160, 160, 170))
        self._brush_axis = SolidColorBrush(Color.FromArgb(120, 100, 100, 110))
        self._brush_bound = SolidColorBrush(Color.FromArgb(200, 24, 24, 27))
        self._brush_line = SolidColorBrush(Color.FromArgb(255, 24, 24, 27))
        self._brush_temp = SolidColorBrush(Color.FromArgb(200, 37, 99, 235))
        self._brush_ghost = SolidColorBrush(Color.FromArgb(45, 24, 24, 27))
        self._brush_prev_tile = SolidColorBrush(Color.FromArgb(40, 180, 180, 190))
        self._brush_prev_line = SolidColorBrush(Color.FromArgb(230, 24, 24, 27))

        # Setup and wire controls
        self._wire_events()
        self._sync_inputs_from_state()

        # Initial render when loaded
        self.canvas_editor.SizeChanged += self._on_canvas_size_changed
        self.canvas_preview.SizeChanged += self._on_preview_size_changed

    def _wire_events(self):
        # Module inputs
        self.txt_mod_w.LostFocus += self._on_dim_text_changed
        self.txt_mod_h.LostFocus += self._on_dim_text_changed
        self.cmb_presets.SelectionChanged += self._on_preset_changed
        self.cmb_shift_mode.SelectionChanged += self._on_shift_mode_changed

        # Snapping & options
        self.chk_snap.Checked += self._on_snap_toggled
        self.chk_snap.Unchecked += self._on_snap_toggled
        self.cmb_grid_step.SelectionChanged += self._on_grid_step_changed
        self.chk_ortho.Checked += self._on_ortho_toggled
        self.chk_ortho.Unchecked += self._on_ortho_toggled
        self.chk_ghost.Checked += self._on_ghost_toggled
        self.chk_ghost.Unchecked += self._on_ghost_toggled

        # Tools
        self.rb_tool_line.Checked += lambda s, e: self._set_tool("line")
        self.rb_tool_rect.Checked += lambda s, e: self._set_tool("rect")
        self.rb_tool_cross.Checked += lambda s, e: self._set_tool("cross")

        # Edit actions
        self.btn_undo.Click += self._on_undo_click
        self.btn_redo.Click += self._on_redo_click
        self.btn_clear.Click += self._on_clear_click
        self.btn_import_revit.Click += self._on_import_revit_click

        # Canvas mouse interactions
        self.canvas_editor.MouseDown += self._on_canvas_mouse_down
        self.canvas_editor.MouseMove += self._on_canvas_mouse_move
        self.canvas_editor.MouseUp += self._on_canvas_mouse_up
        self.canvas_editor.MouseLeave += self._on_canvas_mouse_leave

        # Preview controls
        self.cmb_preview_repeat.SelectionChanged += self._on_preview_repeat_changed

        # Export & Create
        self.btn_export_pat.Click += self._on_export_pat_click
        self.btn_create_pattern.Click += self._on_create_pattern_click

    def _sync_inputs_from_state(self):
        self.txt_mod_w.Text = str(int(self.mod_w)) if self.mod_w.is_integer() else "{:.1f}".format(self.mod_w)
        self.txt_mod_h.Text = str(int(self.mod_h)) if self.mod_h.is_integer() else "{:.1f}".format(self.mod_h)

    def _set_tool(self, tool_name):
        self.active_tool = tool_name

    # ── EVENT HANDLERS: INPUTS ────────────────────────────────────────────────

    def _on_dim_text_changed(self, sender, e):
        nw = max(10.0, parse_float(self.txt_mod_w.Text, self.mod_w))
        nh = max(10.0, parse_float(self.txt_mod_h.Text, self.mod_h))
        if nw != self.mod_w or nh != self.mod_h:
            self.mod_w = nw
            self.mod_h = nh
            self._redraw_all()

    def _on_preset_changed(self, sender, e):
        sel = self.cmb_presets.SelectedIndex
        # Presets:
        # 0: Custom Size
        # 1: 600 x 300 mm (Running Bond)
        # 2: 600 x 600 mm (Square Tile)
        # 3: 300 x 300 mm (Square Tile)
        # 4: 200 x 400 mm (Subway Tile)
        # 5: 150 x 600 mm (Plank / Wood)
        # 6: 1200 x 600 mm (Large Slab)
        # 7: 100 x 100 mm (Mosaic)
        presets = {
            1: (600.0, 300.0, 1),
            2: (600.0, 600.0, 0),
            3: (300.0, 300.0, 0),
            4: (200.0, 400.0, 1),
            5: (150.0, 600.0, 1),
            6: (1200.0, 600.0, 0),
            7: (100.0, 100.0, 0),
        }
        if sel in presets:
            pw, ph, shift_idx = presets[sel]
            self.mod_w = pw
            self.mod_h = ph
            self.cmb_shift_mode.SelectedIndex = shift_idx
            self._sync_inputs_from_state()
            self._redraw_all()

    def _on_shift_mode_changed(self, sender, e):
        sel = self.cmb_shift_mode.SelectedIndex
        # 0: Stack Bond (0)
        # 1: Running Bond 1/2 (0.5)
        # 2: Running Bond 1/3 (0.3333)
        # 3: Running Bond 1/4 (0.25)
        # 4: Vertical Stagger (0.5 vertical)
        ratios = [0.0, 0.5, 0.333333, 0.25, 0.5]
        self.shift_ratio = ratios[sel] if sel < len(ratios) else 0.0
        self._redraw_all()

    def _on_snap_toggled(self, sender, e):
        self.snap_enabled = bool(self.chk_snap.IsChecked)

    def _on_grid_step_changed(self, sender, e):
        steps = [10.0, 25.0, 50.0, 100.0]
        sel = self.cmb_grid_step.SelectedIndex
        if 0 <= sel < len(steps):
            self.snap_step = steps[sel]
            self._redraw_editor()

    def _on_ortho_toggled(self, sender, e):
        self.ortho_enabled = bool(self.chk_ortho.IsChecked)

    def _on_ghost_toggled(self, sender, e):
        self.ghost_enabled = bool(self.chk_ghost.IsChecked)
        self._redraw_editor()

    def _on_preview_repeat_changed(self, sender, e):
        repeats = [3, 5, 8, 12]
        sel = self.cmb_preview_repeat.SelectedIndex
        if 0 <= sel < len(repeats):
            self.preview_repeat = repeats[sel]
            self._redraw_preview()

    def _on_canvas_size_changed(self, sender, e):
        self._redraw_editor()

    def _on_preview_size_changed(self, sender, e):
        self._redraw_preview()

    # ── DRAWING CANVAS INTERACTIONS ───────────────────────────────────────────

    def _snap_point(self, pt_mm):
        x, y = pt_mm
        if self.snap_enabled and self.snap_step > 0:
            x = round(x / self.snap_step) * self.snap_step
            y = round(y / self.snap_step) * self.snap_step
        # Clamp to module bounds
        x = max(0.0, min(self.mod_w, x))
        y = max(0.0, min(self.mod_h, y))
        return (x, y)

    def _apply_ortho(self, start_mm, cur_mm):
        dx = cur_mm[0] - start_mm[0]
        dy = cur_mm[1] - start_mm[1]
        adx, ady = abs(dx), abs(dy)
        if adx > 2.0 * ady:
            return (cur_mm[0], start_mm[1])
        elif ady > 2.0 * adx:
            return (start_mm[0], cur_mm[1])
        else:
            # 45-degree snap
            sign_y = 1.0 if dy >= 0 else -1.0
            return (cur_mm[0], start_mm[1] + adx * sign_y)

    def _screen_to_module(self, px, py):
        """Converts canvas screen pixels to module coordinates (mm)."""
        cw = self.canvas_editor.ActualWidth
        ch = self.canvas_editor.ActualHeight
        if cw < 20 or ch < 20:
            return (0.0, 0.0)

        pad = 28.0
        avail_w = max(10.0, cw - 2 * pad)
        avail_h = max(10.0, ch - 2 * pad)
        scale = min(avail_w / self.mod_w, avail_h / self.mod_h)

        ox = (cw - self.mod_w * scale) / 2.0
        oy = ch - (ch - self.mod_h * scale) / 2.0

        mx = (px - ox) / scale
        my = (oy - py) / scale
        return (mx, my)

    def _module_to_screen(self, mx, my):
        """Converts module coordinates (mm) to canvas screen pixels."""
        cw = self.canvas_editor.ActualWidth
        ch = self.canvas_editor.ActualHeight
        pad = 28.0
        avail_w = max(10.0, cw - 2 * pad)
        avail_h = max(10.0, ch - 2 * pad)
        scale = min(avail_w / self.mod_w, avail_h / self.mod_h)

        ox = (cw - self.mod_w * scale) / 2.0
        oy = ch - (ch - self.mod_h * scale) / 2.0

        px = ox + mx * scale
        py = oy - my * scale
        return (px, py)

    def _on_canvas_mouse_down(self, sender, e):
        pos = e.GetPosition(self.canvas_editor)
        raw_mm = self._screen_to_module(pos.X, pos.Y)
        snap_mm = self._snap_point(raw_mm)

        self.is_drawing = True
        self.start_pt_mm = snap_mm

    def _on_canvas_mouse_move(self, sender, e):
        pos = e.GetPosition(self.canvas_editor)
        raw_mm = self._screen_to_module(pos.X, pos.Y)
        snap_mm = self._snap_point(raw_mm)

        # Check Shift key for temporary Ortho
        from System.Windows.Input import Keyboard, Key
        shift_pressed = Keyboard.IsKeyDown(Key.LeftShift) or Keyboard.IsKeyDown(Key.RightShift)
        is_ortho = self.ortho_enabled or shift_pressed

        if self.is_drawing and self.start_pt_mm:
            cur_mm = snap_mm
            if is_ortho:
                cur_mm = self._apply_ortho(self.start_pt_mm, cur_mm)

            # Update preview line
            p1_px = self._module_to_screen(self.start_pt_mm[0], self.start_pt_mm[1])
            p2_px = self._module_to_screen(cur_mm[0], cur_mm[1])

            if self.temp_line_shape is None:
                self.temp_line_shape = WpfLine()
                self.temp_line_shape.Stroke = self._brush_temp
                self.temp_line_shape.StrokeThickness = 2.0
                dashes = DoubleCollection()
                dashes.Add(4.0)
                dashes.Add(2.0)
                self.temp_line_shape.StrokeDashArray = dashes
                self.canvas_editor.Children.Add(self.temp_line_shape)

            self.temp_line_shape.X1 = p1_px[0]
            self.temp_line_shape.Y1 = p1_px[1]
            self.temp_line_shape.X2 = p2_px[0]
            self.temp_line_shape.Y2 = p2_px[1]

            length = sqrt((cur_mm[0] - self.start_pt_mm[0])**2 + (cur_mm[1] - self.start_pt_mm[1])**2)
            self.txt_cursor_info.Text = "X:{:.0f} Y:{:.0f} mm | L:{:.0f} mm | {} lines".format(
                cur_mm[0], cur_mm[1], length, len(self.lines)
            )
        else:
            self.txt_cursor_info.Text = "X:{:.0f} Y:{:.0f} mm | {} lines".format(
                snap_mm[0], snap_mm[1], len(self.lines)
            )

    def _on_canvas_mouse_up(self, sender, e):
        if not self.is_drawing or not self.start_pt_mm:
            return

        pos = e.GetPosition(self.canvas_editor)
        raw_mm = self._screen_to_module(pos.X, pos.Y)
        snap_mm = self._snap_point(raw_mm)

        from System.Windows.Input import Keyboard, Key
        shift_pressed = Keyboard.IsKeyDown(Key.LeftShift) or Keyboard.IsKeyDown(Key.RightShift)
        if self.ortho_enabled or shift_pressed:
            snap_mm = self._apply_ortho(self.start_pt_mm, snap_mm)

        # Commit shape based on active tool
        p1 = self.start_pt_mm
        p2 = snap_mm
        length = sqrt((p2[0] - p1[0])**2 + (p2[1] - p1[1])**2)

        if length > 2.0:
            self.undo_stack.append(list(self.lines))
            self.redo_stack = []

            if self.active_tool == "line":
                self.lines.append((p1, p2))
            elif self.active_tool == "rect":
                # Rectangle: 4 lines
                min_x, max_x = min(p1[0], p2[0]), max(p1[0], p2[0])
                min_y, max_y = min(p1[1], p2[1]), max(p1[1], p2[1])
                self.lines.append(((min_x, min_y), (max_x, min_y)))
                self.lines.append(((max_x, min_y), (max_x, max_y)))
                self.lines.append(((max_x, max_y), (min_x, max_y)))
                self.lines.append(((min_x, max_y), (min_x, min_y)))
            elif self.active_tool == "cross":
                # Diagonal cross inside bounding box
                min_x, max_x = min(p1[0], p2[0]), max(p1[0], p2[0])
                min_y, max_y = min(p1[1], p2[1]), max(p1[1], p2[1])
                self.lines.append(((min_x, min_y), (max_x, max_y)))
                self.lines.append(((min_x, max_y), (max_x, min_y)))

        # Reset temp state
        self.is_drawing = False
        self.start_pt_mm = None
        if self.temp_line_shape:
            self.canvas_editor.Children.Remove(self.temp_line_shape)
            self.temp_line_shape = None

        self._redraw_all()

    def _on_canvas_mouse_leave(self, sender, e):
        if self.is_drawing:
            self.is_drawing = False
            self.start_pt_mm = None
            if self.temp_line_shape:
                self.canvas_editor.Children.Remove(self.temp_line_shape)
                self.temp_line_shape = None
            self._redraw_editor()

    # ── ACTIONS: UNDO / REDO / CLEAR / IMPORT ─────────────────────────────────

    def _on_undo_click(self, sender, e):
        if self.undo_stack:
            self.redo_stack.append(list(self.lines))
            self.lines = self.undo_stack.pop()
            self._redraw_all()

    def _on_redo_click(self, sender, e):
        if self.redo_stack:
            self.undo_stack.append(list(self.lines))
            self.lines = self.redo_stack.pop()
            self._redraw_all()

    def _on_clear_click(self, sender, e):
        if self.lines:
            self.undo_stack.append(list(self.lines))
            self.redo_stack = []
            self.lines = []
            self._redraw_all()

    def _on_import_revit_click(self, sender, e):
        if not HAS_REVIT or not revit.doc:
            self._set_status("Revit document not available.", is_error=True)
            return

        sel_ids = revit.get_selection().element_ids
        if not sel_ids:
            self._set_status("No linework selected in active Revit view. Please select lines first.", is_error=True)
            return

        imported_lines = []
        doc = revit.doc
        u_min, u_max = float('inf'), float('-inf')
        v_min, v_max = float('inf'), float('-inf')

        for eid in sel_ids:
            elem = doc.GetElement(eid)
            if not elem:
                continue

            curve = None
            if hasattr(elem, "GeometryCurve"):
                curve = elem.GeometryCurve
            elif hasattr(elem, "Curve"):
                curve = elem.Curve

            if curve:
                tess = curve.Tessellate()
                for i in range(len(tess) - 1):
                    p1 = tess[i]
                    p2 = tess[i + 1]
                    # Update bounds (in feet)
                    u_min = min(u_min, p1.X, p2.X)
                    u_max = max(u_max, p1.X, p2.X)
                    v_min = min(v_min, p1.Y, p2.Y)
                    v_max = max(v_max, p1.Y, p2.Y)
                    imported_lines.append((p1, p2))

        if not imported_lines:
            self._set_status("No valid curves found in current selection.", is_error=True)
            return

        # Convert feet to mm: 1 ft = 304.8 mm
        ft_to_mm = 304.8
        mod_w = max(50.0, (u_max - u_min) * ft_to_mm)
        mod_h = max(50.0, (v_max - v_min) * ft_to_mm)

        self.mod_w = round(mod_w, 1)
        self.mod_h = round(mod_h, 1)
        self._sync_inputs_from_state()

        self.undo_stack.append(list(self.lines))
        self.redo_stack = []
        self.lines = []

        for p1, p2 in imported_lines:
            x1 = (p1.X - u_min) * ft_to_mm
            y1 = (p1.Y - v_min) * ft_to_mm
            x2 = (p2.X - u_min) * ft_to_mm
            y2 = (p2.Y - v_min) * ft_to_mm
            self.lines.append(((x1, y1), (x2, y2)))

        self._set_status("Imported {} lines from Revit selection.".format(len(self.lines)))
        self._redraw_all()

    # ── RENDERING: EDITOR CANVAS ──────────────────────────────────────────────

    def _redraw_all(self):
        self._redraw_editor()
        self._redraw_preview()

    def _redraw_editor(self):
        cw = self.canvas_editor.ActualWidth
        ch = self.canvas_editor.ActualHeight
        if cw < 20 or ch < 20:
            return

        self.canvas_editor.Children.Clear()

        # 1. Draw Grid Lines
        if self.snap_enabled and self.snap_step > 0:
            step = self.snap_step
            # Vertical grid lines
            gx = step
            while gx < self.mod_w:
                p1 = self._module_to_screen(gx, 0)
                p2 = self._module_to_screen(gx, self.mod_h)
                l = WpfLine()
                l.X1, l.Y1, l.X2, l.Y2 = p1[0], p1[1], p2[0], p2[1]
                l.Stroke = self._brush_grid
                l.StrokeThickness = 1.0
                self.canvas_editor.Children.Add(l)
                gx += step

            # Horizontal grid lines
            gy = step
            while gy < self.mod_h:
                p1 = self._module_to_screen(0, gy)
                p2 = self._module_to_screen(self.mod_w, gy)
                l = WpfLine()
                l.X1, l.Y1, l.X2, l.Y2 = p1[0], p1[1], p2[0], p2[1]
                l.Stroke = self._brush_grid
                l.StrokeThickness = 1.0
                self.canvas_editor.Children.Add(l)
                gy += step

        # 2. Draw Ghost Adjacent Tiles (Seamless visual guide)
        if self.ghost_enabled and self.lines:
            offsets = [
                (-1, 0), (1, 0), (0, -1), (0, 1),
                (-1, 1), (1, 1), (-1, -1), (1, -1)
            ]
            for (ox_tile, oy_tile) in offsets:
                # Apply shift if vertical tile
                shift_x = 0.0
                if oy_tile != 0:
                    shift_x = self.mod_w * self.shift_ratio * oy_tile

                tile_dx = ox_tile * self.mod_w + shift_x
                tile_dy = oy_tile * self.mod_h

                for (p1, p2) in self.lines:
                    gp1 = self._module_to_screen(p1[0] + tile_dx, p1[1] + tile_dy)
                    gp2 = self._module_to_screen(p2[0] + tile_dx, p2[1] + tile_dy)
                    gl = WpfLine()
                    gl.X1, gl.Y1, gl.X2, gl.Y2 = gp1[0], gp1[1], gp2[0], gp2[1]
                    gl.Stroke = self._brush_ghost
                    gl.StrokeThickness = 1.2
                    self.canvas_editor.Children.Add(gl)

        # 3. Draw Module Boundary Box
        b_tl = self._module_to_screen(0, self.mod_h)
        b_br = self._module_to_screen(self.mod_w, 0)
        bw = abs(b_br[0] - b_tl[0])
        bh = abs(b_br[1] - b_tl[1])

        rect = WpfRect()
        rect.Width = bw
        rect.Height = bh
        rect.Stroke = self._brush_bound
        rect.StrokeThickness = 2.0
        dashes = DoubleCollection()
        dashes.Add(6.0)
        dashes.Add(3.0)
        rect.StrokeDashArray = dashes
        Canvas.SetLeft(rect, b_tl[0])
        Canvas.SetTop(rect, b_tl[1])
        self.canvas_editor.Children.Add(rect)

        # 4. Draw Main Pattern Lines
        for (p1, p2) in self.lines:
            s1 = self._module_to_screen(p1[0], p1[1])
            s2 = self._module_to_screen(p2[0], p2[1])
            l = WpfLine()
            l.X1, l.Y1, l.X2, l.Y2 = s1[0], s1[1], s2[0], s2[1]
            l.Stroke = self._brush_line
            l.StrokeThickness = 2.0
            self.canvas_editor.Children.Add(l)

        # Update counter
        self.txt_cursor_info.Text = "Module: {:.0f} × {:.0f} mm | {} lines".format(
            self.mod_w, self.mod_h, len(self.lines)
        )

    # ── RENDERING: LARGE SURFACE PREVIEW ──────────────────────────────────────

    def _redraw_preview(self):
        pw = self.canvas_preview.ActualWidth
        ph = self.canvas_preview.ActualHeight
        if pw < 20 or ph < 20:
            return

        self.canvas_preview.Children.Clear()

        repeats = self.preview_repeat
        total_w = repeats * self.mod_w
        total_h = repeats * self.mod_h

        pad = 16.0
        avail_w = max(10.0, pw - 2 * pad)
        avail_h = max(10.0, ph - 2 * pad)
        scale = min(avail_w / total_w, avail_h / total_h)

        ox = (pw - total_w * scale) / 2.0
        oy = ph - (ph - total_h * scale) / 2.0

        # Draw tile boundaries & lines across repeats x repeats grid
        for row in range(repeats):
            for col in range(repeats):
                # Calculate shift for this row
                row_shift_x = (row * self.mod_w * self.shift_ratio) % self.mod_w
                tile_orig_x = col * self.mod_w + row_shift_x
                tile_orig_y = row * self.mod_h

                # Module boundary rect in preview
                px_l = ox + tile_orig_x * scale
                px_t = oy - (tile_orig_y + self.mod_h) * scale
                rect = WpfRect()
                rect.Width = self.mod_w * scale
                rect.Height = self.mod_h * scale
                rect.Stroke = self._brush_prev_tile
                rect.StrokeThickness = 0.8
                Canvas.SetLeft(rect, px_l)
                Canvas.SetTop(rect, px_t)
                self.canvas_preview.Children.Add(rect)

                # Lines inside tile
                for (p1, p2) in self.lines:
                    x1 = ox + (tile_orig_x + p1[0]) * scale
                    y1 = oy - (tile_orig_y + p1[1]) * scale
                    x2 = ox + (tile_orig_x + p2[0]) * scale
                    y2 = oy - (tile_orig_y + p2[1]) * scale

                    pl = WpfLine()
                    pl.X1, pl.Y1, pl.X2, pl.Y2 = x1, y1, x2, y2
                    pl.Stroke = self._brush_prev_line
                    pl.StrokeThickness = 1.0
                    self.canvas_preview.Children.Add(pl)

        # Update stats
        cov_w = repeats * self.mod_w
        cov_h = repeats * self.mod_h
        self.txt_preview_stats.Text = "Coverage: {:.0f} × {:.0f} mm ({}×{} tiles)".format(
            cov_w, cov_h, repeats, repeats
        )

        # Compile check
        compiler = PatternCompiler(
            self.txt_pattern_name.Text or "Test",
            self.mod_w, self.mod_h,
            is_model=bool(self.rb_type_model.IsChecked),
            shift_ratio=self.shift_ratio
        )
        grids = compiler.compile_lines(self.lines)
        self.txt_grid_stats.Text = "Fill Grids: {} | Shift: {:.0f}%".format(
            len(grids), self.shift_ratio * 100.0
        )

    # ── EXPORT & CREATE ACTIONS ───────────────────────────────────────────────

    def _set_status(self, text, is_error=False):
        self.status_text.Text = text
        if is_error:
            self.status_dot.Fill = self.FindResource("T3.Danger.Accent")
        else:
            self.status_dot.Fill = self.FindResource("T3.Success.Accent")

    def _on_export_pat_click(self, sender, e):
        if not self.lines:
            self._set_status("Please draw or import lines before exporting.", is_error=True)
            return

        name = self.txt_pattern_name.Text.strip() or "T3_Pattern"
        is_model = bool(self.rb_type_model.IsChecked)

        sfd = SaveFileDialog()
        sfd.Filter = "AutoCAD Pattern (*.pat)|*.pat"
        sfd.FileName = "{}.pat".format(name)
        sfd.Title = "Export Pattern to .PAT File"

        if sfd.ShowDialog():
            try:
                export_pattern_to_file(
                    sfd.FileName,
                    name,
                    self.lines,
                    self.mod_w,
                    self.mod_h,
                    is_model=is_model,
                    shift_ratio=self.shift_ratio,
                    unit='MM'
                )
                self._set_status("Exported pattern successfully to {}".format(os.path.basename(sfd.FileName)))
            except Exception as ex:
                self._set_status("Export failed: {}".format(str(ex)), is_error=True)

    def _on_create_pattern_click(self, sender, e):
        if not self.lines:
            self._set_status("No lines defined. Draw module lines on canvas or import from Revit.", is_error=True)
            return

        if not HAS_REVIT or not revit.doc:
            self._set_status("Revit document not available.", is_error=True)
            return

        name = self.txt_pattern_name.Text.strip() or "T3_Custom_Pattern"
        is_model = bool(self.rb_type_model.IsChecked)
        create_fr = bool(self.chk_create_filled_region.IsChecked)

        try:
            fpe = make_pattern(
                name=name,
                lines=self.lines,
                width=self.mod_w,
                height=self.mod_h,
                is_model=is_model,
                shift_ratio=self.shift_ratio,
                doc=revit.doc,
                create_filled_region=create_fr
            )
            msg = "Pattern '{}' created successfully in Revit!".format(name)
            if create_fr:
                msg += " (Filled Region type added)"
            self._set_status(msg)
        except Exception as ex:
            self._set_status("Failed to create pattern: {}".format(str(ex)), is_error=True)


def show_dialog():
    dialog = MakePatternDialog()
    return dialog.ShowDialog()
