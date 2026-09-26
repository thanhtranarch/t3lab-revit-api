# -*- coding: utf-8 -*-
"""Background Theme dialog — theme studio for the Revit canvas.

UI-only module: every Revit API call is delegated to the ``callbacks``
dict supplied by the launcher script (keeps Revit API logic out of WPF
code, per project rules).

Callbacks contract (missing keys degrade gracefully):
    apply_background(r, g, b)                       -> None
    save_config(cfg_dict)                           -> None
    get_3d_context()                                -> {"view_name": str|None, "count_3d": int}
    apply_view_gradient(sky, horizon, ground, all_) -> (ok, message)
    clear_view_background(all_)                     -> (ok, message)
    get_theme_info()  -> {"supported", "current", "options",
                          "canvas_supported", "canvas_current", "canvas_options"}
    set_ui_theme(name)                              -> (ok, message)
    set_canvas_theme(name)                          -> (ok, message)
"""

import os
import sys

try:
    import clr
    for _ref in ('System', 'WindowsBase', 'PresentationCore', 'PresentationFramework', 'System.Drawing'):
        try:
            clr.AddReference(_ref)
        except Exception:
            pass
except Exception:
    clr = None

from pyrevit import forms
from GUI.WPF_Base import T3WPFWindow

try:
    from System import TimeSpan
except Exception:
    TimeSpan = None

try:
    from System.Windows import (WindowState, Thickness, CornerRadius,
                                Visibility, Clipboard, VerticalAlignment,
                                HorizontalAlignment, FontWeights, TextTrimming)
except Exception:
    WindowState = Thickness = CornerRadius = Visibility = Clipboard = VerticalAlignment = None
    HorizontalAlignment = FontWeights = TextTrimming = None

try:
    from System.Windows.Input import Cursors, Key
except Exception:
    Cursors = Key = None

try:
    from System.Windows.Media import (BrushConverter, Color, SolidColorBrush,
                                      LinearGradientBrush, GradientStop,
                                      GradientStopCollection)
except Exception:
    BrushConverter = Color = SolidColorBrush = LinearGradientBrush = GradientStop = GradientStopCollection = None

try:
    from System.Windows.Controls import (Canvas, Button, TextBlock, StackPanel,
                                         Border, Orientation)
except Exception:
    Canvas = Button = TextBlock = StackPanel = Border = Orientation = None

try:
    from System.Windows.Shapes import Ellipse
except Exception:
    Ellipse = None

try:
    from System.Windows.Threading import DispatcherTimer
except Exception:
    DispatcherTimer = None

try:
    from System.Drawing import Bitmap, Graphics
    from System.Drawing import Point as DrawPoint, Size as DrawSize
    HAS_DRAWING = True
except Exception:
    Bitmap = Graphics = DrawPoint = DrawSize = None
    HAS_DRAWING = False

# Absolute path to XAML
_XAML = os.path.join(os.path.dirname(__file__), 'Tools', 'BGTheme.xaml')

MAX_RECENTS = 10


# ------------------------------------------------------------------ helpers

def brush(hex_string):
    """Safely convert a hex string to a WPF Brush."""
    if not hex_string:
        return None
    try:
        if BrushConverter is not None:
            return BrushConverter().ConvertFromString(hex_string)
        from System.Windows.Media import BrushConverter as BC
        return BC().ConvertFromString(hex_string)
    except Exception:
        pass
    try:
        rgb = from_hex(hex_string)
        if rgb is not None:
            return solid(rgb)
    except Exception:
        pass
    return None


def clamp255(v):
    v = int(round(v))
    return max(0, min(v, 255))


def clamp01(v):
    return max(0.0, min(float(v), 1.0))


def to_hex(r, g, b):
    return "#%02X%02X%02X" % (clamp255(r), clamp255(g), clamp255(b))


def from_hex(hex_string):
    """Parse #RGB or #RRGGBB. Returns (r, g, b) or None."""
    if not hex_string:
        return None
    s = hex_string.strip().lstrip("#")
    if len(s) == 3:
        s = s[0] * 2 + s[1] * 2 + s[2] * 2
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return None


def rgb_to_hsv(r, g, b):
    """RGB 0-255 -> (hue 0-360, sat 0-1, val 0-1)."""
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    mx = max(rf, gf, bf)
    mn = min(rf, gf, bf)
    d = mx - mn
    if d == 0:
        h = 0.0
    elif mx == rf:
        h = 60.0 * (((gf - bf) / d) % 6.0)
    elif mx == gf:
        h = 60.0 * (((bf - rf) / d) + 2.0)
    else:
        h = 60.0 * (((rf - gf) / d) + 4.0)
    s = 0.0 if mx == 0 else d / mx
    return h, s, mx


def hsv_to_rgb(h, s, v):
    """(hue 0-360, sat 0-1, val 0-1) -> RGB 0-255."""
    h = h % 360.0
    c = v * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = v - c
    if h < 60:
        rp, gp, bp = c, x, 0.0
    elif h < 120:
        rp, gp, bp = x, c, 0.0
    elif h < 180:
        rp, gp, bp = 0.0, c, x
    elif h < 240:
        rp, gp, bp = 0.0, x, c
    elif h < 300:
        rp, gp, bp = x, 0.0, c
    else:
        rp, gp, bp = c, 0.0, x
    return (clamp255((rp + m) * 255.0),
            clamp255((gp + m) * 255.0),
            clamp255((bp + m) * 255.0))


def media_color(rgb):
    return Color.FromRgb(clamp255(rgb[0]), clamp255(rgb[1]), clamp255(rgb[2]))


def solid(rgb):
    return SolidColorBrush(media_color(rgb))


def luminance(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b


GRADIENT_PRESETS = [
    ("Day",       (68, 118, 189),  (205, 224, 240), (142, 134, 114)),
    ("Dawn",      (86, 84, 138),   (245, 196, 145), (94, 83, 77)),
    ("Dusk",      (48, 52, 84),    (232, 150, 110), (70, 64, 66)),
    ("Night",     (14, 18, 34),    (52, 66, 96),    (26, 28, 32)),
    ("Studio",    (68, 72, 82),    (150, 155, 164), (44, 46, 52)),
    ("Blueprint", (18, 42, 84),    (56, 88, 140),   (12, 26, 52)),
    ("Mist",      (196, 206, 214), (232, 236, 240), (150, 152, 158)),
]


class BackgroundThemeWindow(T3WPFWindow):
    """3-tab theme studio: model background / 3D gradient / Revit UI theme."""

    def __init__(self, config, presets, callbacks):
        T3WPFWindow.__init__(self, _XAML)

        self.presets = presets
        self.callbacks = callbacks or {}

        cfg = config or {}
        self._custom = list(cfg.get("custom_presets", []))
        self._recents = [tuple(c) for c in cfg.get("recents", [])]
        grad = cfg.get("gradient", {}) or {}
        self._grad = {
            "sky":     tuple(grad.get("sky", GRADIENT_PRESETS[0][1])),
            "horizon": tuple(grad.get("horizon", GRADIENT_PRESETS[0][2])),
            "ground":  tuple(grad.get("ground", GRADIENT_PRESETS[0][3])),
        }

        self._suspend = False
        self._eyedrop = False
        self._sample_bmp = None
        self._sample_gfx = None
        self._h, self._s, self._v = 0.0, 0.0, 0.0
        self._r, self._g, self._b = 0, 0, 0

        # Live-apply debounce timer
        self._live_timer = DispatcherTimer()
        self._live_timer.Interval = TimeSpan.FromMilliseconds(180)
        self._live_timer.Tick += self._on_live_tick

        # ------- wire events
        self.TabModel.Checked += self._on_tab_model
        self.Tab3D.Checked += self._on_tab_3d
        self.TabTheme.Checked += self._on_tab_theme

        self.SliderR.ValueChanged += self._on_slider
        self.SliderG.ValueChanged += self._on_slider
        self.SliderB.ValueChanged += self._on_slider
        self.HexApply.Click += self._on_hex_apply
        self.HexBox.KeyDown += self._on_hex_key
        self.BtnCopyHex.Click += self._on_copy_hex
        self.BtnEyedrop.Click += self._on_eyedrop
        self.BtnSavePreset.Click += self._on_save_preset
        self.BtnApply.Click += self._on_apply
        self.BtnClose.Click += self._on_close

        self.SvCanvas.MouseLeftButtonDown += self._on_sv_down
        self.SvCanvas.MouseMove += self._on_sv_move
        self.SvCanvas.MouseLeftButtonUp += self._on_sv_up
        self.SvCanvas.SizeChanged += self._on_canvas_size
        self.HueCanvas.MouseLeftButtonDown += self._on_hue_down
        self.HueCanvas.MouseMove += self._on_hue_move
        self.HueCanvas.MouseLeftButtonUp += self._on_hue_up
        self.HueCanvas.SizeChanged += self._on_canvas_size

        # eyedropper (window level, works with mouse capture)
        self.PreviewMouseMove += self._on_window_mouse_move
        self.PreviewMouseLeftButtonDown += self._on_window_mouse_down
        self.LostMouseCapture += self._on_lost_capture
        self.PreviewKeyDown += self._on_preview_key

        # 3D gradient tab
        for role, swatch, btn in (
                ("sky", self.SkySwatch, self.SkyFromPicker),
                ("horizon", self.HorizonSwatch, self.HorizonFromPicker),
                ("ground", self.GroundSwatch, self.GroundFromPicker)):
            swatch.Tag = role
            swatch.MouseLeftButtonUp += self._on_grad_swatch
            btn.Tag = role
            btn.Click += self._on_grad_from_picker
        self.BtnApplyGradient.Click += self._on_apply_gradient
        self.BtnClearBackground.Click += self._on_clear_background

        self.Closing += self._on_closing
        self.Loaded += self._on_loaded

        # ------- initial state
        self.LiveApply.IsChecked = bool(cfg.get("live_apply", False))
        self.AllViews3D.IsChecked = bool((grad or {}).get("all_views", False))

        self._build_presets()
        self._rebuild_custom_presets()
        self._rebuild_recents()
        self._build_grad_presets()
        self._update_grad_ui()

        self.set_rgb(cfg.get("r", 0), cfg.get("g", 0), cfg.get("b", 0))

    # ------------------------------------------------------------ chrome

    def minimize_button_clicked(self, sender, e):
        if WindowState is not None:
            self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender, e):
        if WindowState is not None:
            if self.WindowState == WindowState.Maximized:
                self.WindowState = WindowState.Normal
                btn = getattr(self, 'btn_maximize', None) or self.FindName('btn_maximize')
                if btn:
                    btn.ToolTip = "Maximize"
            else:
                self.WindowState = WindowState.Maximized
                btn = getattr(self, 'btn_maximize', None) or self.FindName('btn_maximize')
                if btn:
                    btn.ToolTip = "Restore"

    def close_button_clicked(self, sender, e):
        self.Close()


    def _on_preview_key(self, sender, args):
        self._handle_esc(args)

    def _handle_esc(self, args):
        if args.Handled or args.Key != Key.Escape:
            return
        args.Handled = True
        if self._eyedrop:
            self._stop_eyedrop("Screen pick cancelled")
        else:
            self.Close()

    # ------------------------------------------------------------ misc UI

    def _status(self, msg):
        try:
            self.status_text.Text = msg
        except Exception:
            pass

    def _cb(self, name):
        return self.callbacks.get(name)

    def _on_loaded(self, sender, args):
        self._place_thumbs()

    def _on_canvas_size(self, sender, args):
        self._place_thumbs()

    def _on_tab_model(self, sender, args):
        self.MainTabs.SelectedIndex = 0

    def _on_tab_3d(self, sender, args):
        self.MainTabs.SelectedIndex = 1
        self._refresh_3d_info()

    def _on_tab_theme(self, sender, args):
        self.MainTabs.SelectedIndex = 2
        self._rebuild_theme_buttons()

    # ------------------------------------------------------------ colour core


    def set_rgb(self, r, g, b):
        self._set_color(clamp255(r), clamp255(g), clamp255(b))

    def _set_color(self, r, g, b, source=None, hsv=None):
        """Central colour setter — syncs every control except `source`."""
        self._r, self._g, self._b = clamp255(r), clamp255(g), clamp255(b)
        if hsv is not None:
            self._h, self._s, self._v = hsv
        else:
            h, s, v = rgb_to_hsv(self._r, self._g, self._b)
            if s == 0:
                h = self._h          # keep hue stable on greyscale
            self._h, self._s, self._v = h, s, v

        self._suspend = True
        try:
            if source != "sliders":
                self.SliderR.Value = self._r
                self.SliderG.Value = self._g
                self.SliderB.Value = self._b
            self.LblR.Text = str(self._r)
            self.LblG.Text = str(self._g)
            self.LblB.Text = str(self._b)
            if source != "hex":
                self.HexBox.Text = to_hex(self._r, self._g, self._b)
            self.SvBase.Background = solid(hsv_to_rgb(self._h, 1.0, 1.0))
            self._place_thumbs()
            self._update_preview()
        finally:
            self._suspend = False

        self._on_color_changed()

    def _update_preview(self):
        hex_str = to_hex(self._r, self._g, self._b)
        self.PreviewBox.Background = solid((self._r, self._g, self._b))
        lum = luminance(self._r, self._g, self._b)
        fg = brush("#FFFFFF") if lum < 140 else brush("#0F172A")
        self.PreviewLbl.Foreground = fg
        self.PreviewLum.Foreground = fg
        self.PreviewLbl.Text = hex_str
        self.PreviewLum.Text = "lum %d%% · %s linework" % (
            int(round(lum / 255.0 * 100)),
            "light" if lum < 140 else "dark")

    def _place_thumbs(self):
        w = self.SvCanvas.ActualWidth
        h = self.SvCanvas.ActualHeight
        if w > 0 and h > 0:
            x = self._s * w - self.SvThumb.Width / 2.0
            y = (1.0 - self._v) * h - self.SvThumb.Height / 2.0
            Canvas.SetLeft(self.SvThumb, x)
            Canvas.SetTop(self.SvThumb, y)
        hh = self.HueCanvas.ActualHeight
        if hh > 0:
            ty = (self._h / 360.0) * hh - self.HueThumb.Height / 2.0
            ty = max(-self.HueThumb.Height / 2.0,
                     min(ty, hh - self.HueThumb.Height / 2.0))
            Canvas.SetLeft(self.HueThumb, 0)
            Canvas.SetTop(self.HueThumb, ty)

    def _on_color_changed(self):
        if self.LiveApply.IsChecked:
            self._live_timer.Stop()
            self._live_timer.Start()

    def _on_live_tick(self, sender, args):
        self._live_timer.Stop()
        self._apply_to_revit(record_recent=False, announce=False)

    # ------------------------------------------------------------ SV square

    def _sv_from_point(self, pos):
        w = self.SvCanvas.ActualWidth
        h = self.SvCanvas.ActualHeight
        if w <= 0 or h <= 0:
            return
        s = clamp01(pos.X / w)
        v = 1.0 - clamp01(pos.Y / h)
        r, g, b = hsv_to_rgb(self._h, s, v)
        self._set_color(r, g, b, source="sv", hsv=(self._h, s, v))

    def _on_sv_down(self, sender, args):
        self.SvCanvas.CaptureMouse()
        self._sv_from_point(args.GetPosition(self.SvCanvas))

    def _on_sv_move(self, sender, args):
        if self.SvCanvas.IsMouseCaptured:
            self._sv_from_point(args.GetPosition(self.SvCanvas))

    def _on_sv_up(self, sender, args):
        if self.SvCanvas.IsMouseCaptured:
            self.SvCanvas.ReleaseMouseCapture()

    # ------------------------------------------------------------ hue bar

    def _hue_from_point(self, pos):
        h = self.HueCanvas.ActualHeight
        if h <= 0:
            return
        hue = clamp01(pos.Y / h) * 360.0
        r, g, b = hsv_to_rgb(hue, self._s, self._v)
        self._set_color(r, g, b, source="hue", hsv=(hue, self._s, self._v))

    def _on_hue_down(self, sender, args):
        self.HueCanvas.CaptureMouse()
        self._hue_from_point(args.GetPosition(self.HueCanvas))

    def _on_hue_move(self, sender, args):
        if self.HueCanvas.IsMouseCaptured:
            self._hue_from_point(args.GetPosition(self.HueCanvas))

    def _on_hue_up(self, sender, args):
        if self.HueCanvas.IsMouseCaptured:
            self.HueCanvas.ReleaseMouseCapture()

    # ------------------------------------------------------------ sliders / hex

    def _on_slider(self, sender, args):
        if self._suspend:
            return
        self._set_color(int(self.SliderR.Value),
                        int(self.SliderG.Value),
                        int(self.SliderB.Value),
                        source="sliders")

    def _on_hex_apply(self, sender, args):
        rgb = from_hex(self.HexBox.Text)
        if rgb is None:
            self._status("Invalid HEX value")
            return
        self._set_color(rgb[0], rgb[1], rgb[2], source="hex")
        self._status("Colour set from HEX")

    def _on_hex_key(self, sender, args):
        if args.Key == Key.Enter:
            self._on_hex_apply(sender, args)
            args.Handled = True

    def _on_copy_hex(self, sender, args):
        try:
            Clipboard.SetText(to_hex(self._r, self._g, self._b))
            self._status("HEX copied to clipboard")
        except Exception:
            self._status("Could not access clipboard")

    # ------------------------------------------------------------ eyedropper

    def _on_eyedrop(self, sender, args):
        if self._eyedrop:
            self._stop_eyedrop("Screen pick cancelled")
            return
        self._eyedrop = True
        self.Cursor = Cursors.Cross
        if not self.CaptureMouse():
            self._eyedrop = False
            self.Cursor = Cursors.Arrow
            self._status("Could not capture the mouse")
            return
        self._status("Move over any pixel, click to pick — Esc cancels")

    def _stop_eyedrop(self, msg=None):
        self._eyedrop = False
        self.Cursor = Cursors.Arrow
        try:
            self.ReleaseMouseCapture()
        except Exception:
            pass
        if msg:
            self._status(msg)

    def _on_lost_capture(self, sender, args):
        if self._eyedrop:
            self._eyedrop = False
            self.Cursor = Cursors.Arrow
            self._status("Screen pick cancelled")

    def _sample_screen(self, args):
        if not HAS_DRAWING or Bitmap is None or Graphics is None:
            return None
        try:
            pos = args.GetPosition(self)
            sp = self.PointToScreen(pos)
            if self._sample_bmp is None:
                self._sample_bmp = Bitmap(1, 1)
                self._sample_gfx = Graphics.FromImage(self._sample_bmp)
            self._sample_gfx.CopyFromScreen(
                DrawPoint(int(sp.X), int(sp.Y)), DrawPoint(0, 0), DrawSize(1, 1))
            c = self._sample_bmp.GetPixel(0, 0)
            return (c.R, c.G, c.B)
        except Exception:
            return None

    def _on_window_mouse_move(self, sender, args):
        if not self._eyedrop:
            return
        rgb = self._sample_screen(args)
        if rgb is not None:
            self._set_color(rgb[0], rgb[1], rgb[2])

    def _on_window_mouse_down(self, sender, args):
        if not self._eyedrop:
            return
        rgb = self._sample_screen(args)
        if rgb is not None:
            self._set_color(rgb[0], rgb[1], rgb[2])
        self._stop_eyedrop("Picked %s from screen" % to_hex(self._r, self._g, self._b))
        args.Handled = True

    # ------------------------------------------------------------ style helper

    def _apply_button_style(self, btn, style_key):
        if not btn or not style_key:
            return
        try:
            st = self.TryFindResource(style_key)
            if st is not None:
                btn.Style = st
                return
        except Exception:
            pass
        try:
            if hasattr(self.Resources, "Contains") and self.Resources.Contains(style_key):
                btn.Style = self.Resources[style_key]
        except Exception:
            pass

    # ------------------------------------------------------------ presets

    def _make_chip(self, name, rgb, click_handler, tag):
        btn = Button()
        self._apply_button_style(btn, "T3.Button.Base")
        btn.Background = solid(rgb)

        lum = luminance(rgb[0], rgb[1], rgb[2])
        if lum > 180:
            btn.BorderBrush = brush("#C8C8D0")
        elif lum < 50:
            btn.BorderBrush = brush("#3A3A42")
        else:
            btn.BorderBrush = solid((max(0, rgb[0] - 25), max(0, rgb[1] - 25), max(0, rgb[2] - 25)))
        btn.BorderThickness = Thickness(1)

        btn.Height = 26
        btn.Padding = Thickness(6, 0, 6, 0)
        btn.Margin = Thickness(0, 0, 4, 4)
        if Cursors is not None:
            btn.Cursor = Cursors.Hand

        txt = TextBlock()
        txt.Text = name
        txt.FontSize = 11.5
        if FontWeights is not None:
            txt.FontWeight = FontWeights.SemiBold
        if VerticalAlignment is not None:
            txt.VerticalAlignment = VerticalAlignment.Center
        if HorizontalAlignment is not None:
            txt.HorizontalAlignment = HorizontalAlignment.Center
        if TextTrimming is not None:
            txt.TextTrimming = TextTrimming.CharacterEllipsis

        if lum < 140:
            txt.Foreground = brush("#FFFFFF")
        else:
            txt.Foreground = brush("#18181B")

        btn.Content = txt
        btn.Tag = tag
        btn.ToolTip = "%s (%s)" % (name, to_hex(*rgb))
        btn.Click += click_handler
        return btn

    def _build_presets(self):
        self.PresetGrid.Children.Clear()
        for name, rgb in self.presets:
            tag = "%d,%d,%d" % (rgb[0], rgb[1], rgb[2])
            self.PresetGrid.Children.Add(
                self._make_chip(name, rgb, self._on_preset_click, tag))

    def _on_preset_click(self, sender, args):
        try:
            parts = str(sender.Tag).split(",")
            self.set_rgb(int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            pass

    def _rebuild_custom_presets(self):
        self.CustomPresetPanel.Children.Clear()
        for p in self._custom:
            rgb = tuple(p.get("rgb", (0, 0, 0)))
            chip = self._make_chip(p.get("name", "?"), rgb,
                                   self._on_custom_load, p.get("name", "?"))
            chip.ToolTip = "Click: load · Right-click: delete"
            chip.MouseRightButtonUp += self._on_custom_delete
            self.CustomPresetPanel.Children.Add(chip)

    def _find_custom(self, name):
        for p in self._custom:
            if p.get("name") == name:
                return p
        return None

    def _on_custom_load(self, sender, args):
        p = self._find_custom(str(sender.Tag))
        if p:
            rgb = p.get("rgb", (0, 0, 0))
            self.set_rgb(rgb[0], rgb[1], rgb[2])

    def _on_custom_delete(self, sender, args):
        name = str(sender.Tag)
        p = self._find_custom(name)
        if p:
            self._custom.remove(p)
            self._rebuild_custom_presets()
            self._save_config()
            self._status("Preset '%s' removed" % name)
        args.Handled = True

    def _on_save_preset(self, sender, args):
        name = (self.PresetNameBox.Text or "").strip()
        if not name:
            name = to_hex(self._r, self._g, self._b)
        existing = self._find_custom(name)
        if existing:
            existing["rgb"] = [self._r, self._g, self._b]
        else:
            self._custom.append({"name": name,
                                 "rgb": [self._r, self._g, self._b]})
        self.PresetNameBox.Text = ""
        self._rebuild_custom_presets()
        self._save_config()
        self._status("Preset '%s' saved" % name)

    # ------------------------------------------------------------ recents

    def _push_recent(self, rgb):
        rgb = (clamp255(rgb[0]), clamp255(rgb[1]), clamp255(rgb[2]))
        self._recents = [c for c in self._recents if tuple(c) != rgb]
        self._recents.insert(0, rgb)
        self._recents = self._recents[:MAX_RECENTS]
        self._rebuild_recents()

    def _rebuild_recents(self):
        self.RecentPanel.Children.Clear()
        if not self._recents:
            empty = TextBlock()
            empty.Text = "No colours applied yet"
            empty.FontSize = 11
            empty.Foreground = brush("#9A9AA2")
            self.RecentPanel.Children.Add(empty)
            return
        for rgb in self._recents:
            sw = Border()
            sw.Width = 24
            sw.Height = 24
            sw.CornerRadius = CornerRadius(4)
            sw.Margin = Thickness(0, 0, 6, 6)
            sw.Background = solid(rgb)
            sw.BorderBrush = brush("#D4D4DA")
            sw.BorderThickness = Thickness(1)
            sw.Cursor = Cursors.Hand
            sw.ToolTip = to_hex(rgb[0], rgb[1], rgb[2])
            sw.Tag = "%d,%d,%d" % (rgb[0], rgb[1], rgb[2])
            sw.MouseLeftButtonUp += self._on_recent_click
            self.RecentPanel.Children.Add(sw)

    def _on_recent_click(self, sender, args):
        try:
            parts = str(sender.Tag).split(",")
            self.set_rgb(int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            pass

    # ------------------------------------------------------------ 3D gradient

    def _refresh_3d_info(self):
        cb = self._cb("get_3d_context")
        if not cb:
            self.View3DInfo.Text = "3D background is not available in this session."
            return
        try:
            ctx = cb() or {}
        except Exception as ex:
            self.View3DInfo.Text = "Could not read the active view: %s" % ex
            return
        name = ctx.get("view_name")
        count = ctx.get("count_3d", 0)
        if name:
            self.View3DInfo.Text = ("Active 3D view: %s   ·   %d 3D view(s) in "
                                    "the project" % (name, count))
        else:
            self.View3DInfo.Text = ("The active view is not a 3D view — open a "
                                    "3D view, or tick 'all 3D views' below "
                                    "(%d found)." % count)

    def _update_grad_ui(self):
        self.SkySwatch.Background = solid(self._grad["sky"])
        self.HorizonSwatch.Background = solid(self._grad["horizon"])
        self.GroundSwatch.Background = solid(self._grad["ground"])
        self.SkyHex.Text = to_hex(*self._grad["sky"])
        self.HorizonHex.Text = to_hex(*self._grad["horizon"])
        self.GroundHex.Text = to_hex(*self._grad["ground"])
        stops = GradientStopCollection()
        stops.Add(GradientStop(media_color(self._grad["sky"]), 0.0))
        stops.Add(GradientStop(media_color(self._grad["horizon"]), 0.5))
        stops.Add(GradientStop(media_color(self._grad["ground"]), 1.0))
        self.GradPreview.Background = LinearGradientBrush(stops, 90.0)

    def _on_grad_swatch(self, sender, args):
        role = str(sender.Tag)
        rgb = self._grad.get(role)
        if rgb:
            self.set_rgb(rgb[0], rgb[1], rgb[2])
            self._status("%s colour loaded into the picker" % role.capitalize())

    def _on_grad_from_picker(self, sender, args):
        role = str(sender.Tag)
        self._grad[role] = (self._r, self._g, self._b)
        self._update_grad_ui()
        self._status("%s set to %s" % (role.capitalize(),
                                       to_hex(self._r, self._g, self._b)))

    def _build_grad_presets(self):
        self.GradPresetPanel.Children.Clear()
        for idx, item in enumerate(GRADIENT_PRESETS):
            name = item[0]
            btn = Button()
            self._apply_button_style(btn, "T3.Button.Secondary")
            btn.Height = 24
            btn.Padding = Thickness(8, 0, 8, 0)
            btn.Margin = Thickness(0, 0, 4, 4)
            sp = StackPanel()
            sp.Orientation = Orientation.Horizontal
            for rgb in item[1:]:
                dot = Ellipse()
                dot.Width = 8
                dot.Height = 8
                dot.Fill = solid(rgb)
                dot.Stroke = brush("#DCDCE0")
                dot.StrokeThickness = 1
                dot.Margin = Thickness(0, 0, 4, 0)
                dot.VerticalAlignment = VerticalAlignment.Center
                sp.Children.Add(dot)
            txt = TextBlock()
            txt.Text = name
            txt.FontSize = 11.5
            txt.Margin = Thickness(4, 0, 0, 0)
            txt.VerticalAlignment = VerticalAlignment.Center
            sp.Children.Add(txt)
            btn.Content = sp
            btn.Tag = idx
            btn.Click += self._on_grad_preset
            self.GradPresetPanel.Children.Add(btn)

    def _on_grad_preset(self, sender, args):
        try:
            name, sky, horizon, ground = GRADIENT_PRESETS[int(sender.Tag)]
        except Exception:
            return
        self._grad = {"sky": sky, "horizon": horizon, "ground": ground}
        self._update_grad_ui()
        self._status("Gradient preset '%s' loaded — press Apply Gradient" % name)

    def _on_apply_gradient(self, sender, args):
        cb = self._cb("apply_view_gradient")
        if not cb:
            self._status("3D background is not available in this session")
            return
        try:
            ok, msg = cb(self._grad["sky"], self._grad["horizon"],
                         self._grad["ground"], bool(self.AllViews3D.IsChecked))
        except Exception as ex:
            ok, msg = False, "Failed: %s" % ex
        self._status(msg)
        if ok:
            self._save_config()
        self._refresh_3d_info()

    def _on_clear_background(self, sender, args):
        cb = self._cb("clear_view_background")
        if not cb:
            self._status("3D background is not available in this session")
            return
        try:
            ok, msg = cb(bool(self.AllViews3D.IsChecked))
        except Exception as ex:
            ok, msg = False, "Failed: %s" % ex
        self._status(msg)
        self._refresh_3d_info()

    # ------------------------------------------------------------ UI theme

    def _rebuild_theme_buttons(self):
        cb = self._cb("get_theme_info")
        info = {}
        if cb:
            try:
                info = cb() or {}
            except Exception:
                info = {}
        supported = info.get("supported", False)
        self.ThemeBtnPanel.Children.Clear()
        self.CanvasBtnPanel.Children.Clear()

        if not supported:
            self.ThemeInfo.Text = ("UI theme switching requires the "
                                   "UIThemeManager API (Revit 2024 or newer) — "
                                   "not available in this session.")
            self.ThemeLbl.Visibility = Visibility.Collapsed
            self.ThemeBtnPanel.Visibility = Visibility.Collapsed
            self.CanvasLbl.Visibility = Visibility.Collapsed
            self.CanvasBtnPanel.Visibility = Visibility.Collapsed
            return

        current = info.get("current")
        parts = ["Current UI theme: %s" % current]
        if info.get("canvas_supported"):
            parts.append("Canvas theme: %s" % info.get("canvas_current"))
        self.ThemeInfo.Text = "   ·   ".join(parts)

        self.ThemeLbl.Visibility = Visibility.Visible
        self.ThemeBtnPanel.Visibility = Visibility.Visible
        for name in info.get("options", []):
            btn = Button()
            btn.Content = name
            btn.Width = 110
            btn.Margin = Thickness(0, 0, 8, 0)
            style_key = "T3.Button.Primary" if name == current else "T3.Button.Secondary"
            self._apply_button_style(btn, style_key)
            btn.Tag = name
            btn.Click += self._on_theme_click
            self.ThemeBtnPanel.Children.Add(btn)

        if info.get("canvas_supported"):
            self.CanvasLbl.Visibility = Visibility.Visible
            self.CanvasBtnPanel.Visibility = Visibility.Visible
            canvas_current = info.get("canvas_current")
            for name in info.get("canvas_options", []):
                btn = Button()
                btn.Content = name
                btn.Width = 110
                btn.Margin = Thickness(0, 0, 8, 0)
                style_key = ("T3.Button.Primary" if name == canvas_current
                             else "T3.Button.Secondary")
                self._apply_button_style(btn, style_key)
                btn.Tag = name
                btn.Click += self._on_canvas_theme_click
                self.CanvasBtnPanel.Children.Add(btn)
        else:
            self.CanvasLbl.Visibility = Visibility.Collapsed
            self.CanvasBtnPanel.Visibility = Visibility.Collapsed

    def _on_theme_click(self, sender, args):
        cb = self._cb("set_ui_theme")
        if not cb:
            return
        try:
            ok, msg = cb(str(sender.Tag))
        except Exception as ex:
            ok, msg = False, "Failed: %s" % ex
        self._status(msg)
        self._rebuild_theme_buttons()

    def _on_canvas_theme_click(self, sender, args):
        cb = self._cb("set_canvas_theme")
        if not cb:
            return
        try:
            ok, msg = cb(str(sender.Tag))
        except Exception as ex:
            ok, msg = False, "Failed: %s" % ex
        self._status(msg)
        self._rebuild_theme_buttons()

    # ------------------------------------------------------------ apply / persist

    def _apply_to_revit(self, record_recent=True, announce=True):
        cb = self._cb("apply_background")
        if not cb:
            self._status("Apply is not available in this session")
            return
        try:
            cb(self._r, self._g, self._b)
        except Exception as ex:
            self._status("Apply failed: %s" % ex)
            return
        if record_recent:
            self._push_recent((self._r, self._g, self._b))
        if announce:
            self._status("Applied %s to the model background"
                         % to_hex(self._r, self._g, self._b))

    def _on_apply(self, sender, args):
        self._apply_to_revit(record_recent=True, announce=True)
        self._save_config()

    def _collect_config(self):
        return {
            "r": self._r, "g": self._g, "b": self._b,
            "live_apply": bool(self.LiveApply.IsChecked),
            "custom_presets": self._custom,
            "recents": [list(c) for c in self._recents],
            "gradient": {
                "sky": list(self._grad["sky"]),
                "horizon": list(self._grad["horizon"]),
                "ground": list(self._grad["ground"]),
                "all_views": bool(self.AllViews3D.IsChecked),
            },
        }

    def _save_config(self):
        cb = self._cb("save_config")
        if cb:
            try:
                cb(self._collect_config())
            except Exception:
                pass

    def _on_closing(self, sender, args):
        if self._eyedrop:
            self._stop_eyedrop()
        self._live_timer.Stop()
        self._save_config()
        try:
            if self._sample_gfx is not None:
                self._sample_gfx.Dispose()
            if self._sample_bmp is not None:
                self._sample_bmp.Dispose()
        except Exception:
            pass

    def _on_close(self, sender, args):
        self.Close()


def show_bg_theme_dialog(config, presets, callbacks):
    """Factory function to create and show the BG Theme dialog."""
    dlg = BackgroundThemeWindow(config, presets, callbacks)
    dlg.ShowDialog()
    return dlg
