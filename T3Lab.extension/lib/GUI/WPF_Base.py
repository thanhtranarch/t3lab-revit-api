# -*- coding: utf-8 -*-
"""
WPF Base
========
Universal WPF Window base class and loader for pyRevit under CPython 3 and IronPython.

Features:
  1. Full CPython 3 (Python.NET) compatibility via System.Windows.Markup.XamlReader.
  2. Automatic extraction and dynamic binding of XAML event handlers (Click, SelectionChanged, etc.).
  3. Automatic binding of all named elements (x:Name / Name) to instance attributes.
  4. Automatic Revit window ownership and host styling.
  5. Backward compatibility with IronPython 2.7/3.x.
  6. Auto-monkeypatch for pyrevit.forms.WPFWindow in CPython so existing dialogs run seamlessly.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__author__  = "Tran Tien Thanh"
__title__   = "WPF Base"

import os
import io
import re
import sys


# ── STALENESS DETECTOR ───────────────────────────────────────────────────────
# The CPython engine is PERSISTENT: `sys.modules` survives between clicks, so a
# module keeps running the bytecode it was first imported with until pyRevit is
# reloaded. pyRevit renders tracebacks by reading the CURRENT file from disk, so
# a stale module produces a traceback whose line numbers are real but whose code
# is not — an error message that no longer exists in the source, pointing at a
# line that belongs to a different function. That has cost two debugging rounds.
#
# Recording the file's mtime AT IMPORT lets any error path say so outright.
try:
    _LOADED_MTIME = os.path.getmtime(os.path.abspath(__file__))
except Exception:
    _LOADED_MTIME = None


def module_is_stale():
    """True when this file changed on disk after the running module was imported."""
    if _LOADED_MTIME is None:
        return False
    try:
        return os.path.getmtime(os.path.abspath(__file__)) > _LOADED_MTIME + 1.0
    except Exception:
        return False


def stale_module_note():
    """A line to append to any error raised out of a possibly stale module."""
    if not module_is_stale():
        return ""
    return ("\n\nNOTE: GUI/WPF_Base.py changed on disk AFTER this module was "
            "loaded, so Revit is running the older copy and the line numbers "
            "above do not match the code that actually ran. Reload pyRevit "
            "(pyRevit tab -> Reload) and try again before reading anything "
            "else in this traceback.")

# ─── CLR References ────────────────────────────────────────────────────────────
try:
    import clr
    for _ref in (
        "System",
        "System.IO",
        "System.Xml",
        "System.Xml.ReaderWriter",
        "System.Private.Xml",
        "WindowsBase",
        "PresentationCore",
        "PresentationFramework",
        "System.Xaml",
        "System.Diagnostics.Process",
    ):
        try:
            clr.AddReference(_ref)
        except Exception:
            pass
except Exception:
    clr = None

try:
    from System.Windows import Window, WindowState, Visibility
except Exception:
    class Window(object):
        pass
    class WindowState(object):
        Normal = 0
        Minimized = 1
        Maximized = 2
    class Visibility(object):
        Visible = 0
        Hidden = 1
        Collapsed = 2

try:
    from System import Action
    from System.Windows.Threading import DispatcherFrame, DispatcherPriority
except Exception:
    Action = None
    DispatcherFrame = None
    DispatcherPriority = None

try:
    from System.Windows.Input import Key
except Exception:
    Key = None

try:
    from System.Windows.Interop import WindowInteropHelper
except Exception:
    WindowInteropHelper = None

try:
    from System.Windows.Markup import XamlReader
except Exception:
    try:
        import System.Windows.Markup
        XamlReader = System.Windows.Markup.XamlReader
    except Exception:
        XamlReader = None

try:
    from System.IO import StringReader
except Exception:
    try:
        import System.IO
        StringReader = System.IO.StringReader
    except Exception:
        StringReader = None

try:
    from System.Xml import XmlReader
except Exception:
    try:
        import System.Xml
        XmlReader = System.Xml.XmlReader
    except Exception:
        XmlReader = None

try:
    from System.Diagnostics.Process import Start
except Exception:
    def Start(url):
        import os
        try:
            os.startfile(url)
        except Exception:
            pass

# Check if running in IronPython
try:
    from pyrevit.compat import IRONPY
except Exception:
    IRONPY = 'IronPython' in sys.version

# Set of standard WPF event attribute names extracted from XAML
_EVENT_NAMES = {
    'Click', 'Checked', 'Unchecked', 'SelectionChanged', 'TextChanged',
    'MouseLeftButtonDown', 'MouseLeftButtonUp', 'MouseRightButtonDown', 'MouseRightButtonUp',
    'MouseDoubleClick', 'MouseEnter', 'MouseLeave', 'MouseMove', 'MouseWheel',
    'Loaded', 'Unloaded', 'Closing', 'Closed', 'Initialized',
    'ContentRendered', 'Activated', 'Deactivated', 'StateChanged', 'LocationChanged', 'SourceInitialized',
    'KeyDown', 'KeyUp', 'PreviewKeyDown', 'PreviewKeyUp', 'PreviewTextInput',
    'DropDownClosed', 'DropDownOpened', 'ValueChanged', 'Drop', 'DragOver',
    'PreviewMouseLeftButtonDown', 'PreviewMouseLeftButtonUp', 'MouseDown', 'MouseUp',
    'LostFocus', 'GotFocus', 'ScrollChanged', 'SizeChanged',
    'CellEditEnding', 'RowEditEnding', 'BeginningEdit', 'SelectedDateChanged'
}


def to_items_source(items):
    """Convert a Python sequence into a .NET collection compatible with WPF ItemsSource under CPython 3 / PythonNet.

    In IronPython 2, Python lists coerced automatically to System.Collections.IEnumerable.
    In PythonNet (CPython 3), assigning a Python list to ItemsSource raises:
    TypeError: 'list' value cannot be converted to System.Collections.IEnumerable.
    This helper returns an ObservableCollection[Object] (or List[Object]) that WPF recognizes.
    """
    if items is None:
        return None
    if not isinstance(items, (list, tuple, set)):
        try:
            from System.Collections import IEnumerable
            if isinstance(items, IEnumerable):
                return items
        except Exception:
            pass
    try:
        from System.Collections.ObjectModel import ObservableCollection
        from System import Object
        coll = ObservableCollection[Object]()
        for item in items:
            coll.Add(item)
        return coll
    except Exception:
        try:
            from System.Collections.Generic import List
            from System import Object
            net_list = List[Object]()
            for item in items:
                net_list.Add(item)
            return net_list
        except Exception:
            return items


def set_items_source(control, items):
    """Safely assign items to control.ItemsSource under both CPython 3 and IronPython."""
    if control is not None:
        control.ItemsSource = to_items_source(items)


def _sanitize_xaml(xaml_content):
    """
    Sanitize XAML for CPython XamlReader:
    Strips event handler attributes and records them with target element names.
    Returns (clean_xaml_string, event_bindings, named_elements).
    """
    event_bindings = []
    named_elements = set()
    counter = [0]

    for m in re.finditer(r'(?:x:)?Name\s*=\s*"([^"]+)"', xaml_content):
        named_elements.add(m.group(1))

    def process_tag(match):
        tag = match.group(0)
        has_event = False
        for evt in _EVENT_NAMES:
            if re.search(r'\b' + evt + r'\s*=\s*"[^"]*"', tag):
                has_event = True
                break
        if not has_event:
            return tag

        is_root_window = tag.startswith('<Window')
        name_match = re.search(r'(?:x:)?Name\s*=\s*"([^"]+)"', tag)
        if name_match:
            elem_name = name_match.group(1)
        elif is_root_window:
            elem_name = "__root__"
        else:
            counter[0] += 1
            elem_name = "__t3_dyn_" + str(counter[0])
            named_elements.add(elem_name)
            tag = re.sub(r'^(<[A-Za-z0-9_.:]+)', r'\1 x:Name="' + elem_name + '"', tag)

        for evt in _EVENT_NAMES:
            evt_match = re.search(r'\b(' + evt + r')\s*=\s*"([^"]+)"', tag)
            if evt_match:
                event_name = evt_match.group(1)
                handler_name = evt_match.group(2)
                event_bindings.append((elem_name, event_name, handler_name))
                tag = re.sub(r'\s*\b' + evt + r'\s*=\s*"[^"]*"', '', tag)

        return tag

    clean_xaml = re.sub(r'<[A-Za-z0-9_.:]+(?:\s+[^>]*?)?/?>', process_tag, xaml_content)
    return clean_xaml, event_bindings, named_elements


def setup_window_logo(win_or_elem):
    """Auto-bind T3Lab logo to logo_image and window Icon if present on any Window or FrameworkElement."""
    try:
        logo_elem = getattr(win_or_elem, 'logo_image', None)
        if logo_elem is None and hasattr(win_or_elem, 'FindName'):
            try:
                logo_elem = win_or_elem.FindName('logo_image')
            except Exception:
                logo_elem = None
        if logo_elem is None and hasattr(win_or_elem, 'Content') and win_or_elem.Content is not None and hasattr(win_or_elem.Content, 'FindName'):
            try:
                logo_elem = win_or_elem.Content.FindName('logo_image')
            except Exception:
                logo_elem = None

        _gui_dir = os.path.dirname(os.path.abspath(__file__))
        _logo_path = None
        for _cand in ('T3Lab_logo_tight.png', 'T3Lab_logo.png'):
            _p = os.path.join(_gui_dir, _cand)
            if os.path.exists(_p):
                _logo_path = _p
                break
        if not _logo_path:
            _alt_dir = os.path.join(os.path.dirname(_gui_dir), 'lib', 'GUI')
            for _cand in ('T3Lab_logo_tight.png', 'T3Lab_logo.png'):
                _p = os.path.join(_alt_dir, _cand)
                if os.path.exists(_p):
                    _logo_path = _p
                    break

        if _logo_path and os.path.exists(_logo_path):
            from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
            from System import Uri, UriKind
            bitmap = BitmapImage()
            bitmap.BeginInit()
            bitmap.CacheOption = BitmapCacheOption.OnLoad
            bitmap.UriSource = Uri(_logo_path, UriKind.Absolute)
            bitmap.EndInit()
            bitmap.Freeze()
            if logo_elem is not None:
                logo_elem.Source = bitmap
            try:
                if hasattr(win_or_elem, 'Icon') and (not win_or_elem.Icon):
                    win_or_elem.Icon = bitmap
            except Exception:
                pass
            return bitmap
    except Exception:
        pass
    return None


class T3WPFWindow(Window):
    """
    Universal WPF Window base class supporting both CPython 3 and IronPython.
    """

    def __init__(self, xaml_source, literal_string=False, handle_esc=True, set_owner=True):
        try:
            Window.__init__(self)
        except Exception:
            pass
        self._xaml_source = xaml_source
        self.load_xaml(xaml_source, literal_string=literal_string,
                       handle_esc=handle_esc, set_owner=set_owner)

    def FindName(self, name):
        """
        Finds a named element across native WPF NameScope, instance attributes,
        and the visual Content tree.
        """
        try:
            elem = super(T3WPFWindow, self).FindName(name)
            if elem is not None:
                return elem
        except Exception:
            pass

        elem = getattr(self, name, None)
        if elem is not None:
            return elem

        try:
            content = getattr(self, 'Content', None)
            if content is not None and hasattr(content, 'FindName'):
                elem = content.FindName(name)
                if elem is not None:
                    return elem
        except Exception:
            pass

        return None

    def load_xaml(self, xaml_source, literal_string=False, handle_esc=True, set_owner=True):
        """Loads XAML and wires named elements + event handlers."""
        # Read XAML content
        if literal_string:
            xaml_content = xaml_source
        else:
            if not os.path.isabs(xaml_source):
                # Search relative to calling module or GUI/Tools
                _here = os.path.dirname(os.path.abspath(__file__))
                _tools = os.path.join(_here, "Tools")
                cand1 = os.path.join(_tools, os.path.basename(xaml_source))
                cand2 = os.path.abspath(xaml_source)
                xaml_path = cand1 if os.path.exists(cand1) else cand2
            else:
                xaml_path = xaml_source

            with io.open(xaml_path, 'r', encoding='utf-8-sig') as f:
                xaml_content = f.read()

        if IRONPY:
            # IronPython engine: use wpf.LoadComponent if available
            try:
                import wpf
                if literal_string:
                    wpf.LoadComponent(self, StringReader(xaml_content))
                else:
                    wpf.LoadComponent(self, xaml_path)
            except Exception:
                self._load_via_xaml_reader(xaml_content)
        else:
            # CPython engine: use sanitized XamlReader
            self._load_via_xaml_reader(xaml_content)

        if set_owner:
            self.setup_owner()
        if handle_esc:
            self.setup_default_handlers()

    def _load_via_xaml_reader(self, xaml_content):
        """Hydrates Window via System.Windows.Markup.XamlReader with multi-strategy fallbacks."""
        if xaml_content is not None:
            xaml_content = xaml_content.lstrip('\ufeff \t\r\n')
        clean_xaml, event_bindings, named_elements = _sanitize_xaml(xaml_content)

        loaded_win = None
        errors = []

        # ── Strategy 1: Direct System.Windows.Markup.XamlReader.Parse ──
        try:
            from System.Windows.Markup import XamlReader as _xr_direct
            if _xr_direct is not None and hasattr(_xr_direct, 'Parse'):
                loaded_win = _xr_direct.Parse(clean_xaml)
        except Exception as ex:
            errors.append("XamlReader.Parse: {}".format(ex))
            loaded_win = None

        # ── Strategy 2: Direct MemoryStream -> XamlReader.Load(Stream) ──
        if loaded_win is None:
            try:
                import System.IO
                import System.Text
                from System.Windows.Markup import XamlReader as _xr_direct
                if _xr_direct is not None and hasattr(_xr_direct, 'Load'):
                    raw_bytes = System.Text.Encoding.UTF8.GetBytes(clean_xaml)
                    ms = System.IO.MemoryStream(raw_bytes)
                    loaded_win = _xr_direct.Load(ms)
            except Exception as ex:
                errors.append("XamlReader.Load(Stream): {}".format(ex))
                loaded_win = None

        # ── Strategy 3: AppDomain Reflection for PresentationFramework XamlReader ──
        if loaded_win is None:
            try:
                import System
                import clr
                xr_type = None
                # Search PresentationFramework specifically
                for asm in System.AppDomain.CurrentDomain.GetAssemblies():
                    try:
                        asm_name = asm.FullName or ""
                        if asm_name.startswith("PresentationFramework"):
                            t = asm.GetType("System.Windows.Markup.XamlReader")
                            if t is not None:
                                xr_type = t
                                break
                    except Exception:
                        continue

                # Fallback: search all loaded assemblies
                if xr_type is None:
                    for asm in System.AppDomain.CurrentDomain.GetAssemblies():
                        try:
                            t = asm.GetType("System.Windows.Markup.XamlReader")
                            if t is not None:
                                xr_type = t
                                break
                        except Exception:
                            continue

                if xr_type is not None:
                    # 3a. Reflection Parse(string)
                    try:
                        str_type = clr.GetClrType(System.String) if hasattr(clr, 'GetClrType') else System.String
                        parse_m = xr_type.GetMethod("Parse", System.Array[System.Type]([str_type]))
                        if parse_m is not None:
                            loaded_win = parse_m.Invoke(None, System.Array[System.Object]([clean_xaml]))
                    except Exception as ex:
                        errors.append("Reflection XamlReader.Parse: {}".format(ex))

                    # 3b. Reflection Load(Stream)
                    if loaded_win is None:
                        try:
                            import System.IO
                            import System.Text
                            raw_bytes = System.Text.Encoding.UTF8.GetBytes(clean_xaml)
                            ms = System.IO.MemoryStream(raw_bytes)
                            for m in xr_type.GetMethods():
                                if m.Name == "Load":
                                    params = m.GetParameters()
                                    if len(params) == 1 and params[0].ParameterType.Name == "Stream":
                                        loaded_win = m.Invoke(None, System.Array[System.Object]([ms]))
                                        break
                        except Exception as ex:
                            errors.append("Reflection XamlReader.Load(Stream): {}".format(ex))
            except Exception as ex:
                errors.append("AppDomain reflection: {}".format(ex))

        # ── Strategy 4: XmlReader.Create + XamlReader.Load(XmlReader) ──
        if loaded_win is None:
            try:
                import System.IO
                from System.Windows.Markup import XamlReader as _xr
                xml_r = None
                try:
                    from System.Xml import XmlReader as xml_r
                except Exception:
                    pass
                if xml_r is None or not hasattr(xml_r, 'Create'):
                    import clr
                    for ref in ("System.Xml.ReaderWriter", "System.Private.Xml", "System.Xml"):
                        try:
                            clr.AddReference(ref)
                        except Exception:
                            pass
                    try:
                        from System.Xml import XmlReader as xml_r
                    except Exception:
                        pass
                if xml_r is not None and hasattr(xml_r, 'Create') and _xr is not None and hasattr(_xr, 'Load'):
                    sr = System.IO.StringReader(clean_xaml)
                    xml_reader = xml_r.Create(sr)
                    loaded_win = _xr.Load(xml_reader)
            except Exception as ex:
                errors.append("XmlReader.Create + Load: {}".format(ex))

        if loaded_win is None:
            err_msg = "\n  - ".join(errors) if errors else "XAML parser returned None without error"
            raise RuntimeError(
                "Failed to parse XAML for {}:\n  - {}{}".format(
                    getattr(self, '_xaml_source', '<inline XAML>'),
                    err_msg,
                    stale_module_note()))

        # Copy essential window properties
        for prop in ('Title', 'Width', 'Height', 'MinWidth', 'MinHeight',
                     'MaxWidth', 'MaxHeight', 'WindowStartupLocation',
                     'WindowStyle', 'AllowsTransparency', 'Background',
                     'ResizeMode', 'ShowInTaskbar', 'FontFamily', 'FontSize'):
            try:
                val = getattr(loaded_win, prop, None)
                if val is not None:
                    setattr(self, prop, val)
            except BaseException:
                # If setting property failed due to WPF property system state (e.g. LocalValueEnumerationInvalidated),
                # try deferring Title set via Dispatcher once the window is idle.
                if prop == 'Title':
                    try:
                        disp = getattr(self, 'Dispatcher', None)
                        if disp is not None and Action is not None:
                            val = getattr(loaded_win, 'Title', None)
                            if val:
                                def _set_title(t=val):
                                    try:
                                        self.Title = t
                                    except BaseException:
                                        pass
                                disp.BeginInvoke(Action(_set_title))
                    except BaseException:
                        pass

        # Copy WindowChrome if present
        try:
            from System.Windows.Shell import WindowChrome
            chrome = WindowChrome.GetWindowChrome(loaded_win)
            if chrome is not None:
                WindowChrome.SetWindowChrome(self, chrome)
        except BaseException:
            pass

        try:
            self.Resources = loaded_win.Resources
        except BaseException:
            pass

        # Transfer NameScope so FindName works on self
        try:
            NameScope_cls = None
            try:
                from System.Windows import NameScope as NameScope_cls
            except BaseException:
                pass
            if NameScope_cls is None:
                try:
                    from System.Windows.Markup import NameScope as NameScope_cls
                except BaseException:
                    pass
            if NameScope_cls is not None:
                scope = NameScope_cls.GetNameScope(loaded_win)
                if scope is not None:
                    NameScope_cls.SetNameScope(self, scope)
                else:
                    NameScope_cls.SetNameScope(self, NameScope_cls())
        except BaseException:
            pass

        # Bind all named elements as instance attributes before detaching content
        for name in named_elements:
            try:
                elem = loaded_win.FindName(name)
                if elem is None and hasattr(loaded_win, 'Content') and loaded_win.Content is not None and hasattr(loaded_win.Content, 'FindName'):
                    elem = loaded_win.Content.FindName(name)
                if elem is not None:
                    setattr(self, name, elem)
                    try:
                        self.RegisterName(name, elem)
                    except BaseException:
                        pass
            except BaseException:
                pass

        # Move content to self
        try:
            content = loaded_win.Content
            loaded_win.Content = None
            self.Content = content
        except BaseException:
            pass

        # Bind event handlers dynamically
        for elem_name, event_name, handler_name in event_bindings:
            try:
                if elem_name == '__root__':
                    ctrl = self
                else:
                    ctrl = getattr(self, elem_name, None)
                    if ctrl is None:
                        ctrl = loaded_win.FindName(elem_name) or self.FindName(elem_name)
                if ctrl is None:
                    continue
                handler = getattr(self, handler_name, None)
                if handler is not None and callable(handler):
                    evt = getattr(ctrl, event_name, None)
                    if evt is not None:
                        evt += handler
            except BaseException:
                pass

        # Wire title bar drag if an element named 'title_bar' or similar exists
        for tb_name in ('title_bar', 'titlebar', 'border_titlebar', 'TitleBar'):
            tb = getattr(self, tb_name, None)
            if tb is not None:
                try:
                    tb.MouseLeftButtonDown += self._on_title_bar_mouse_down
                except BaseException:
                    pass

        # Auto-load logo if logo_image exists or set window Icon
        self._auto_load_logo()

        # Auto-wire window chrome controls (minimize, maximize, close)
        self._wire_window_controls()

    def _wire_window_controls(self):
        """Auto-wires and standardizes window chrome buttons (minimize, maximize, close)."""
        try:
            from System.Windows import Visibility, ResizeMode, WindowState

            # 1. Close Button
            close_btn = None
            for name in ('btn_close', 'btn_close_chrome', 'btn_close_x',
                         'button_close', 'cancel_btn_top', 'btnCancelTop'):
                close_btn = getattr(self, name, None) or (self.FindName(name) if hasattr(self, 'FindName') else None)
                if close_btn is not None:
                    break

            if close_btn is not None:
                try:
                    close_btn.Click -= self.close_button_clicked
                except BaseException:
                    pass
                try:
                    close_btn.Click += self.close_button_clicked
                    close_btn.IsCancel = True
                    if not getattr(close_btn, 'ToolTip', None):
                        close_btn.ToolTip = "Close"
                except BaseException:
                    pass

            # 2. Minimize Button
            min_btn = None
            for name in ('btn_minimize', 'btn_min'):
                min_btn = getattr(self, name, None) or (self.FindName(name) if hasattr(self, 'FindName') else None)
                if min_btn is not None:
                    break

            if min_btn is not None:
                try:
                    min_btn.Click -= self.minimize_button_clicked
                except BaseException:
                    pass
                try:
                    min_btn.Click += self.minimize_button_clicked
                    if not getattr(min_btn, 'ToolTip', None):
                        min_btn.ToolTip = "Minimize"
                except BaseException:
                    pass

            # 3. Maximize Button
            max_btn = None
            for name in ('btn_maximize', 'btn_max'):
                max_btn = getattr(self, name, None) or (self.FindName(name) if hasattr(self, 'FindName') else None)
                if max_btn is not None:
                    break

            if max_btn is not None:
                try:
                    if getattr(self, 'ResizeMode', None) == ResizeMode.NoResize:
                        max_btn.Visibility = Visibility.Collapsed
                    else:
                        try:
                            max_btn.Click -= self.maximize_button_clicked
                        except BaseException:
                            pass
                        max_btn.Click += self.maximize_button_clicked
                        if not getattr(max_btn, 'ToolTip', None):
                            max_btn.ToolTip = "Maximize"
                except BaseException:
                    pass

            # 4. StateChanged Listener for Maximize / Restore icon & tooltip
            try:
                self.StateChanged -= self._on_window_state_changed
            except BaseException:
                pass
            try:
                self.StateChanged += self._on_window_state_changed
            except BaseException:
                pass

            # Run once initially to sync state
            self._update_maximize_icon()
        except BaseException:
            pass

    def _on_window_state_changed(self, sender, e):
        self._update_maximize_icon()

    def _update_maximize_icon(self):
        try:
            from System.Windows import WindowState
            max_btn = None
            for name in ('btn_maximize', 'btn_max'):
                max_btn = getattr(self, name, None) or (self.FindName(name) if hasattr(self, 'FindName') else None)
                if max_btn is not None:
                    break
            if max_btn is None:
                return

            is_max = (self.WindowState == WindowState.Maximized)
            glyph = u"\uE923" if is_max else u"\uE922"   # E923 = ChromeRestore, E922 = ChromeMaximize
            tooltip = "Restore" if is_max else "Maximize"

            try:
                max_btn.ToolTip = tooltip
            except BaseException:
                pass

            # Update icon inside button if TextBlock is present
            tb = None
            content = getattr(max_btn, 'Content', None)
            if content is not None and hasattr(content, 'Text'):
                tb = content
            elif hasattr(max_btn, 'FindName'):
                try:
                    tb = max_btn.FindName('maximize_icon') or max_btn.FindName('btn_maximize_icon')
                except BaseException:
                    tb = None

            if tb is None:
                try:
                    from System.Windows.Media import VisualTreeHelper
                    count = VisualTreeHelper.GetChildrenCount(max_btn)
                    for i in range(count):
                        child = VisualTreeHelper.GetChild(max_btn, i)
                        if hasattr(child, 'Text'):
                            tb = child
                            break
                except BaseException:
                    pass

            if tb is not None:
                try:
                    tb.Text = glyph
                except BaseException:
                    pass
        except BaseException:
            pass

    def _auto_load_logo(self):
        """Auto-bind T3Lab logo to logo_image and window Icon if present."""
        return setup_window_logo(self)

    def setup_owner(self):
        """Sets host Revit application window as owner."""
        try:
            from pyrevit.api import AdWindows
            wih = WindowInteropHelper(self)
            wih.Owner = AdWindows.ComponentManager.ApplicationWindow
        except BaseException:
            pass

    def setup_default_handlers(self):
        """Binds Escape key to close window."""
        try:
            self.PreviewKeyDown += self._handle_esc_key
        except BaseException:
            pass
        self._wire_window_controls()

    def _handle_esc_key(self, sender, args):
        try:
            if args.Key == Key.Escape:
                self.Close()
        except Exception:
            pass

    def _on_title_bar_mouse_down(self, sender, e):
        try:
            from System.Windows.Input import MouseButtonState
            from System.Windows import ResizeMode
            if e.LeftButton == MouseButtonState.Pressed:
                if getattr(e, 'ClickCount', 1) == 2:
                    if getattr(self, 'ResizeMode', None) != ResizeMode.NoResize:
                        self.maximize_button_clicked(sender, e)
                        return
                self.DragMove()
        except Exception:
            pass

    # ── Standard Window Operations ─────────────────────────────────────────

    def show(self, modal=False):
        """Show window."""
        if modal:
            return self.ShowDialog()
        self.Show()

    def show_dialog(self):
        """Show modal window."""
        return self.ShowDialog()

    def hide(self):
        """Hide window."""
        self.Hide()

    def close(self):
        """Close window."""
        self.Close()

    # ── Common Event Handlers ──────────────────────────────────────────────

    def button_close(self, sender=None, e=None):
        self.Close()

    def close_button_clicked(self, sender=None, e=None):
        self.Close()

    def minimize_button_clicked(self, sender=None, e=None):
        self.WindowState = WindowState.Minimized

    def maximize_button_clicked(self, sender=None, e=None):
        if self.WindowState == WindowState.Maximized:
            self.WindowState = WindowState.Normal
        else:
            self.WindowState = WindowState.Maximized
        self._update_maximize_icon()

    # Aliases so any subclass / event wiring works consistently
    _minimize = minimize_button_clicked
    _maximize = maximize_button_clicked
    _on_minimize = minimize_button_clicked
    _on_maximize = maximize_button_clicked
    _close_chrome = close_button_clicked
    _on_close = close_button_clicked
    _close = close_button_clicked

    def Hyperlink_RequestNavigate(self, sender, e):
        try:
            Start(e.Uri.AbsoluteUri)
        except Exception:
            pass

    def _adopt_host_font(self):
        pass

    def _apply_theme(self):
        pass

    # ── Progress / Pause / Stop Support (ProgressPauseMixin built-in) ────────
    PP_PANEL      = "progress_panel"
    PP_BAR        = "pb_run"
    PP_PAUSE      = "btn_pause"
    PP_STOP       = "btn_stop"
    PP_STATUS     = "status_text"

    PP_PAUSE_LABEL  = u"⏸  Pause"
    PP_RESUME_LABEL = u"▶  Resume"
    PP_STOP_MSG     = u"Stopping… finishing current item"
    PP_PAUSED_MSG   = u"Paused — click Resume to continue"

    PP_PAUSE_ICON   = "btn_pause_icon"
    PP_PAUSE_TEXT   = "btn_pause_label"
    PP_PAUSE_GLYPH  = u"\uE769"   # MDL2 Pause
    PP_RESUME_GLYPH = u"\uE768"   # MDL2 Play
    PP_PAUSE_PLAIN  = u"Pause"
    PP_RESUME_PLAIN = u"Resume"

    def _pp_el(self, name):
        """XAML element lookup by x:Name; None when the window lacks it."""
        elem = getattr(self, name, None)
        if elem is not None:
            return elem
        try:
            return self.FindName(name)
        except Exception:
            return None

    def _pp_ensure_state(self):
        """Lazy flag init so handlers are safe even before begin_progress()."""
        if getattr(self, "_pause_requested", None) is None:
            self._pause_requested = False
        if getattr(self, "_cancel_requested", None) is None:
            self._cancel_requested = False
        if getattr(self, "_pp_disabled", None) is None:
            self._pp_disabled = []

    def _pp_show_paused(self, paused):
        """Reflect pause state on the Pause/Resume button."""
        try:
            icon  = self._pp_el(self.PP_PAUSE_ICON)
            label = self._pp_el(self.PP_PAUSE_TEXT)
            if icon is not None or label is not None:
                if icon is not None:
                    icon.Text = self.PP_RESUME_GLYPH if paused else self.PP_PAUSE_GLYPH
                if label is not None:
                    label.Text = self.PP_RESUME_PLAIN if paused else self.PP_PAUSE_PLAIN
            else:
                btn = self._pp_el(self.PP_PAUSE)
                if btn is not None:
                    btn.Content = self.PP_RESUME_LABEL if paused else self.PP_PAUSE_LABEL
        except Exception:
            pass

    def _pp_set_status(self, text):
        """Write to the window's status area."""
        upd = getattr(self, "_update_status", None)
        if callable(upd):
            try:
                upd(text)
                return
            except Exception:
                pass
        st = self._pp_el(self.PP_STATUS)
        if st is not None:
            try:
                st.Text = text
            except Exception:
                pass

    def _pp_dispatcher(self):
        """The WPF Dispatcher to pump."""
        disp = getattr(self, "Dispatcher", None)
        if disp is not None:
            return disp
        win = getattr(self, "window", None)
        return getattr(win, "Dispatcher", None) if win is not None else None

    def _do_events(self):
        """Pump the WPF dispatcher so the window repaints and buttons click."""
        try:
            disp = self._pp_dispatcher()
            if disp is None or DispatcherFrame is None or Action is None:
                return
            frame = DispatcherFrame()
            def _stop(f=frame):
                f.Continue = False
            priority = DispatcherPriority.Background if DispatcherPriority is not None else 4
            disp.BeginInvoke(priority, Action(_stop))
            disp.PushFrame(frame)
        except Exception:
            pass

    _pump_events = _do_events

    def _update_progress(self, value, maximum=None):
        """Set bar value (and Maximum), show the panel, pump events."""
        self._pp_ensure_state()
        try:
            bar = self._pp_el(self.PP_BAR)
            if bar is not None:
                if maximum is not None:
                    bar.Maximum = maximum
                bar.Value = value
            panel = self._pp_el(self.PP_PANEL)
            if panel is not None:
                panel.Visibility = Visibility.Visible
        except Exception:
            pass
        self._do_events()
        while self._pause_requested and not self._cancel_requested:
            self._do_events()

    def _hide_progress(self):
        """Hide the panel and reset flags + Pause/Stop button states."""
        self._pp_ensure_state()
        try:
            panel = self._pp_el(self.PP_PANEL)
            if panel is not None:
                panel.Visibility = Visibility.Collapsed
            bar = self._pp_el(self.PP_BAR)
            if bar is not None:
                bar.Value = 0
            self._cancel_requested = False
            self._pause_requested  = False
            self._pp_show_paused(False)
            btn_pause = self._pp_el(self.PP_PAUSE)
            if btn_pause is not None:
                btn_pause.IsEnabled = True
            btn_stop = self._pp_el(self.PP_STOP)
            if btn_stop is not None:
                btn_stop.IsEnabled = True
        except Exception:
            pass

    @property
    def is_cancelled(self):
        """True once the user pressed Stop. Reset by end_progress()."""
        self._pp_ensure_state()
        return self._cancel_requested

    def begin_progress(self, maximum=100, disable=None):
        """Reset flags, show the panel at 0/<maximum>, disable action controls."""
        self._pp_ensure_state()
        self._cancel_requested = False
        self._pause_requested  = False
        self._pp_disabled = []
        for ctrl in (disable or []):
            try:
                if ctrl.IsEnabled:
                    ctrl.IsEnabled = False
                    self._pp_disabled.append(ctrl)
            except Exception:
                pass
        self._update_progress(0, maximum)

    def step_progress(self, value, message=None):
        """Per-item update: bar + optional status message."""
        if message is not None:
            self._pp_set_status(message)
        self._update_progress(value)
        return not self._cancel_requested

    def end_progress(self):
        """Hide the panel and re-enable controls disabled by begin_progress()."""
        self._pp_ensure_state()
        for ctrl in self._pp_disabled:
            try:
                ctrl.IsEnabled = True
            except Exception:
                pass
        self._pp_disabled = []
        self._hide_progress()

    def stop_clicked(self, sender=None, e=None):
        """Click handler for btn_stop — cooperative cancel."""
        self._pp_ensure_state()
        self._cancel_requested = True
        self._pause_requested  = False
        try:
            btn_stop = self._pp_el(self.PP_STOP)
            if btn_stop is not None:
                btn_stop.IsEnabled = False
        except Exception:
            pass
        self._pp_set_status(self.PP_STOP_MSG)

    def pause_resume_clicked(self, sender=None, e=None):
        """Click handler for btn_pause — toggles pause/resume."""
        self._pp_ensure_state()
        if self._pause_requested:
            self._pause_requested = False
            self._pp_show_paused(False)
        else:
            self._pause_requested = True
            self._pp_show_paused(True)
            self._pp_set_status(self.PP_PAUSED_MSG)

    def to_items_source(self, items):
        """Convert a Python list/iterable into a .NET collection compatible with WPF ItemsSource."""
        return to_items_source(items)

    def set_items_source(self, control, items):
        """Safely assign items to control.ItemsSource under both CPython 3 and IronPython."""
        set_items_source(control, items)

    # ── Select-all ở header của cột checkbox ────────────────────────────
    # Mọi bảng có cột checkbox chọn dòng đều phải có checkbox select-all ở
    # header. Handler trong tool chỉ cần gọi 2 hàm dưới đây, không tự lặp.

    def toggle_all_rows(self, grid, prop, is_checked):
        """Bật/tắt `prop` trên MỌI dòng ĐANG HIỂN THỊ của `grid`.

        Dùng `grid.Items` chứ không phải `ItemsSource` để tôn trọng filter/sort
        đang áp: select-all phải chọn đúng những gì người dùng đang nhìn thấy,
        không chọn lén các dòng đã bị lọc đi.

        Trả về số dòng đã đổi.
        """
        if grid is None:
            return 0
        value = bool(is_checked)
        changed = 0
        try:
            rows = list(grid.Items)
        except Exception:
            return 0
        for row in rows:
            try:
                if getattr(row, prop) != value:
                    setattr(row, prop, value)
                    changed += 1
            except AttributeError:
                continue        # dòng placeholder "new item" của DataGrid
        try:
            grid.Items.Refresh()
        except Exception:
            pass
        return changed

    def sync_header_checkbox(self, header_cb, grid, prop):
        """Đồng bộ checkbox header theo trạng thái các dòng.

        Tất cả chọn → checked · không dòng nào chọn → unchecked ·
        chọn một phần → indeterminate (ô vuông đặc), để người dùng nhìn là biết
        đang ở trạng thái hỗn hợp thay vì đoán.
        """
        if header_cb is None or grid is None:
            return
        try:
            rows = list(grid.Items)
        except Exception:
            return
        flags = []
        for row in rows:
            try:
                flags.append(bool(getattr(row, prop)))
            except AttributeError:
                continue
        # IsThreeState phải là False: nó chỉ quyết định CÚ CLICK của người dùng
        # có đi qua trạng thái indeterminate hay không. Select-all mà click ba
        # nhịp là khó chịu — ta vẫn gán None được bằng code khi hỗn hợp.
        header_cb.IsThreeState = False
        if not flags:
            header_cb.IsChecked = False
        elif all(flags):
            header_cb.IsChecked = True
        elif not any(flags):
            header_cb.IsChecked = False
        else:
            header_cb.IsChecked = None      # indeterminate

    # ── AI Mode Helpers ──────────────────────────────────────────────────
    @property
    def ai_bridge(self):
        """Return the AIModeBridge singleton instance, or None if unavailable."""
        if not hasattr(self, '_ai_bridge_inst') or self._ai_bridge_inst is None:
            try:
                from Intelligence.ai_bridge import ai_bridge
                self._ai_bridge_inst = ai_bridge
            except Exception:
                self._ai_bridge_inst = None
        return self._ai_bridge_inst

    def is_ai_mode_active(self, tool_name=None):
        """True if AI mode is enabled and a provider is available."""
        b = self.ai_bridge
        return bool(b and b.is_ai_mode_active(tool_name))

    def get_ai_status_info(self):
        """Return active AI provider and model status info dict."""
        b = self.ai_bridge
        if not b:
            return {"available": False, "provider": "None", "model": "", "label": "AI Offline"}
        return b.get_active_provider_info()

    def run_ai_async(self, worker_func, on_done=None, on_error=None, **kwargs):
        """Run an AI query on a background thread and invoke callbacks on the UI thread.
        
        Supports:
        - 2-callback pattern: on_done(result) and on_error(exception)
        - 1-callback dual pattern: callback(result, error)
        - Keyword args: on_success, on_error
        """
        if on_done is None and 'on_success' in kwargs:
            on_done = kwargs['on_success']
        if on_error is None and 'on_error' in kwargs:
            on_error = kwargs['on_error']

        disp = self._pp_dispatcher()

        def _invoke_ui(fn):
            if disp and not disp.HasShutdownStarted and Action is not None:
                try:
                    disp.BeginInvoke(Action(fn))
                except Exception:
                    fn()
            else:
                fn()

        def _safe_on_done(res):
            if not on_done:
                return
            def _run():
                try:
                    on_done(res)
                except TypeError:
                    try:
                        on_done(res, None)
                    except Exception:
                        raise
            _invoke_ui(_run)

        def _safe_on_error(err):
            if on_error:
                def _run_err():
                    on_error(err)
                _invoke_ui(_run_err)
            elif on_done:
                def _run_dual():
                    try:
                        on_done(None, err)
                    except TypeError:
                        pass
                _invoke_ui(_run_dual)

        b = self.ai_bridge
        if b:
            return b.run_async(worker_func, _safe_on_done, _safe_on_error)
        else:
            def _fallback():
                try:
                    r = worker_func()
                    _safe_on_done(r)
                except Exception as ex:
                    _safe_on_error(ex)
            import threading
            t = threading.Thread(target=_fallback)
            t.daemon = True
            t.start()
            return t


class my_WPF(T3WPFWindow):
    """Legacy alias for backward compatibility."""
    pass


WPFWindow = T3WPFWindow


# ── Monkeypatch pyrevit.forms for CPython ─────────────────────────────────────
try:
    from pyrevit import forms
    if not getattr(forms, 'IRONPY', False):
        forms.WPFWindow = T3WPFWindow
    forms.to_items_source = to_items_source
    forms.set_items_source = set_items_source
except Exception:
    pass
