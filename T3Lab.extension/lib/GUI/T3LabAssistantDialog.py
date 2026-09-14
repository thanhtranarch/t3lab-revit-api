# -*- coding: utf-8 -*-
"""T3Lab Assistant Dialog — Multimodal AI Assistant for Autodesk Revit.

Author: Tran Tien Thanh
Mail: trantienthanh909@gmail.com
"""

__author__  = "Tran Tien Thanh"
__title__   = "T3Lab Assistant Dialog"

# IMPORT LIBRARIES
# ==================================================
import io
import os
import sys
# ─── CPython 3 & lib bootstrap ────────────────────────────────────────────────
for _env in ('APPDATA', 'PROGRAMDATA'):
    _base = os.environ.get(_env, '')
    if _base:
        for _clone in ('pyRevit-Master', 'pyRevit'):
            _ceng = os.path.join(_base, _clone, 'bin', 'cengines', 'CPY3123')
            if os.path.isdir(_ceng):
                for _d in (_ceng, os.path.join(_ceng, 'Lib')):
                    if hasattr(os, 'add_dll_directory'):
                        try:
                            os.add_dll_directory(_d)
                        except Exception:
                            pass
                for _p in (_ceng, os.path.join(_ceng, 'Lib'), os.path.join(_ceng, 'python312.zip')):
                    if os.path.exists(_p) and _p not in sys.path:
                        sys.path.insert(0, _p)

_cur = os.path.dirname(os.path.abspath(__file__))
while _cur and not os.path.exists(os.path.join(_cur, 'lib')):
    _parent = os.path.dirname(_cur)
    if _parent == _cur:
        break
    _cur = _parent
_lib_dir = os.path.join(_cur, 'lib')
if os.path.exists(_lib_dir) and _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

try:
    import _cpython_bootstrap
    _cpython_bootstrap.init_cpython_paths()
except Exception:
    pass
# ──────────────────────────────────────────────────────────────────────────────
import clr
import json
import re
import datetime

clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('System')
import System.Windows
from System.Windows import Visibility, WindowState, GridLength
from System.Windows.Media.Imaging import BitmapImage
from System import Uri, UriKind, Action
from System.Threading import Thread, ThreadStart, ApartmentState

from pyrevit import revit, forms, script
from GUI.WPF_Base import T3WPFWindow
from Autodesk.Revit import DB

# DEFINE VARIABLES
# ==================================================
logger = script.get_logger()
# NO script.get_output() here. It was assigned and never used (one reference in
# the whole file, and that one is the word "output" inside a comment), but the
# call itself materialises a pyRevit output window. This script runs in a
# PERSISTENT engine and logs from background worker threads, and pyRevit's
# Reload closes every output window from the UI thread — closing a WPF window
# owned by another thread throws InvalidOperationException and aborts Reload.
# One unused window is not worth that risk.

try:
    REVIT_VERSION = int(revit.doc.Application.VersionNumber)
except Exception:
    try:
        from pyrevit import HOST_APP
        REVIT_VERSION = int(HOST_APP.version)
    except Exception:
        REVIT_VERSION = 2023  # safe fallback

# ─── Lib path setup ───────────────────────────────────────────────────────────
# __file__ → .../T3LabAssistant.pushbutton/script.py
# extension_dir → .../T3Lab_Lite.extension
_gui_dir = os.path.dirname(__file__)
_lib_dir = os.path.dirname(_gui_dir)
extension_dir = os.path.dirname(_lib_dir)
lib_dir = os.path.join(extension_dir, 'lib')
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)

# ─── Theme: follow Revit's own UI theme ───────────────────────────────────────
# Every colour the chat surface paints comes from here rather than from a hex
# literal typed at the call site, so the window can re-skin itself when Revit
# flips between Light and Dark (Revit 2024+). See GUI/RevitTheme.py.
try:
    from GUI import RevitTheme as _theme

    def _tb(token):
        """SolidColorBrush for a theme token."""
        return _theme.brush(token)

    def _trgb(token):
        """(r, g, b) for a theme token."""
        return _theme.rgb(token)

    def _theme_color(token):
        """System.Windows.Media.Color for a theme token."""
        return _theme.color(token)

    def _bind_theme(element, prop_name, token):
        """Make `element.<prop_name>` FOLLOW a theme token instead of copying it.

        _tb() hands back a frozen brush, so anything painted with it keeps the
        colours it was born with. That is why a transcript already on screen
        stayed light after Revit switched to dark: RevitTheme.apply() rewrites
        the token resources and every XAML {DynamicResource} re-evaluates, but
        a brush that was assigned once is just a value and nothing re-runs.

        SetResourceReference is the code-behind equivalent of DynamicResource:
        the element registers against the resource key and repaints when the
        key's value changes. No re-render, so a message that is mid-stream is
        never touched — which is the reason the old code gave for not fixing
        this by rebuilding the transcript.

        Falls back to the plain assignment (today's exact behaviour) when the
        property cannot be resolved to a DependencyProperty, so the worst case
        is the colour it would have had anyway.
        """
        try:
            from System.Windows import DependencyProperty
            dp = DependencyProperty.FromName(prop_name, element.GetType())
            if dp is not None:
                element.SetResourceReference(dp, _theme.RESOURCE_PREFIX + token)
                return True
        except Exception:
            pass
        try:
            setattr(element, prop_name, _tb(token))
        except Exception:
            pass
        return False

    def _bind_fg(element, token):
        """Foreground follows `token`."""
        return _bind_theme(element, 'Foreground', token)

    def _bind_bg(element, token):
        """Background follows `token`."""
        return _bind_theme(element, 'Background', token)

    def _bind_border(element, token):
        """BorderBrush follows `token`."""
        return _bind_theme(element, 'BorderBrush', token)

    def _bind_stroke(element, token):
        """Stroke follows `token` (Path / Ellipse / Line icons)."""
        return _bind_theme(element, 'Stroke', token)
except Exception as _theme_ex:                       # pragma: no cover
    logger.warning("RevitTheme unavailable, using light defaults: {}".format(
        _theme_ex))
    _theme = None
    # Revit's light-theme greys — same values as RevitTheme._LIGHT, as RGB.
    # Keep the two in step: this table is what paints the window when the theme
    # module cannot be imported at all.
    _LIGHT_FALLBACK = {
        'AppBg': (245, 245, 245), 'ChatBg': (255, 255, 255),
        'ComposerBg': (255, 255, 255), 'CardBg': (255, 255, 255),
        'CardBorder': (222, 222, 222), 'Divider': (236, 236, 236),
        'PaneEdge': (222, 222, 222), 'UserBubbleBg': (219, 232, 246),
        'UserBubbleText': (0, 0, 0), 'BotText': (0, 0, 0),
        'Ink': (60, 60, 60), 'Muted': (90, 90, 90), 'Faint': (171, 171, 171),
        'Accent': (6, 150, 215), 'AccentSoft': (214, 233, 247),
        'Blue': (0, 110, 175), 'Success': (11, 122, 74), 'Danger': (196, 43, 28),
        'Amber': (245, 158, 11), 'IconFg': (145, 145, 145),
        'IconFgHover': (60, 60, 60), 'IconHoverBg': (234, 234, 234),
        'InputText': (60, 60, 60), 'InputCaret': (60, 60, 60),
        'CodeBg': (245, 245, 245), 'CodeFg': (28, 28, 28),
        'ScrollThumb': (193, 193, 193), 'SelectedBg': (227, 227, 227),
    }

    def _trgb(token):
        return _LIGHT_FALLBACK.get(token, _LIGHT_FALLBACK['Ink'])

    def _tb(token):
        from System.Windows.Media import SolidColorBrush, Color
        r, g, b = _trgb(token)
        return SolidColorBrush(Color.FromRgb(r, g, b))

    def _theme_color(token):
        from System.Windows.Media import Color
        r, g, b = _trgb(token)
        return Color.FromRgb(r, g, b)

    # Same surface as the themed branch above, so call sites never have to ask
    # which one is live. With no RevitTheme there is nothing to follow, so
    # these just assign the light-fallback colour once.
    def _bind_theme(element, prop_name, token):
        try:
            setattr(element, prop_name, _tb(token))
        except Exception:
            pass
        return False

    def _bind_fg(element, token):
        return _bind_theme(element, 'Foreground', token)

    def _bind_bg(element, token):
        return _bind_theme(element, 'Background', token)

    def _bind_border(element, token):
        return _bind_theme(element, 'BorderBrush', token)

    def _bind_stroke(element, token):
        return _bind_theme(element, 'Stroke', token)


# ─── Shared with the LLMs Setting dialog ──────────────────────────────────────
# Provider brand colours and the shell folder/file openers live in one place;
# the chat window's private copies of the openers lacked the .NET 8
# UseShellExecute fallback, so those buttons died silently under Revit 2025+.
try:
    from GUI.AssistantShared import (PROVIDER_COLORS as _SHARED_PROVIDER_COLORS,
                                     PROVIDER_GRAY as _SHARED_PROVIDER_GRAY,
                                     open_in_explorer as _open_in_explorer,
                                     open_file_with_shell as _open_file_shell,
                                     project_scope_lines as _project_scope_lines)
except Exception:
    _SHARED_PROVIDER_COLORS = {
        "claude": (217, 119, 87), "openai": (16, 163, 127),
        "deepseek": (37, 99, 235), "ollama": (59, 130, 246),
        "lmstudio": (124, 58, 237),
    }
    _SHARED_PROVIDER_GRAY = (161, 161, 170)

    def _open_in_explorer(path, create=True):
        return False

    def _open_file_shell(path):
        return False

    def _project_scope_lines(meta, doc_counts=None):
        return []

# ─── NLP module ───────────────────────────────────────────────────────────────
try:
    from Intelligence.t3lab_assistant import (parse_command, has_api_key, keyword_parse,
                                              learn_pattern, find_learned_match,
                                              has_local_llm, parse_command_local,
                                              get_local_model_name, parse_command_nlu,
                                              inject_discovered_tools,
                                              get_active_provider_name,
                                              get_provider_display_label,
                                              get_setup_guidance_message,
                                              _build_system_prompt,
                                              _RAG_SYSTEM_PREFIX)
    HAS_NLP = True
except Exception as e:
    logger.warning("Could not import t3lab_assistant: {}".format(e))
    HAS_NLP = False
    def learn_pattern(*a, **kw): pass
    def find_learned_match(*a, **kw): return None
    def has_local_llm(*a, **kw): return False
    def parse_command_local(*a, **kw): return None
    def get_local_model_name(*a, **kw): return None
    def parse_command_nlu(*a, **kw): return None
    def inject_discovered_tools(*a, **kw): pass
    def get_active_provider_name(*a, **kw): return "claude"
    def get_provider_display_label(*a, **kw): return "AI"
    def get_setup_guidance_message(viet=True):
        return (u"Chưa hiểu yêu cầu — bạn mô tả cụ thể hơn nhé." if viet
                else "I didn't understand — could you describe it more specifically?")
    def _build_system_prompt(revit_context=u""):
        from Intelligence.t3lab_agent import build_system_prompt
        return build_system_prompt(revit_context=revit_context)
    _RAG_SYSTEM_PREFIX = u""  # fallback: no RAG prefix if NLP module unavailable

# ─── Tool discovery module ────────────────────────────────────────────────────
try:
    from Services.tool_discovery import (discover_new_tools, get_registered_tools,
                                         make_generic_launcher, make_launcher_for)
    HAS_DISCOVERY = True
except Exception as e:
    logger.warning("Could not import tool_discovery: {}".format(e))
    HAS_DISCOVERY = False
    def discover_new_tools(): return []
    def get_registered_tools(): return []
    def make_generic_launcher(script_path, title, url=None): return lambda: False
    def make_launcher_for(entry): return lambda: False

# ─── Revit API context runner ─────────────────────────────────────────────────
# Tool scripts are written to run as Revit commands (ExternalEvent.Create,
# Transaction, ...). The assistant's WPF callbacks run while Revit is IDLE,
# which is NOT an API context — see Services/revit_context.py.
try:
    from Services.revit_context import (ensure_api_context, run_in_api_context)
    HAS_API_CONTEXT = True
except Exception as e:
    logger.warning("Could not import revit_context: {}".format(e))
    HAS_API_CONTEXT = False
    def ensure_api_context(): return False, u"revit_context unavailable"
    def run_in_api_context(func, on_done=None):
        try:
            res = func()
            if res is None:
                ok, err = True, u""
            elif isinstance(res, tuple):
                ok = bool(res[0]) if res else False
                err = res[1] if len(res) > 1 else u""
            else:
                ok, err = bool(res), u""
        except Exception as ex:
            ok, err = False, _exc_text(ex)
        if on_done:
            on_done(ok, err or u"")
        return 'inline'

# ─── Context Scout (BIM Context) ────────────────────────────────────────────────
try:
    from Selection.scout import ContextScout
    HAS_SCOUT = True
except Exception as e:
    logger.warning("Could not import ContextScout: {}".format(e))
    HAS_SCOUT = False
    class ContextScout:
        @staticmethod
        def get_context_summary_for_ai(): return ""

# ─── BatchOut executor (configure + direct export) ────────────────────────────
try:
    from Services.batchout_executor import configure_batchout_window, direct_export
    HAS_EXECUTOR = True
except Exception as e:
    logger.warning("Could not import batchout_executor: {}".format(e))
    HAS_EXECUTOR = False

# ─── RAG processor (PDF / image attachments) ──────────────────────────────────
try:
    from Intelligence.rag_processor import (is_supported, is_image, is_pdf,
                                           build_text_context, build_vision_content_blocks,
                                           has_images, summarize_attachments, SUPPORTED_EXTS)
    HAS_RAG = True
except Exception as e:
    logger.warning("Could not import rag_processor: {}".format(e))
    HAS_RAG = False
    def is_supported(p): return False
    def is_image(p): return False
    def is_pdf(p): return False
    def build_text_context(files): return ''
    def build_vision_content_blocks(text, files): return [{"type": "text", "text": text}]
    def has_images(files): return False
    def summarize_attachments(files): return ''
    SUPPORTED_EXTS = set()

# ─── Link reader (URLs / UNC / local paths pasted into the message) ───────────
try:
    from Intelligence import link_reader
    HAS_LINKS = True
except Exception as e:
    logger.warning("Could not import link_reader: {}".format(e))
    link_reader = None
    HAS_LINKS = False

# ─── Knowledge stack (RAG v2: index + retrieval) ──────────────────────────────
try:
    from Intelligence.knowledge.knowledge_store import (get_global_store,
                                                        get_active_store)
    HAS_KNOWLEDGE = True
except Exception as e:
    logger.warning("Could not import knowledge_store: {}".format(e))
    HAS_KNOWLEDGE = False
    def get_global_store(): return None
    def get_active_store(): return None

# ─── Routing decisions (pure, testable — see lib/Intelligence/routing.py) ─────
try:
    from Intelligence import routing
except Exception as e:
    logger.warning("Could not import routing: {}".format(e))

    class routing(object):
        """Degradation stub: keep the previous turn's behaviour, never carry
        over, and let learned patterns act as they did before."""
        @staticmethod
        def is_continuation(*a, **kw): return False

        @staticmethod
        def learned_pattern_wins(learned, nlu_result, raw=None): return bool(learned)

        @staticmethod
        def wants_spellcheck_fix(args): return False

# ─── Bilingual language analysis (VI/EN) ──────────────────────────────────────
# Feeds routing, the prompt hint and the reply-language decision. Degrading to
# the old diacritic check is always safe: every consumer takes a `default`.
try:
    from Intelligence.language import analyzer as _lang_analyzer
    from Intelligence.language import lang_detect as _lang_detect
    HAS_LANGUAGE = True
except Exception as e:
    logger.warning("Could not import language layer: {}".format(e))
    HAS_LANGUAGE = False
    _lang_analyzer = None
    _lang_detect = None

# ─── Graph agent layer (multi-goal turns) ─────────────────────────────────────
try:
    from Intelligence.graph.orchestrator import GraphOrchestrator
    HAS_GRAPH = True
except Exception as e:
    logger.warning("Could not import graph layer: {}".format(e))
    HAS_GRAPH = False
    GraphOrchestrator = None

# ─── Multi-agent dispatcher (specialist routing) ──────────────────────────────
try:
    from Intelligence.agents.dispatcher import AgentDispatcher
    HAS_AGENTS = True
except Exception as e:
    logger.warning("Could not import AgentDispatcher: {}".format(e))
    HAS_AGENTS = False
    class AgentDispatcher(object):
        def classify(self, *a, **kw):
            return {'specialist': 'general', 'skill': None,
                    'source': 'default', 'confidence': 0.0}

# ─── "Narrated instead of acted" guard (shared with the native AgentLoop) ─────
# Both prompt paths need the same rule, so the detector lives in agent_loop
# and the legacy JSON-intent loop below borrows it.
try:
    from Intelligence.agent_loop import (_announces_work,
                                         _ANNOUNCE_FIXUP)
except Exception as e:
    logger.warning("Could not import the announce guard: {}".format(e))
    _ANNOUNCE_FIXUP = u"Call the tool that answers the request now."

    def _announces_work(_text):
        return False

# ─── Specialist specs (per-agent prompt/tools/budget) ─────────────────────────
try:
    from Intelligence.agents.specialists import get_spec, build_specialist_prompt
    HAS_SPECIALISTS = True
except Exception as e:
    logger.warning("Could not import specialists: {}".format(e))
    HAS_SPECIALISTS = False
    def get_spec(name): return None
    def build_specialist_prompt(spec, revit_context='', **kw):
        from Intelligence.agent_loop import build_agent_system_prompt
        return build_agent_system_prompt(local=kw.get('local', False),
                                         lang=kw.get('lang', 'auto'))

# ─── Turn telemetry (latency + token/cache accounting) ────────────────────────
try:
    from Intelligence import telemetry
    HAS_TELEMETRY = True
except Exception as e:
    logger.warning("Could not import telemetry: {}".format(e))
    HAS_TELEMETRY = False

    class telemetry(object):
        """Degradation stub: measuring nothing must never break a turn."""
        @staticmethod
        def TurnTimer(*a, **kw): return None

        @staticmethod
        def record(turn, path=None): return False

        @staticmethod
        def prune(*a, **kw): return 0

# ─── Streaming message extractor (live token rendering) ───────────────────────
try:
    from Intelligence.llm_provider import StreamingJSONExtractor
except Exception as e:
    logger.warning("Could not import StreamingJSONExtractor: {}".format(e))
    class StreamingJSONExtractor(object):
        """Fallback: show whatever raw text streams in (no JSON unwrapping)."""
        def display(self, raw):
            return (raw or u"").strip()

# ─── Tool launchers ───────────────────────────────────────────────────────────
# Each function opens the corresponding T3Lab tool.

def _get_tool_script_dir(*parts):
    """Return the path to a pushbutton script.py given path parts relative to the tab.

    Usage:
        _get_tool_script_dir('Export.panel', 'BatchOut.pushbutton')
        _get_tool_script_dir('Annotation & Select.panel', 'Text.stack', 'DimText.pushbutton')
    """
    # __file__ = .../T3Lab_Lite.tab/AI Connection.panel/T3LabAssistant.pushbutton/script.py
    # dirname x1 = T3LabAssistant.pushbutton/
    # dirname x2 = AI Connection.panel/
    # dirname x3 = T3Lab_Lite.tab/
    tab_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(tab_dir, *parts + ('script.py',))


def _load_script(name, script_path):
    """Load a tool script as a module. Works in both CPython and IronPython."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(name, script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        pass
    try:
        import imp
        return imp.load_source(name, script_path)
    except Exception:
        pass
    return None


def _load_batchout_mod():
    """Load the BatchOut script module, raising RuntimeError on failure."""
    try:
        from GUI import BatchOutDialog
        return BatchOutDialog
    except Exception:
        pass
    script_path = _get_tool_script_dir('Views & Sheets.panel', 'BatchOut.pushbutton')
    mod = _load_script('batchout_script', script_path)
    if mod is None:
        raise RuntimeError("Could not load BatchOut module from: {}".format(script_path))
    return mod


def launch_batchout():
    """Open the BatchOut export dialog (no pre-configuration).

    Zero-arg launchers return (ok, error_text) — see TOOL_LAUNCHERS.
    """
    try:
        mod = _load_batchout_mod()
        window = mod.ExportManagerWindow()
        window.show(modal=not window.modeless)
        return True, u""
    except Exception as ex:
        logger.error("Error launching BatchOut: {}".format(_exc_text(ex)))
        return False, _exc_text(ex)


def launch_batchout_configured(config, progress_cb=None):
    """Open BatchOut pre-configured: sheets selected, format set, tab = Create.

    Args:
        config: dict with keys format, filter (from batchout_executor / NLP).
        progress_cb: optional callable(str) for status messages.
    Returns:
        bool success
    """
    try:
        mod = _load_batchout_mod()
        window = mod.ExportManagerWindow()

        if HAS_EXECUTOR:
            configure_batchout_window(window, config)
            fmt    = (config.get('format') or 'pdf').upper()
            filt   = config.get('filter') or ''
            filt_s = u" {} sheet".format(filt) if filt else u" all sheets"
            if progress_cb:
                progress_cb(u"BatchOut selected{}, format {} — press Export to run.".format(
                    filt_s, fmt))

        window.show(modal=not window.modeless)
        return True
    except Exception as ex:
        logger.error(u"Error launching configured BatchOut: {}".format(_exc_text(ex)))
        if progress_cb:
            progress_cb(u"Error: {}".format(_exc_text(ex)))
        return False


def launch_export_direct(config, progress_cb=None):
    """Export sheets directly without showing BatchOut UI.

    Args:
        config: dict with format, filter, folder (optional).
        progress_cb: optional callable(str) for chat progress updates.
    Returns:
        bool success
    """
    try:
        if not HAS_EXECUTOR:
            raise RuntimeError("batchout_executor not available")
        mod = _load_batchout_mod()
        ok, count, msg = direct_export(mod, config, progress_cb)
        return ok
    except Exception as ex:
        logger.error(u"Error in direct export: {}".format(_exc_text(ex)))
        if progress_cb:
            progress_cb(u"Export error: {}".format(_exc_text(ex)))
        return False


def launch_loadfamily():
    """Open the Family Manager on its Loader tab."""
    try:
        from GUI.ManaFamiDialog import show_family_manager
        show_family_manager(default_tab=0)
        return True, u""
    except Exception as ex:
        logger.error("Error launching LoadFamily: {}".format(_exc_text(ex)))
        return False, _exc_text(ex)


# NOTE — 2026-07-28. Seven hardcoded launchers (ParaSync, ProjectName,
# Workset, DimText, UpperAll, Reset Overrides, Beam) were removed here: every
# one pointed at a pushbutton directory that no longer exists, so they always
# failed, while still being advertised to the model. `open_grids` and
# `open_loadfamily_cloud` went with them — Grids.pushbutton never existed, and
# the "cloud" launcher was a byte-for-byte duplicate of launch_loadfamily
# (show_family_manager only has tabs 0/1, Loader and Management — no cloud
# tab). Everything except BatchOut and the Family Manager now comes from the
# tool_discovery registry, validated against disk. See _build_tool_launchers.


# Map intent → launcher function
_THINK_BLOCK_RE = re.compile(
    r'<(think|thinking|reasoning|reflection)>[\s\S]*?</\1\s*>', re.IGNORECASE)
_THINK_OPEN_RE = re.compile(
    r'<(think|thinking|reasoning|reflection)>[\s\S]*\Z', re.IGNORECASE)


def _hide_reasoning(text):
    """Hide model chain-of-thought from anything user-visible.

    Closed <think>...</think> blocks are removed; an unterminated opening
    tag (mid-stream) hides everything after it so reasoning never flashes
    in the live bubble. Providers strip the final text themselves — this
    guards the LIVE stream and any path that bypasses a provider strip.
    """
    if not text:
        return text
    out = _THINK_BLOCK_RE.sub('', text)
    out = _THINK_OPEN_RE.sub('', out)
    return out.lstrip()


def _exc_text(exc):
    """Exception message as unicode. Never raises.

    `str(exc)` is unsafe under IronPython 2.7: a Revit error carrying a
    non-ASCII message (a Vietnamese family name, a localised Windows string)
    makes it throw UnicodeEncodeError from INSIDE an error handler, which then
    killed the whole chat turn and left the user staring at nothing.
    """
    try:
        return u"{}".format(exc)
    except Exception:
        pass
    try:
        r = repr(exc)
        return r.decode('utf-8', 'replace') if isinstance(r, bytes) else u"{}".format(r)
    except Exception:
        return u"<unprintable error>"


def _json_safe(obj):
    """Recursively coerce byte strings inside `obj` to unicode. Never raises.

    Byte strings reach the tool loop from anywhere the .NET/OS side hands
    back a legacy code page — a localised Revit message, a Windows path, an
    exception text — and they are NOT UTF-8. That is the one input json's
    encoder cannot survive; see _json_text.
    """
    if isinstance(obj, bytes):
        try:
            return obj.decode('utf-8')
        except Exception:
            return obj.decode('latin-1', 'replace')
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[_json_safe(k)] = _json_safe(v)
        return out
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    return obj


def _json_text(obj, sort_keys=False):
    """`json.dumps` as unicode that can never kill a chat turn.

    IronPython 2.7's encoder decodes byte strings as UTF-8 (json/encoder.py
    line 47, reached whenever ensure_ascii is on). A byte string carrying
    Windows code-page bytes raises there — and on the tool-loop display path
    that exception escaped every handler up to _route_input, so the user got
    "'unknown' codec can't decode byte 0xe0 …" instead of an answer, mid-way
    through a turn that had already run tools. Sanitising first removes the
    only input the encoder cannot handle; the fallbacks cover the rest.
    """
    try:
        out = json.dumps(_json_safe(obj), ensure_ascii=False,
                         sort_keys=sort_keys)
        return out.decode('utf-8', 'replace') if isinstance(out, bytes) \
            else u"{}".format(out)
    except Exception:
        pass
    try:
        # Second rung: ASCII-escaped. Must be jsonsafe — `ensure_ascii=True`
        # here would raise on exactly the non-ASCII input that pushed us past
        # rung one, so the fallback could only ever fall through to the
        # "<unserializable>" string it is supposed to prevent.
        from core import jsonsafe
        return jsonsafe.dumps(_json_safe(obj), sort_keys=sort_keys,
                              default=_exc_text)
    except Exception:
        return u"<unserializable {}>".format(type(obj).__name__)


def _provider_usable(provider):
    """True when `provider` is worth calling for a real chat turn.

    Prefers is_configured() — a pure credential check — over check_health(),
    which hits the vendor's /models endpoint on every call. Falls back to
    check_health() for any provider object that predates is_configured().
    """
    if provider is None:
        return False
    try:
        probe = getattr(provider, "is_configured", None) or provider.check_health
        return bool(probe())
    except Exception:
        return False


def _get_uiapp():
    """The live UIApplication, or None.

    `HOST_APP.uiapp` is None on this pyRevit build: pyRevit only keeps a
    UIApplication when the `__revit__` builtin *is* one, and here `__revit__`
    is a DB.Application — so `HOST_APP.uiapp` (and `HOST_APP.uidoc`) stay None
    and every `HOST_APP.uiapp.X` call died with
    `AttributeError: 'NoneType' object has no attribute 'X'`.
    UIApplication has a public constructor taking a DB.Application, which is
    the supported way back to the UI layer from there.
    """
    try:
        from pyrevit import HOST_APP
        uiapp = getattr(HOST_APP, 'uiapp', None)
        if uiapp is not None:
            return uiapp
    except Exception:
        pass
    try:
        import builtins as __builtin__
        from Autodesk.Revit.UI import UIApplication
        rvt = getattr(__builtin__, '__revit__', None)
        if rvt is None:
            return None
        if isinstance(rvt, UIApplication):
            return rvt
        return UIApplication(rvt)
    except Exception as ex:
        logger.debug("_get_uiapp failed: {}".format(_exc_text(ex)))
        return None


def _get_uicontrolled_app():
    """Locate UIControlledApplication from pyRevit runtime if available."""
    try:
        import System
        for assm in System.AppDomain.CurrentDomain.GetAssemblies():
            try:
                name = assm.GetName().Name
                if name.startswith("pyRevitLoader"):
                    t = assm.GetType("PyRevitLoader.PyRevitLoaderApplication")
                    if t:
                        f = t.GetField(
                            "_uiControlledApplication",
                            System.Reflection.BindingFlags.Public
                            | System.Reflection.BindingFlags.NonPublic
                            | System.Reflection.BindingFlags.Static,
                        )
                        if f:
                            app = f.GetValue(None)
                            if app is not None and (type(app).__name__ == "UIControlledApplication" or (hasattr(app, 'ControlledApplication') and not hasattr(app, 'ActiveUIDocument'))):
                                return app
            except Exception:
                continue
    except Exception:
        pass

    try:
        from pyrevit import HOST_APP
        app = getattr(HOST_APP, 'uicontrolledapp', None)
        if app is not None and (type(app).__name__ == "UIControlledApplication" or (hasattr(app, 'ControlledApplication') and not hasattr(app, 'ActiveUIDocument'))):
            return app
    except Exception:
        pass

    return None


_VIET_CHARS = (u"àáâãèéêìíòóôõùúýăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợ"
               u"ụủứừửữựỳỵỷỹ")


def _reply_language():
    """User's language preference: 'auto' | 'vi' | 'en'. Never raises."""
    try:
        from config.settings import get_settings
        return get_settings().get_reply_language()
    except Exception:
        return 'auto'


def _is_viet_text(text):
    """True when the assistant should answer this turn in Vietnamese.

    Between 2026-07-18 and 2026-07-28 this was hard-wired to `return False`
    with the detection left unreachable below it. That did not make the UI
    English: the ~57 `if viet:` branches all took their English arm, but the
    Vietnamese strings that were written WITHOUT a branch (the /memory
    replies, the local-model download notices, the spell-check confirmation,
    every label in AssistantCards) still rendered — so the window mixed both
    languages. The setting now decides, and 'auto' detects from the text.
    """
    lang = _reply_language()
    if lang == 'vi':
        return True
    if lang == 'en':
        return False
    if not text:
        return _ui_viet()
    viet = _detect_viet(text, default=_ui_viet())
    if viet:
        # Remember it: messages that belong to no particular turn (startup
        # notices, card labels) should follow the language the user is
        # actually typing in.
        global _LAST_USER_VIET
        _LAST_USER_VIET = True
    return viet


def _detect_viet(text, default=True):
    """Language verdict for one message, with a fallback for the undecidable.

    The old test was `any(c in _VIET_CHARS for c in text)` — a single-line
    diacritic check that got the three commonest cases in a Vietnamese BIM
    office wrong: diacritic-less typing ("xuat pdf sheet A") read as English,
    code-switched sentences ("export cái sheet này") read by whichever half
    won, and a bare tool name ("BatchOut") flipped the whole session to
    English. `Intelligence.language` weighs evidence instead and returns
    `default` when there is none either way, so a bare tool name no longer
    changes anything.
    """
    if HAS_LANGUAGE:
        try:
            return _lang_detect.is_vietnamese(text, default=default)
        except Exception:
            pass
    return any(c in _VIET_CHARS for c in (text or u'').lower())


# Language of the most recent user message, for strings that are not tied to
# one turn. None until the user has typed something.
_LAST_USER_VIET = None


def _note_user_language(text):
    """Record the language of a user message. Call once per submitted turn.

    An undecidable message (a bare tool name, a number, "ok") leaves the
    session language ALONE rather than resetting it — that is the whole point
    of the `default` argument.
    """
    global _LAST_USER_VIET
    if text:
        _LAST_USER_VIET = _detect_viet(
            text, default=(True if _LAST_USER_VIET is None
                           else _LAST_USER_VIET))


def _relative_time(ts_text, viet=True):
    """'hôm nay 16:09' / '3 ngày trước' from a stored 'Y-m-d H:M:S' stamp.

    Returns u'' for anything unparseable — a history file written before
    timestamps existed, or one hand-edited — so callers can concatenate it
    without guarding. Never raises.
    """
    if not ts_text:
        return u""
    try:
        import datetime as _dt
        then = _dt.datetime.strptime(u"{}".format(ts_text).strip(),
                                     "%Y-%m-%d %H:%M:%S")
    except Exception:
        return u""
    try:
        now = _dt.datetime.now()
        days = (now.date() - then.date()).days
        clock = then.strftime("%H:%M")
        if days <= 0:
            return (u"hôm nay lúc {}" if viet else u"today at {}").format(clock)
        if days == 1:
            return (u"hôm qua lúc {}" if viet else u"yesterday at {}").format(clock)
        if days < 7:
            return (u"{} ngày trước" if viet else u"{} days ago").format(days)
        return then.strftime("%d/%m/%Y" if viet else "%d %b %Y")
    except Exception:
        return u""


def _time_greeting(hour=None, viet=None):
    """Time-of-day greeting phrase (no name). Never raises.

    Six buckets rather than the old three: Vietnamese distinguishes sáng sớm
    / sáng / trưa / chiều / tối / khuya, and "Good afternoon" at 11am or
    11pm reads as if nobody is home. English has no separate midday
    greeting, so 11–13 and 13–18 legitimately share one phrase — the split
    exists for the Vietnamese side.
    """
    try:
        if hour is None:
            import datetime
            hour = datetime.datetime.now().hour
        hour = int(hour)
        if viet is None:
            viet = _ui_viet()
    except Exception:
        return u"Xin chào" if viet else u"Hello"

    if hour < 5 or hour >= 22:
        return u"Khuya rồi" if viet else u"Working late"
    if hour < 7:
        return u"Dậy sớm nhỉ" if viet else u"Early start"
    if hour < 11:
        return u"Chào buổi sáng" if viet else u"Good morning"
    if hour < 13:
        return u"Chào buổi trưa" if viet else u"Good afternoon"
    if hour < 18:
        return u"Chào buổi chiều" if viet else u"Good afternoon"
    return u"Chào buổi tối" if viet else u"Good evening"


def _ui_viet():
    """Language for UI strings with no user text of their own."""
    lang = _reply_language()
    if lang == 'vi':
        return True
    if lang == 'en':
        return False
    return bool(_LAST_USER_VIET)


# Tools whose launcher needs arguments or a non-script entry point. Every
# OTHER intent is generated from the tool_discovery registry — see
# _register_discovered_launchers. Keeping this list to what genuinely cannot
# be auto-derived is the point: the previous hardcoded map drifted until 8 of
# its 10 intents pointed at pushbuttons that had been deleted or renamed.
_SPECIAL_LAUNCHERS = {
    "open_batchout":   launch_batchout,
    "open_loadfamily": launch_loadfamily,
}

TOOL_LAUNCHERS = dict(_SPECIAL_LAUNCHERS)

# intent → display title, for user-facing "Opening X..." messages.
TOOL_TITLES = {
    "open_batchout":   u"BatchOut",
    "open_loadfamily": u"Family Loader",
}

# Registry entries dropped because their script.py is not on disk. Surfaced in
# the activity log instead of silently shipping a broken intent to the model.
DROPPED_TOOLS = []


def _register_discovered_launchers(tools):
    """Rebuild TOOL_LAUNCHERS from the registry, validating each against disk.

    An intent only survives when its script.py actually exists — an intent the
    assistant cannot honour must never reach the model, because the model will
    happily promise to open it.

    Args:
        tools: list of tool dicts from discover_new_tools() / get_registered_tools()
    """
    del DROPPED_TOOLS[:]
    for tool in tools:
        intent = tool.get('intent')
        path   = tool.get('script_path')
        url    = tool.get('url')
        if not intent or intent in _SPECIAL_LAUNCHERS:
            continue
        # A urlbutton (Autodesk Forma / Health, Bluebeam Status) has no
        # script.py — its hyperlink IS its target.
        if not url and not (path and os.path.exists(path)):
            DROPPED_TOOLS.append((intent, path or u"<no path>"))
            TOOL_LAUNCHERS.pop(intent, None)
            TOOL_TITLES.pop(intent, None)
            continue
        title = tool.get('title') or intent
        TOOL_LAUNCHERS[intent] = make_launcher_for(tool)
        TOOL_TITLES[intent] = title

    # Inject all registered tools (new + old) into the NLP system prompt
    if HAS_NLP:
        try:
            inject_discovered_tools(get_registered_tools())
        except Exception:
            pass


def get_tool_title(intent):
    """Display label for an intent; falls back to a readable form of the id."""
    if intent in TOOL_TITLES:
        return TOOL_TITLES[intent]
    return (intent or u"").replace('open_', '').replace('_', ' ').strip().title()


# ─── Chat history persistence ─────────────────────────────────────────────────

LEGACY_DOC_KEY = "default"


def _get_doc_key():
    """Return a filesystem-safe key for the current Revit document.

    Resolved through Snippets._host.resolve_doc(), NOT `revit.doc`. In this
    modeless pane's engine `revit.doc` is a getattr chain that silently yields
    None, so the old code fell into `except` on every call and returned
    "default" — which is why every model on this machine shared ONE history
    file and switching projects never switched the conversation.
    """
    title = None
    try:
        from Snippets._host import resolve_doc
        doc, _err = resolve_doc()
        if doc is not None:
            title = doc.Title
    except Exception:
        pass
    if not title:
        return LEGACY_DOC_KEY
    # Strip chars that are invalid in filenames
    safe = re.sub(r'[\\/:*?"<>|]', '_', title)
    return safe[:80] or LEGACY_DOC_KEY   # cap at 80 chars


def _history_file(doc_key):
    """Return path to the JSON history file for doc_key.

    With an active project the history lives inside that project's
    workspace (projects/<pid>/chats/); otherwise the legacy per-document
    location is unchanged.
    """
    try:
        from config.project_store import ProjectStore
        _ps = ProjectStore()
        _pid = _ps.get_active_project_id()
        if _pid:
            return _ps.history_path(_pid, doc_key)
    except Exception:
        pass
    config_dir = os.path.join(lib_dir, 'config', 'chat_history')
    if not os.path.exists(config_dir):
        try:
            os.makedirs(config_dir)
        except Exception:
            pass
    return os.path.join(config_dir, '{}.json'.format(doc_key))


def save_chat_history(doc_key, messages, summary=u""):
    """Persist the last N messages to disk for this document.

    Args:
        doc_key  : identifier returned by _get_doc_key()
        messages : list of {role, content, ts} dicts
        summary  : rolling summary of turns that fell out of the window
    """
    try:
        from core import jsonsafe
        path = _history_file(doc_key)
        # Keep only the last 60 messages
        to_save = messages[-60:]
        # Serialize to an ASCII string FIRST, then write in one shot, so a
        # mid-write encode error can never truncate the file.
        #
        # This MUST go through jsonsafe, not json.dumps(ensure_ascii=True).
        # The old code used the latter and its comment claimed it was the fix
        # for Vietnamese chats — it was the cause. IronPython 2.7's ASCII
        # escaper raises on non-ASCII, the `except` below swallowed it, and so
        # the history file was never written at all the moment a conversation
        # contained Vietnamese. Every history on disk was English-only and
        # nobody saw an error. See core/jsonsafe.py.
        data = jsonsafe.dumps({"doc_key": doc_key, "messages": to_save,
                               "summary": summary or u""}, indent=2)
        with io.open(path, 'w', encoding='utf-8') as f:
            f.write(data)
    except Exception as ex:
        logger.debug(u"Could not save chat history: {}".format(_exc_text(ex)))


def _is_stale_tool_call_blob(role, content):
    """True for a saved assistant turn that is nothing but a tool call the
    model wrote as chat text, e.g.

        {"name": "apply_playbook", "parameters": {"playbook_name": "lod-standard"}}

    AgentLoop suppresses these now, but transcripts written before that fix
    still carry them — and they are worse than cosmetic: _restore_history
    replays them into _conversation_history, so every later turn sees a
    "successful" example of answering with a fake tool call and copies it.
    Dropped on load; the rest of the conversation is kept.
    """
    if role != "assistant":
        return False
    try:
        from Intelligence.agent_loop import is_bare_tool_call_text
        return is_bare_tool_call_text(content)
    except Exception:
        return False


def _history_path_for_read(doc_key):
    """Path to read this document's history from, or None.

    Falls back to the legacy shared "default" file so the conversations saved
    before _get_doc_key() started resolving a real document title are adopted
    by whichever model is open next, instead of appearing to vanish. Read-only
    fallback: the next saved message goes to the per-document file.
    """
    path = _history_file(doc_key)
    if os.path.exists(path):
        return path
    if doc_key != LEGACY_DOC_KEY:
        legacy = _history_file(LEGACY_DOC_KEY)
        if os.path.exists(legacy):
            return legacy
    return None


def load_chat_history(doc_key):
    """Load saved messages for doc_key.  Returns [] if none / error."""
    try:
        path = _history_path_for_read(doc_key)
        if not path:
            return []
        with io.open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return [m for m in data.get("messages", [])
                if not _is_stale_tool_call_blob(m.get("role", ""),
                                                m.get("content", ""))]
    except Exception as ex:
        logger.debug(u"Could not load chat history: {}".format(_exc_text(ex)))
        return []


def load_chat_summary(doc_key):
    """Rolling summary saved alongside the messages, or u''.

    A file written before summaries existed simply has no "summary" key, so
    older histories load with an empty one rather than failing.
    """
    try:
        path = _history_path_for_read(doc_key)
        if not path:
            return u""
        with io.open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get("summary") or u""
    except Exception as ex:
        logger.debug(u"Could not load chat summary: {}".format(_exc_text(ex)))
        return u""


def clear_chat_history(doc_key):
    """Delete the saved history file for doc_key."""
    try:
        path = _history_file(doc_key)
        if os.path.exists(path):
            os.remove(path)
    except Exception as ex:
        logger.debug(u"Could not clear chat history: {}".format(_exc_text(ex)))


# ─── Minimal icon vocabulary for bot chat messages ─────────────────────────────
# Segoe MDL2 Assets glyphs already verified elsewhere in this codebase (window
# chrome buttons, command palette, tool-card status) — reused here rather than
# guessing new codepoints, per the Lumina rule that chat icons must be minimal
# monochrome glyphs, never full-color detailed emoji (🔍🤖⚠️📎🔧👋🎉 etc.).
_ICON_INFO    = u""   # Info — neutral notices ("no AI configured", RAG note)
_ICON_SEARCH  = u""   # Zoom — tool discovery
_ICON_WARNING = u""   # Warning triangle
_ICON_SUCCESS = u""   # CheckMark
_ICON_SYNC    = u""   # Sync — "in progress" (matches tool-card running glyph)
_ICON_STOP    = u""   # Stop
_ICON_REFRESH = u""   # Refresh (chat cleared)
_ICON_ATTACH  = u""   # Attach (paperclip)
_ICON_ANALYZE = u""   # Analyze — fast-context "instant DB answer" badge
_ICON_LIST    = u""   # List/reference — stats & selection section headers

# Inline message-icon colours, read from the theme at import. The old fixed
# slate #64748B ignored the host theme entirely and turned near-invisible on a
# dark one.
_ICON_BLUE  = _trgb('Blue')      # info / discovery
_ICON_AMBER = _trgb('Amber')     # warning
_ICON_GREEN = _trgb('Success')   # success
_ICON_RED   = _trgb('Danger')    # "no / not available" markers
_ICON_SLATE = _trgb('Muted')     # neutral / muted


# ─── Persistent memory: explicit "remember ..." save trigger ───────────────────
# Only unambiguous save phrasings ("remember that X", "nhớ là X", "ghi nhớ X",
# "từ nay X") are handled deterministically — bare "nhớ"/"remember to" stay
# with the LLM, which can still save via the remember_fact tool when the
# statement really is durable. Both diacritic and folded Vietnamese forms are
# in the pattern so match positions stay valid on the ORIGINAL text.
_MEM_SAVE_RX = re.compile(
    r'^\s*(?:please\s+|h[aã]y\s+)?'
    r'(?:remember|ghi\s*nh[oớ]|nh[oớ]\s+r[aằ]ng|nh[oớ]\s+l[aà]'
    r'|t[uừ]\s+nay(?:\s+tr[oở]\s+[dđ]i)?|from\s+now\s+on)'
    r'\s*[:,]?\s+(.+)$',
    re.IGNORECASE | re.DOTALL | re.UNICODE)


# CLASS/FUNCTIONS
# ==================================================

class T3LabAssistantWindow(T3WPFWindow):
    """Standalone T3Lab Assistant chatbox window."""

    # Dynamic buttons added by _bootstrap_discovered_tools
    _DYNAMIC_BTNS = []   # list of Button WPF objects (not names)

    def __init__(self, is_docked=False):
        self.is_docked = is_docked
        try:
            xaml_path = os.path.join(_gui_dir, 'Tools', 'T3LabAssistant.xaml')
            T3WPFWindow.__init__(self, xaml_path)
        except Exception as ex:
            logger.error(u"Could not load T3LabAssistant XAML: {}".format(_exc_text(ex)))
            raise

        # Skin the window to the host BEFORE anything renders — theme brushes,
        # pane chrome, compact layout. Doing it later means one frame of the
        # wrong theme flashing inside the Revit pane.
        try:
            self._apply_revit_skin()
        except Exception as ex:
            logger.debug(u"_apply_revit_skin error: {}".format(_exc_text(ex)))

        self.doc = revit.doc

        # ── Session state ─────────────────────────────────────────────────────
        self._busy             = False          # concurrency guard
        self._switching_provider = False        # guard: _switch_provider bg probe in flight
        self._warming_up       = False          # guard: local-model warm-up in flight
        self._warmed_provider  = None           # last local provider we preloaded
        self._warmed_at        = 0.0            # timestamp of that warm-up
        self._snapshot_building = False         # guard: model-snapshot build in flight
        self._snapshot_built_for = None         # doc_key whose snapshot we ensured
        self._typing_row       = None           # reference to typing indicator element
        self._conversation_history = []         # [{role, content}, ...] multi-turn context
        self._history_summary  = u''            # turns folded out of the window
        self._last_raw         = ''             # last user input (for learning)
        self._last_result      = None           # last parsed result (for votes)
        self._doc_key          = _get_doc_key() # document identifier for history
        self._persisted_msgs   = []             # flat list with timestamps, for save/load
        self._attached_files   = []             # list of file paths (images / PDFs)
        # {provider_name: [model_list]} — filled ONLY by live probes against
        # each vendor's models endpoint after a connection succeeds. Never
        # pre-seeded with hardcoded names: the MODEL combo stays empty and
        # disabled (with a hint) until the provider actually reports which
        # models the account/server currently has.
        self._models_cache     = {}
        
        # ── History & Typing Animation State ──────────────────────────────────
        self._input_history    = []
        self._history_index    = -1
        self._current_input_temp = ""
        self._typing_timer     = None
        self._typing_elapsed   = 0

        # ── Live streaming bubble state ───────────────────────────────────────
        self._stream_row       = None           # Grid row of the live reply
        self._stream_tb        = None           # TextBlock being filled token-by-token
        self._stream_host      = None           # StackPanel holding body + action row

        # ── Claude-style message actions ──────────────────────────────────────
        # Set while a "Try again" is in flight: the answer it produces is filed
        # as another version of that row instead of appended as a new reply,
        # and the user's original message is not echoed a second time.
        self._retry_target_row   = None
        self._suppress_user_echo = False

        # ── Native agent loop state ───────────────────────────────────────────
        self._agent_loop        = None    # running AgentLoop (native tools path)
        self._cancel_requested  = False   # Stop pressed before the loop existed

        # ── Turn bookkeeping ──────────────────────────────────────────────────
        # _request_id is bumped by _set_busy(True). Every terminal handler
        # captures it at request start and no-ops if it no longer matches, so a
        # late worker and the Stop watchdog can never BOTH release the UI —
        # a double release fires _drain_queued_input twice and sends two
        # queued messages back to back.
        self._request_id  = 0
        self._replied     = False   # a terminal message was already posted
        self._tool_runs   = 0       # tools executed in the current turn

        # ── Easy-to-use input: multi-line with Shift+Enter ────────────────────
        try:
            from System.Windows.Controls import ScrollBarVisibility
            from System.Windows import TextWrapping
            self.chat_input.AcceptsReturn = False
            self.chat_input.TextWrapping  = TextWrapping.Wrap
            self.chat_input.MaxHeight     = 120
            self.chat_input.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
            self.chat_input.ToolTip = (u"Type your request (English or Vietnamese)  •  "
                                       u"Enter to send, Shift+Enter for a new line")
        except Exception:
            pass

        # ── Claude-style composer state (slash-skills popup + chips) ──────────
        self._slash_open      = False   # skills popup visible?
        self._slash_items     = []      # filtered catalog metas shown in popup
        self._slash_rows      = []      # Border rows (for highlight swap)
        self._slash_sel       = 0       # highlighted index
        self._forced_skill_id = None    # skill forced via /slash for THIS message
        self._forced_skill_args = u""   # user text after the /slash id (pre-boilerplate)
        self._tool_group      = None    # collapsed tool-card group for THIS turn

        # Provider active before a project override was applied, so leaving the
        # project can put it back (a scoped override must be undoable).
        self._pre_project_provider = None

        # ── Usage-flow state: message queue + quick-reply chips ───────────────
        # The input stays ENABLED while a request runs (Claude-style): Enter
        # queues the next message, which auto-sends when the current request
        # releases the busy state. Quick-reply chips (confirm / retry /
        # continue) are one row at a time — replaced on every new send.
        self._queued_inputs  = []      # [{'text': unicode, 'row': element}]
        self._quickreply_row = None    # active chip row (or None)

        # Paint the project/model/action chips AFTER first render — their
        # first update constructs LLMRouter + reads project JSON, which does
        # not belong on the startup-critical path of the UI thread.
        def _init_chips():
            try:
                self._update_composer_chips()
                self._update_action_mode_chip()
            except Exception:
                pass
        try:
            from System.Windows.Threading import DispatcherPriority
            self.Dispatcher.BeginInvoke(DispatcherPriority.Background,
                                        Action(_init_chips))
        except Exception:
            _init_chips()
        # Popups must never linger when focus leaves the input / the window
        try:
            def _input_lost_focus(s, ev):
                self._close_skills_popup()
            self.chat_input.LostKeyboardFocus += _input_lost_focus

            def _win_deactivated(s, ev):
                self._close_skills_popup()
                try:
                    self.project_popup.IsOpen = False
                    self.model_popup.IsOpen = False
                except Exception:
                    pass
            self.Deactivated += _win_deactivated
        except Exception:
            pass

        # Per-project scheduled prompts (project panel → Scheduled): a 30s
        # UI-thread timer fires due daily tasks while the window is open.
        # No-op when no project is active or no tasks are defined.
        try:
            self._start_schedule_timer()
        except Exception:
            pass

        # ── Paste (Ctrl+V) + drag-drop files straight into the chat ──────────
        # Wired in code (not XAML) so pyRevit's XAML event wiring quirks for
        # Window-level events never break window loading.
        try:
            self.AllowDrop = True
            self.PreviewDragOver += self._file_drag_over
            self.PreviewDrop += self._file_drop
            self.chat_input.PreviewKeyDown += self._input_preview_keydown
        except Exception:
            pass


        # ── Logo ──────────────────────────────────────────────────────────────

        # ── Restore conversation from previous session ─────────────────────────
        self._restore_history()
        self._update_welcome_greeting()

        # ── Tool discovery (background, then inject chips into UI) ─────────────
        def _discover_and_update():
            # Unguarded, this threw unhandled on a .NET background thread when
            # the window was closed inside the 300ms sleep — every other worker
            # in this file wraps its dispatcher hop.
            try:
                import time; time.sleep(0.3)
                self.Dispatcher.Invoke(Action(self._bootstrap_discovered_tools))
            except Exception as ex:
                logger.debug("tool discovery skipped: {}".format(_exc_text(ex)))

        _dt = Thread(ThreadStart(_discover_and_update))
        _dt.IsBackground = True
        _dt.SetApartmentState(ApartmentState.STA)
        _dt.Start()

        # Restore window geometry and sidebar state from last session
        self._restore_window_state()

        # ── Register the MCP ExternalEvent on the UI thread ────────────────────
        # This MUST happen here, on Revit's main thread — ExternalEvent.Create
        # throws if called from the background startup probe below, which would
        # leave every model-editing MCP tool (create wall/floor/column, rename,
        # set_parameter, …) unable to open a transaction. Creating it up-front
        # here guarantees the server can marshal those tools onto the UI thread.
        try:
            from Services.mcp_service import MCPService as _MCPService
            _ok, _ee_err = _MCPService.ensure_external_event()
            if not _ok:
                logger.debug(u"MCP ExternalEvent init failed: {}".format(_exc_text(_ee_err)))
        except Exception as _ex:
            logger.debug(u"MCP ExternalEvent init error: {}".format(_exc_text(_ex)))

        # ── Register the tool-launch ExternalEvent, same constraint ───────────
        # Without it every ribbon tool the assistant opens runs in Revit's IDLE
        # context, where ExternalEvent.Create and Transaction are illegal —
        # BCF Reader / ManaLoca / BatchOut threw "Attempting to create an
        # ExternalEvent outside of a standard API execution" and never opened.
        try:
            _ok, _ctx_err = ensure_api_context()
            if not _ok:
                logger.debug(u"API context init failed: {}".format(_exc_text(_ctx_err)))
        except Exception as _ex:
            logger.debug(u"API context init error: {}".format(_exc_text(_ex)))

        # Update AI badge, pre-load models cache, and warm up router status in background
        def _bg_startup_probe():
            try:
                import time
                time.sleep(0.5)   # let window render first

                # ─── 1. Auto-start Revit MCP Server & File Watcher ───
                # In-process infrastructure: nothing is downloaded and nothing
                # outlives Revit, so this still runs unattended — but a failure
                # is no longer invisible. It used to vanish into logger.debug,
                # and every tool call would then fail for no stated reason.
                try:
                    from Services.mcp_service import MCPService
                    # Start MCP HTTP Server if stopped
                    srv_status = MCPService.server_status()
                    if not srv_status.get('running'):
                        MCPService.start_server()
                    # Start File Task Watcher if stopped
                    wat_status = MCPService.watcher_status()
                    if not wat_status.get('running'):
                        MCPService.start_watcher()
                except Exception as ex:
                    logger.debug(u"Auto-start MCP/watcher failed: {}".format(_exc_text(ex)))
                    self._log_activity(
                        u"MCP server/watcher did not start: {}".format(
                            _exc_text(ex)))

                # ─── 2. Local engine: OFFER, never start or download ───────
                # This used to spawn `ollama serve` with subprocess.Popen and
                # then POST /api/pull for qwen2.5:1.5b with a 600 s timeout —
                # launching a background process and pulling ~1 GB onto the
                # user's machine, with no consent, no cancel and every failure
                # swallowed. The knowledge-embeddings step below already held
                # the right line ("the ~270 MB pull is never triggered
                # silently from startup"); this now follows it.
                try:
                    self._offer_local_engine()
                except Exception as ex:
                    logger.debug(u"Local engine offer failed: {}".format(_exc_text(ex)))

                # ─── 3.4 Skills registry scan ───
                try:
                    from Intelligence.skills_engine import SkillsEngine
                    _n_skills = SkillsEngine().scan()
                    logger.debug("Skills scanned: {}".format(_n_skills))
                except Exception as ex:
                    logger.debug(u"Skills scan failed: {}".format(_exc_text(ex)))
                    self._log_activity(
                        u"Skills scan failed: {}".format(_exc_text(ex)))

                # ─── 3.5 Knowledge index: incremental scan + vectors ───
                # Scans %APPDATA%/T3LabAI/knowledge/ plus user dirs; only
                # changed files are re-extracted. Embeddings only when the
                # model is ALREADY installed — the ~270 MB pull is never
                # triggered silently from startup.
                try:
                    if HAS_KNOWLEDGE:
                        _store = get_active_store()
                        if _store is not None:
                            _scan_res = _store.scan()
                            if _scan_res.get('added') or _scan_res.get('updated'):
                                logger.debug(u"Knowledge scan: {}".format(_exc_text(_scan_res)))
                            try:
                                from Intelligence.knowledge.embeddings import (
                                    get_default_embedder)
                                _emb = get_default_embedder()
                                if _emb is not None and _emb.is_available():
                                    _store.embed_pending(_emb, budget_sec=90)
                            except Exception:
                                pass
                            self.Dispatcher.Invoke(
                                Action(self._update_knowledge_status))
                except Exception as ex:
                    logger.debug(u"Knowledge scan failed: {}".format(_exc_text(ex)))
                    self._log_activity(
                        u"Knowledge scan failed: {}".format(_exc_text(ex)))

                from Intelligence.llm_router import LLMRouter
                router = LLMRouter()
                active = router.get_active_name()

                # Update the badge display instantly from router settings
                self.Dispatcher.Invoke(Action(self._update_ai_badge))

                # Step 1: Probe the active provider first to fill cache ASAP
                try:
                    router.probe_provider(active)
                    provider = router.get_provider(active)
                    if provider:
                        self._models_cache[active] = provider.get_models()
                except Exception:
                    pass

                # Step 2: Pre-load/cache models for all other providers in the background
                for name in router.get_provider_names():
                    if name == active:
                        continue
                    try:
                        p = router.get_provider(name)
                        if p and p.check_health():
                            self._models_cache[name] = p.get_models()
                    except Exception:
                        pass

                # Step 3: Warm up LLMRouter status cache
                try:
                    router.get_status(use_cache=False)
                except Exception:
                    pass

                # Step 4: Proactive setup nudge — only on a fresh chat (no saved
                # history for this document yet), only after first-run onboarding
                # has already been shown/dismissed (avoids duplicating that flow),
                # and only if auto-start above didn't already find a provider.
                try:
                    from config.user_profile import UserProfile
                    already_onboarded = not UserProfile().is_first_run()
                    fresh_chat = not self._persisted_msgs
                    no_provider = not has_api_key() and not has_local_llm()
                    if already_onboarded and fresh_chat and no_provider:
                        def _nudge():
                            _guide = get_setup_guidance_message(_ui_viet())
                            self._append_bot_message(_guide, icon=_ICON_INFO,
                                                     icon_color=_ICON_SLATE)
                            self._add_to_history("assistant", _guide)
                        self.Dispatcher.Invoke(Action(_nudge))
                except Exception:
                    pass
            except Exception:
                pass

        _t = Thread(ThreadStart(_bg_startup_probe))
        _t.IsBackground = True
        _t.SetApartmentState(ApartmentState.STA)
        _t.Start()

        # ── First-run onboarding (new installs only) ──────────────────────────
        try:
            from config.user_profile import UserProfile
            if UserProfile().is_first_run():
                self._show_onboarding()
        except Exception as ex:
            logger.debug(u"onboarding check error: {}".format(_exc_text(ex)))

        # Live Revit context strip (active view + selection) under the chat.
        # Started last so a host that refuses the read costs nothing during
        # window construction.
        try:
            self._update_revit_context()
            self._start_context_timer()
        except Exception as ex:
            logger.debug(u"revit context strip unavailable: {}".format(_exc_text(ex)))

        # Persist window geometry/sidebar on close (custom X button was removed)
        try:
            self.Closing += self._on_closing
        except Exception:
            pass

    def setup_icon(self):
        """Override pyRevit's setup_icon to remove the window icon from the title bar."""
        pass

    # ═══════════════════════════════════════════════════════════════════════
    # REVIT INTEGRATION — chrome, theme, live context
    # The assistant is not a guest application that happens to float over
    # Revit; hosted in the dockable pane it should read as one of Revit's own
    # panels. That means three things: no floating-card chrome inside the
    # pane, colours that follow the host's Light/Dark theme, and a layout that
    # survives a 320px-wide dock.
    # ═══════════════════════════════════════════════════════════════════════

    def _apply_revit_skin(self):
        """Make the window sit inside Revit instead of on top of it."""
        self._current_skin = None
        self._sync_theme(force=True)

        if self.is_docked:
            self._flatten_chrome_for_pane()

        # Revit's theme can change while the pane is open (Options ▸ User
        # Interface, or the BG Theme tool in this very extension), so re-read
        # it whenever the window is activated rather than only at startup.
        try:
            self.Activated += self._on_window_activated
        except Exception:
            pass

        try:
            self.SizeChanged += self._on_size_changed
            if hasattr(self, 'root_chrome') and self.root_chrome is not None:
                self.root_chrome.SizeChanged += self._on_size_changed
        except Exception:
            pass
        self._compact = None
        try:
            _initial_w = self.root_chrome.ActualWidth if (hasattr(self, 'root_chrome') and self.root_chrome.ActualWidth > 0) else (self.ActualWidth or self.Width)
            self._apply_compact_layout(_initial_w)
        except Exception:
            pass

    def _flatten_chrome_for_pane(self):
        """Strip the floating-card chrome so the pane is filled edge to edge.

        Floating, the window is a rounded card with a hairline edge. Docked,
        that same card sits *inside* Revit's pane with the host background
        showing through its 22px corners — the single strongest visual cue
        that the assistant is a foreign window. Docked mode squares it off.
        """
        try:
            from System.Windows import CornerRadius, Thickness
            self.root_chrome.CornerRadius    = CornerRadius(0)
            self.root_chrome.BorderThickness = Thickness(0)
        except Exception as ex:
            logger.debug(u"_flatten_chrome_for_pane: {}".format(_exc_text(ex)))

        # The floating min/max cluster belongs to a window, not to a pane —
        # Revit already provides the pane's own close/undock affordances.
        try:
            self.float_ctrls_panel.Visibility = Visibility.Collapsed
        except Exception:
            pass

    def _theme_scopes(self):
        """Every element that must carry its own copy of the T3Theme* brushes.

        Docked, AssistantPaneControl hands the content to Revit and detaches
        it from this window:

            content = win.Content
            win.Content = None          # <- root_chrome loses the Window
            data.FrameworkElement = content

        Window.Resources is where RevitTheme.apply() writes the tokens, so
        after that detach it is no longer an ancestor resource scope of the
        tree that binds to it. Every {DynamicResource T3Theme*} in the docked
        pane then resolves to nothing and the property falls back to its
        default — Background to null — leaving the pane transparent over
        Revit's black HwndSource. That is the "pane went dark while Revit is
        light" report: not a theme misdetection, a lookup that stops
        resolving.

        Writing the tokens onto the content root as well keeps them with the
        content wherever it is reparented. Floating, both scopes are alive and
        identical (RevitTheme caches one frozen brush per token per theme, so
        the second copy is ~30 dictionary entries pointing at the same
        objects); root_chrome is the nearer scope and simply wins.
        """
        scopes = []
        root = getattr(self, 'root_chrome', None)
        if root is not None:
            scopes.append(root)
        scopes.append(self)     # the Window's own Background binds too
        return scopes

    def _sync_theme(self, force=False):
        """Repaint the shell in Revit's current theme (Light / Dark).

        Everything bound with DynamicResource — window, chat surface, composer,
        popups, scrollbars, chips — follows immediately. Message bodies already
        on screen keep the colours they were rendered with; they pick up the
        new theme as new messages arrive, or in full on the next New
        conversation / pane reopen. Rebuilding a live transcript mid-flight
        would mean replaying rows while a stream may be writing into one.
        """
        if _theme is None:
            return None
        try:
            theme = _theme.current_theme()
            if theme == getattr(self, '_current_skin', None) and not force:
                return theme
            self._current_skin = theme
            for scope in self._theme_scopes():
                try:
                    _theme.apply(scope, theme)
                except Exception:
                    continue
            return theme
        except Exception as ex:
            logger.debug(u"_sync_theme error: {}".format(_exc_text(ex)))
            return None

    def _on_window_activated(self, sender, e):
        self._sync_theme()
        self._update_revit_context()
        # Preload the local model so the FIRST message isn't a cold VRAM load.
        # Throttled + backgrounded, and a cheap no-op for cloud providers.
        self._maybe_warm_up_local()
        # Ensure a cached open-model digest exists so answers are grounded in
        # the real model. Backgrounded, once per document per session.
        self._maybe_refresh_model_snapshot()

    def _maybe_warm_up_local(self, force=False):
        """Preload the active LOCAL model so the first message replies fast.

        Ollama/LM Studio only keep a model resident AFTER a first use, so the
        first message after the pane opens otherwise pays the multi-second load.
        No-op for cloud providers, throttled (keep_alive holds a model ~15m), and
        run entirely on a background thread so the UI is never blocked.
        """
        try:
            import time as _time
            name = get_active_provider_name()
            if name not in ("ollama", "lmstudio"):
                return
            now = _time.time()
            if not force and self._warmed_provider == name \
                    and (now - self._warmed_at) < 600:
                return
            if self._warming_up:
                return
            self._warming_up = True

            def _bg():
                try:
                    from Intelligence.llm_router import LLMRouter
                    provider = LLMRouter().get_active_provider()
                    if provider is not None and hasattr(provider, "warm_up"):
                        provider.warm_up()
                        self._warmed_provider = name
                        self._warmed_at = _time.time()
                except Exception:
                    pass
                finally:
                    self._warming_up = False

            t = Thread(ThreadStart(_bg))
            t.IsBackground = True
            t.SetApartmentState(ApartmentState.STA)
            t.Start()
        except Exception:
            self._warming_up = False

    def _model_snapshot_grounding(self):
        """Cached digest of the OPEN model (element counts / health / project) as
        a short grounding block. Injected with the live turn so answers — above
        all on a local model that cannot afford many read round-trips — start
        already knowing the model. Empty until a snapshot has been built for
        this document (self-study idle loop, or _maybe_refresh_model_snapshot)."""
        try:
            from Intelligence.learning.enrichers import model_snapshot
            digest = model_snapshot.load_digest(self._doc_key)
            return model_snapshot.digest_to_grounding_text(digest) or u""
        except Exception:
            return u""

    def _maybe_refresh_model_snapshot(self):
        """Build the open-model digest in the background if this document has none
        yet, so project grounding works without waiting for the self-study idle
        loop. Once per document per session; read-only tools, marshalled to
        Revit's thread by the enricher; never blocks the UI."""
        try:
            key = self._doc_key
            if self._snapshot_built_for == key or self._snapshot_building:
                return
            from Intelligence.learning.enrichers import model_snapshot
            if model_snapshot.load_digest(key).get('element_counts'):
                self._snapshot_built_for = key   # a usable snapshot already exists
                return
            self._snapshot_building = True

            def _bg():
                try:
                    model_snapshot.run(doc_key=key)
                    self._snapshot_built_for = key
                except Exception:
                    pass
                finally:
                    self._snapshot_building = False

            t = Thread(ThreadStart(_bg))
            t.IsBackground = True
            t.SetApartmentState(ApartmentState.STA)
            t.Start()
        except Exception:
            self._snapshot_building = False

    # ─── Compact layout for narrow docks ─────────────────────────────────────

    #: Below this width the pane is too narrow for the full composer furniture.
    COMPACT_WIDTH = 400

    def _on_size_changed(self, sender, e):
        try:
            if e.WidthChanged:
                self._apply_compact_layout(e.NewSize.Width)
                # Always, not just on the compact transition: dragging a
                # docked pane from 250px to 350px never crosses the threshold,
                # so _apply_compact_layout returns early and the popups would
                # stay clamped to the narrowest width they ever saw.
                self._fit_popups_to_width(e.NewSize.Width)
        except Exception:
            pass

    def _apply_compact_layout(self, width):
        """Tighten padding and drop optional footer text on a narrow dock.

        A pane docked beside the Project Browser is routinely 300–340px. At
        that width the 16px gutters plus the three-part footer leave the
        composer with barely half the pane, so both give way — the copyright
        stays (it is required), the disclaimer and the shortcut hint do not.
        """
        try:
            compact = bool(width) and width < self.COMPACT_WIDTH
        except Exception:
            return
        if compact == getattr(self, '_compact', None):
            return
        self._compact = compact

        from System.Windows import Thickness
        try:
            if compact:
                self.composer_bar.Padding = Thickness(9, 6, 9, 8)
                self.chat_scroll.Padding  = Thickness(10, 10, 5, 6)
            else:
                self.composer_bar.Padding = Thickness(16, 8, 16, 10)
                self.chat_scroll.Padding  = Thickness(16, 12, 10, 8)
        except Exception:
            pass

        vis = Visibility.Collapsed if compact else Visibility.Visible
        try:
            self.footer_disclaimer.Visibility = vis
        except Exception:
            pass

    # Popup widths are authored for a floating window; a pane docked beside the
    # Project Browser is routinely narrower than the skills popup's MinWidth
    # alone (340), so the popup hangs outside the pane over Revit's own UI.
    _POPUP_PANELS = ('skills_popup_panel', 'project_popup_panel',
                     'model_popup_panel', 'saved_prompts_list_panel')
    # Room for the popup's own border, margin and shadow.
    _POPUP_GUTTER = 24
    # The widest MinWidth authored in the XAML (skills_popup). Below this the
    # popups are pinned to the pane; at or above it they keep their own sizes.
    _POPUP_MIN_AUTHORED = 340

    def _fit_popups_to_width(self, width):
        """Clamp popup content to the pane. UI THREAD. Never raises.

        Widths are pinned on the popup's Border — the element that actually
        carries MinWidth/MaxWidth — so a popup shrinks with the pane instead
        of spilling out of it. Once the pane is wide enough again the authored
        widths are restored by clearing the local values.
        """
        from System.Windows import FrameworkElement
        from System.Windows.Controls import Border
        try:
            avail = float(width or 0) - self._POPUP_GUTTER
        except Exception:
            return
        if avail <= 0:
            return
        for name in self._POPUP_PANELS:
            try:
                node = getattr(self, name, None)
                # The panel may sit inside a ScrollViewer; walk up to the
                # Border that carries the width.
                while node is not None and not isinstance(node, Border):
                    node = getattr(node, 'Parent', None)
                if node is None:
                    continue
                if avail < self._POPUP_MIN_AUTHORED:
                    node.MinWidth = avail
                    node.MaxWidth = avail
                else:
                    node.ClearValue(FrameworkElement.MinWidthProperty)
                    node.ClearValue(FrameworkElement.MaxWidthProperty)
            except Exception:
                continue

    # ─── Live Revit context ──────────────────────────────────────────────────

    def _start_context_timer(self):
        """Poll the active view / selection so the strip stays live.

        Reads only — no transaction, no ExternalEvent — which is legal outside
        an API context. Three consecutive failures (closed document, a modal
        dialog holding the API, an unexpected host state) stop the timer for
        good rather than logging once per tick forever.
        """
        try:
            from System.Windows.Threading import DispatcherTimer
            from System import TimeSpan
            self._ctx_failures = 0
            self._ctx_timer = DispatcherTimer()
            self._ctx_timer.Interval = TimeSpan.FromSeconds(2)
            self._ctx_timer.Tick += self._on_context_tick
            self._ctx_timer.Start()
        except Exception as ex:
            logger.debug(u"context timer unavailable: {}".format(_exc_text(ex)))

    def _on_context_tick(self, sender, e):
        # Ask the element that is actually on screen. Docked, this window is
        # never shown — its content was detached into Revit's pane — so
        # self.IsVisible is permanently False and this tick used to return
        # early on every single fire, for the whole life of the pane.
        probe = getattr(self, 'root_chrome', None)
        if probe is None:
            probe = self
        try:
            if not probe.IsVisible:
                return          # docked pane hidden — nothing to refresh
        except Exception:
            pass
        # Revit's theme can change while the pane is open, and a docked pane
        # raises no Window.Activated — this tick is the only thing that would
        # notice. _sync_theme returns immediately unless the theme changed.
        self._sync_theme()
        if not self._update_revit_context():
            self._ctx_failures = getattr(self, '_ctx_failures', 0) + 1
            if self._ctx_failures >= 3:
                try:
                    self._ctx_timer.Stop()
                except Exception:
                    pass
        else:
            self._ctx_failures = 0

    def _read_revit_context(self):
        """(view_name, selection_count) for the active document, or None."""
        uiapp = _get_uiapp()
        if uiapp is None:
            return None
        uidoc = getattr(uiapp, 'ActiveUIDocument', None)
        if uidoc is None:
            return None
        doc = getattr(uidoc, 'Document', None)
        if doc is None:
            return None
        view_name = u""
        try:
            view = doc.ActiveView
            if view is not None:
                view_name = view.Name
        except Exception:
            pass
        count = 0
        try:
            count = uidoc.Selection.GetElementIds().Count
        except Exception:
            pass
        return (view_name, count)

    def _update_revit_context(self):
        """Refresh the context strip. Returns False if Revit could not be read."""
        try:
            ctx = self._read_revit_context()
        except Exception as ex:
            logger.debug(u"_read_revit_context: {}".format(_exc_text(ex)))
            ctx = None
        if ctx is None:
            try:
                self.revit_ctx_btn.Visibility = Visibility.Collapsed
            except Exception:
                pass
            return False

        view_name, count = ctx
        try:
            self.revit_ctx_view.Text = view_name or u"No active view"
            if count:
                self.revit_ctx_sel.Text = (u"· 1 selected" if count == 1
                                           else u"· {} selected".format(count))
            else:
                self.revit_ctx_sel.Text = u""
            self.revit_ctx_btn.Visibility = Visibility.Visible
        except Exception:
            return False
        return True

    def revit_ctx_clicked(self, sender, e):
        """Re-read Revit immediately instead of waiting for the next tick."""
        self._update_revit_context()

    # ─── Window state persistence ─────────────────────────────────────────────

    def _restore_window_state(self):
        """Restore window position and size from settings with strict boundary clamping."""
        if getattr(self, 'is_docked', False):
            return

        try:
            from config.settings import T3LabAISettings
            ws = T3LabAISettings().get_window_state()

            # Restore size — strictly clamp so assistant never opens full screen
            w = ws.get('width')
            h = ws.get('height')
            if w and h:
                w_f = float(w)
                h_f = float(h)
                self.Width = w_f if 480.0 <= w_f <= 720.0 else 560.0
                self.Height = h_f if 580.0 <= h_f <= 950.0 else 720.0
            else:
                self.Width  = 560.0
                self.Height = 720.0

            # Restore position — validate it is still on-screen; default to screen right
            left = ws.get('left')
            top  = ws.get('top')
            try:
                import System.Windows
                sw = System.Windows.SystemParameters.PrimaryScreenWidth
                sh = System.Windows.SystemParameters.PrimaryScreenHeight
                if left is not None and top is not None:
                    left_f = float(left)
                    top_f  = float(top)
                    if 0 <= left_f <= sw - 100 and 0 <= top_f <= sh - 60:
                        self.Left = left_f
                        self.Top  = top_f
                    else:
                        self.Left = max(40.0, sw - self.Width - 40.0)
                        self.Top  = max(40.0, (sh - self.Height) / 2.0)
                else:
                    self.Left = max(40.0, sw - self.Width - 40.0)
                    self.Top  = max(40.0, (sh - self.Height) / 2.0)
            except Exception:
                pass

        except Exception as ex:
            logger.debug(u"_restore_window_state error: {}".format(_exc_text(ex)))

    def _save_window_state(self):
        """Persist current window geometry to settings (skipped when docked or maximized)."""
        if getattr(self, 'is_docked', False):
            return

        try:
            from System.Windows import WindowState
            if getattr(self, 'WindowState', None) != WindowState.Normal:
                return

            from config.settings import T3LabAISettings
            w = max(480.0, min(float(self.Width), 720.0))
            h = max(580.0, min(float(self.Height), 950.0))
            T3LabAISettings().save_window_state(
                float(self.Left), float(self.Top),
                w, h,
                False,
            )
        except Exception as ex:
            logger.debug(u"_save_window_state error: {}".format(_exc_text(ex)))

    # ─── Window controls ──────────────────────────────────────────────────────

    def _on_closing(self, sender, e):
        """Persist window geometry/sidebar state when the window closes.

        Wired to the WPF Closing event in __init__. Previously this logic lived
        in close_clicked (the custom X button), which was removed — this keeps
        window-state saving alive for Alt+F4 / Revit pane close.
        """
        self._save_window_state()
        # Stop the scheduled-prompts timer: a DispatcherTimer keeps ticking
        # after the window closes (holding the window alive) and a due task
        # would run _process_input() on a closed window — an invisible LLM
        # request with results shown nowhere.
        try:
            self._sched_timer.Stop()
        except Exception:
            pass
        # Same reasoning for the typing-indicator timer: it ticks once a second
        # against a closed window and holds a strong reference to the whole
        # window tree (history, avatar bitmaps) for the rest of the Revit
        # session — reopening the assistant leaked another one.
        try:
            self._typing_timer.Stop()
        except Exception:
            pass
        # And the Revit context poller, for exactly the same reason: it reads
        # the active document every 2s and would keep doing so — against a
        # dead window — for the rest of the session.
        try:
            self._ctx_timer.Stop()
        except Exception:
            pass
        self._stop_stop_watchdog()

    def minimize_clicked(self, sender, e):
        self.WindowState = WindowState.Minimized

    def maximize_clicked(self, sender, e):
        """Toggle between standard width (560px) and wide width (720px).

        Prevents full-screen blowout while giving the user extra room when needed.
        """
        try:
            if self.Width > 640.0:
                self.Width = 560.0
            else:
                self.Width = 720.0
        except Exception:
            pass

    def close_clicked(self, sender, e):
        self.Close()

    def undo_clicked(self, sender, e):
        """Undo the last Revit transaction.

        Autodesk.Revit.DB.Document has neither CanUndo() nor Undo() — the old
        code raised AttributeError straight into a logger.debug, so the button
        did nothing at all and said nothing about it. Undo is a POSTABLE UI
        command: it must go through UIApplication.PostCommand, and PostCommand
        takes a RevitCommandId, not the PostableCommand enum value.

        The UIApplication must come from _get_uiapp(), NOT from
        `HOST_APP.uiapp` — that is None here, so the button always fell into
        the "Undo is unavailable" branch.
        """
        try:
            from Autodesk.Revit.UI import RevitCommandId, PostableCommand
            uiapp = _get_uiapp()
            if uiapp is None:
                raise AttributeError("no UIApplication available")
            cid = RevitCommandId.LookupPostableCommandId(PostableCommand.Undo)
            # CanPostCommand is the "can Revit take this right now" query:
            # False while a modal dialog or another command owns the UI.
            if not uiapp.CanPostCommand(cid):
                self._append_bot_message(
                    u"Revit is busy (a dialog or command is open) — "
                    u"close it and press Undo again.",
                    icon=_ICON_WARNING, icon_color=_ICON_AMBER)
                return
            uiapp.PostCommand(cid)
            # PostCommand is asynchronous — Revit runs it on the next input
            # cycle — so this must not claim the undo already happened. Revit
            # no-ops when the undo stack is empty, and CanPostCommand does not
            # report that, so the wording stays neutral.
            self._append_bot_message(u"Undo sent to Revit.")
        except Exception as ex:
            # Not _report_error: this is a standalone button, not a chat turn —
            # claiming the turn would suppress a running request's own reply.
            logger.error(u"undo: {}".format(_exc_text(ex)))
            self._append_bot_message(
                u"Undo is unavailable right now — use Ctrl+Z in Revit.",
                icon=_ICON_WARNING, icon_color=_ICON_AMBER)

    # ─── Tool discovery bootstrap ──────────────────────────────────────────────

    def _bootstrap_discovered_tools(self):
        """
        Run on startup (UI thread):
          1. Discover new tools → register launchers → update NLP prompt.
          2. Post a chat notification for truly NEW tools.
        Must be called from the UI thread (via Dispatcher.Invoke).
        """
        try:
            if not HAS_DISCOVERY:
                return

            # ── Discover (writes registry, drops vanished buttons) ───────────
            new_tools = discover_new_tools()

            # ── Register launchers + inject into NLP ─────────────────────────
            # ONE pass over the full registry: _register_discovered_launchers
            # rebuilds the catalog and validates every script path, so calling
            # it with just the new tools first would only reset the drop list.
            _register_discovered_launchers(get_registered_tools())

            # A registry entry whose script has since been deleted never
            # reaches the model — but it should not vanish silently either.
            if DROPPED_TOOLS:
                self._log_activity(
                    u"Tool catalog: skipped {} intent(s) with no script on "
                    u"disk — {}".format(
                        len(DROPPED_TOOLS),
                        u", ".join(i for i, _p in DROPPED_TOOLS[:8])))

            # ── Chat notification for NEW tools only ──────────────────────────
            if new_tools:
                names = u', '.join(t['title'] for t in new_tools[:5])
                if len(new_tools) > 5:
                    names += u'...'
                self._append_bot_message(
                    u"Discovered {} new tools: {}.\n"
                    u"I learned them and can open them from natural-language commands.".format(
                        len(new_tools), names),
                    icon=_ICON_SEARCH, icon_color=_ICON_BLUE
                )
        except Exception as ex:
            logger.debug(u"_bootstrap_discovered_tools error: {}".format(_exc_text(ex)))

    # ─── Old-context repeat guard ──────────────────────────────────────────────
    # Every (intent, args) that has COMPLETED in this conversation, newest last.
    # A small local model re-reads the transcript and re-issues work it already
    # did: one "tô đỏ tường" produced revit_override_color on Walls/red (right),
    # then Floors/blue and Doors/yellow (both lifted from earlier turns), and
    # then claimed only the walls had been coloured. The turn-local guard could
    # not see those — their arguments differ from anything in THIS turn — and
    # the old registry only held the single immediately-previous turn, so a
    # command from two turns back sailed through. Bounded so a long session
    # cannot grow it without limit.
    _MAX_REMEMBERED_CALLS = 60

    def _earlier_turn_calls(self):
        """Calls completed in earlier turns of this conversation."""
        return set(getattr(self, '_prev_turn_calls', None) or set())

    def _remember_turn_calls(self, calls):
        """Fold this turn's executed calls into the conversation-wide set."""
        try:
            known = getattr(self, '_prev_turn_calls', None)
            if not isinstance(known, set):
                known = set()
            order = getattr(self, '_prev_turn_call_order', None)
            if not isinstance(order, list):
                order = [k for k in known]
            for key in calls:
                if key not in known:
                    known.add(key)
                    order.append(key)
            # Drop the oldest beyond the cap: recent work is what the model is
            # most likely to replay, and an unbounded set would keep a command
            # blocked long after the user could plausibly mean it again.
            while len(order) > self._MAX_REMEMBERED_CALLS:
                known.discard(order.pop(0))
            self._prev_turn_calls = known
            self._prev_turn_call_order = order
        except Exception as ex:
            logger.debug(u"repeat-guard bookkeeping: {}".format(_exc_text(ex)))

    # ─── History persistence ───────────────────────────────────────────────────

    def _restore_history(self):
        """Load saved conversation from disk and replay bubbles + context.

        Order matters: the "restored" banner is emitted BEFORE the replayed
        turns and with no action row. It used to be appended last as a normal
        assistant reply, so after a Revit restart the pane looked like the
        assistant's most recent utterance was a notice about itself — complete
        with copy / try-again / thumbs buttons on a line it makes no sense to
        regenerate or rate.
        """
        try:
            # Restore the rolling summary first: the replay below re-adds
            # turns, and anything that falls back out of the window must fold
            # onto what was already condensed rather than starting over.
            self._history_summary = load_chat_summary(self._doc_key)
            saved = load_chat_history(self._doc_key)
            if not saved:
                return

            replay = saved[-30:]   # last 30 messages = 15 exchanges

            # Re-learn the conversation's language BEFORE anything is rendered.
            # _LAST_USER_VIET is process state, so a Revit restart reset a
            # Vietnamese session to the English default and the first reply
            # after reopening came back in the wrong language. Feed the
            # restored user turns through the normal detector, oldest first, so
            # the most recent decidable message wins.
            for msg in replay:
                if msg.get("role") == "user" and msg.get("content"):
                    _note_user_language(msg["content"])

            viet = _ui_viet()
            self._append_bot_message(self._restored_banner(saved, viet),
                                     icon=_ICON_REFRESH, icon_color=_ICON_SLATE,
                                     actions=False)

            # Replay the bubbles
            for msg in replay:
                role    = msg.get("role", "")
                content = msg.get("content", "")
                if not content:
                    continue
                if role == "user":
                    self._append_user_message(content)
                elif role == "assistant":
                    self._append_bot_message(content)
                # Re-populate NLP context (last 16 messages = 8 exchanges)
                self._conversation_history.append(
                    {"role": role, "content": content}
                )

            self._persisted_msgs = list(saved)
        except Exception as ex:
            logger.debug(u"Could not restore history: {}".format(_exc_text(ex)))

    def _restored_banner(self, saved, viet):
        """One line of context for a restored session: how much, how old.

        "Previous conversation restored" alone left the obvious question
        unanswered — restored from when? A session reopened after lunch and one
        reopened after three weeks looked identical, so stale context silently
        framed the next question.
        """
        exchanges = max(1, len([m for m in saved if m.get("role") == "user"]))
        when = _relative_time(
            (saved[-1] or {}).get("ts") if saved else None, viet)
        if viet:
            head = u"Đã khôi phục hội thoại trước ({} lượt{})".format(
                exchanges, u", " + when if when else u"")
            tail = u"Bấm nút hội thoại mới dưới ô nhập để bắt đầu lại từ đầu."
        else:
            head = u"Previous conversation restored ({} turn{}{})".format(
                exchanges, u"s" if exchanges != 1 else u"",
                u", " + when if when else u"")
            tail = u"Use the new-conversation button under the message box to start fresh."
        return head + u"\n" + tail

    def _persist_message(self, role, content):
        """Append one message to the in-memory list and save to disk."""
        try:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._persisted_msgs.append(
                {"role": role, "content": content, "ts": ts}
            )
            save_chat_history(self._doc_key, self._persisted_msgs,
                              summary=getattr(self, '_history_summary', u''))
        except Exception as ex:
            logger.debug(u"Could not persist message: {}".format(_exc_text(ex)))

    def reset_chat_clicked(self, sender, e):
        """Clear the chat history for this document and reset the UI."""
        try:
            # Resetting mid-request detached the live bubble while the worker
            # kept a reference to it: the answer landed in an orphaned
            # TextBlock (blank chat), and the stale _typing_row made
            # _show_typing_indicator early-return for the rest of the session.
            # _project_popup_select / _create_new_project already guard this way.
            if getattr(self, '_busy', False):
                self._append_bot_message(
                    u"Still working on the current request — press Stop first, "
                    u"then start a new conversation.",
                    icon=_ICON_WARNING, icon_color=_ICON_AMBER)
                return
            self._reset_session_state(clear_transcript=True)
            # Delete saved file
            clear_chat_history(self._doc_key)
            # No "Conversation refreshed" bubble — the welcome greeting panel
            # comes back on an empty transcript and already says the same thing.
            self._update_welcome_greeting()
        except Exception as ex:
            logger.debug(u"reset_chat error: {}".format(_exc_text(ex)))

    def new_chat_clicked(self, sender=None, e=None):
        """Start a new chat conversation. Archive current session if it has messages."""
        try:
            if getattr(self, '_busy', False):
                self._append_bot_message(
                    u"Still working on the current request — press Stop first, "
                    u"then start a new conversation.",
                    icon=_ICON_WARNING, icon_color=_ICON_AMBER)
                return

            # Archive current conversation if it contains messages
            if getattr(self, '_persisted_msgs', None):
                self._archive_current_session()

            # Reset session state and delete current document active history
            self._reset_session_state(clear_transcript=True)
            clear_chat_history(self._doc_key)

            # Update greeting & show suggestion prompt cards
            self._update_welcome_greeting()

            # Switch to chat tab
            self.tab_chat_clicked()
        except Exception as ex:
            logger.debug(u"new_chat_clicked error: {}".format(_exc_text(ex)))

    def _archive_current_session(self):
        """Archive current conversation to a historical session file."""
        try:
            if not getattr(self, '_persisted_msgs', None):
                return None
            from core import jsonsafe
            sessions_dir = os.path.join(lib_dir, 'config', 'chat_history', 'sessions')
            if not os.path.exists(sessions_dir):
                try:
                    os.makedirs(sessions_dir)
                except Exception:
                    pass

            title = u"Conversation"
            for m in self._persisted_msgs:
                if m.get("role") == "user" and m.get("content"):
                    c = m["content"].strip().replace('\n', ' ')
                    if len(c) > 40:
                        c = c[:37] + u"..."
                    title = c
                    break

            now = datetime.datetime.now()
            session_id = now.strftime("%Y%m%d_%H%M%S")
            ts = now.strftime("%Y-%m-%d %H:%M")
            session_data = {
                "id": session_id,
                "doc_key": self._doc_key,
                "title": title,
                "timestamp": ts,
                "message_count": len(self._persisted_msgs),
                "messages": list(self._persisted_msgs),
                "summary": getattr(self, '_history_summary', u'')
            }

            fname = "{}_{}.json".format(self._doc_key, session_id)
            fpath = os.path.join(sessions_dir, fname)
            data = jsonsafe.dumps(session_data, indent=2)
            with io.open(fpath, 'w', encoding='utf-8') as f:
                f.write(data)
            return fpath
        except Exception as ex:
            logger.debug(u"_archive_current_session error: {}".format(_exc_text(ex)))
            return None

    def tab_chat_clicked(self, sender=None, e=None):
        """Switch to Chat view."""
        try:
            from System.Windows import Visibility
            from System.Windows.Media import Brushes
            self.chat_view_grid.Visibility = Visibility.Visible
            self.history_view_grid.Visibility = Visibility.Collapsed

            try:
                self.tab_chat_border.Background = self.FindResource("T3.Surface")
                self.tab_chat_border.BorderBrush = self.FindResource("T3.Border")
                self.tab_chat_icon.Foreground = self.FindResource("T3.Ink")
                self.tab_history_border.Background = Brushes.Transparent
                self.tab_history_border.BorderBrush = Brushes.Transparent
                self.tab_history_icon.Foreground = self.FindResource("T3.TextSecondary")
            except Exception:
                pass

            try:
                self.chat_input.Focus()
            except Exception:
                pass
        except Exception as ex:
            logger.debug(u"tab_chat_clicked error: {}".format(_exc_text(ex)))

    def tab_history_clicked(self, sender=None, e=None):
        """Switch to History view and render past sessions."""
        try:
            from System.Windows import Visibility
            from System.Windows.Media import Brushes
            self.chat_view_grid.Visibility = Visibility.Collapsed
            self.history_view_grid.Visibility = Visibility.Visible

            try:
                self.tab_history_border.Background = self.FindResource("T3.Surface")
                self.tab_history_border.BorderBrush = self.FindResource("T3.Border")
                self.tab_history_icon.Foreground = self.FindResource("T3.Ink")
                self.tab_chat_border.Background = Brushes.Transparent
                self.tab_chat_border.BorderBrush = Brushes.Transparent
                self.tab_chat_icon.Foreground = self.FindResource("T3.TextSecondary")
            except Exception:
                pass

            self._render_history_sessions()
        except Exception as ex:
            logger.debug(u"tab_history_clicked error: {}".format(_exc_text(ex)))

    def suggestion_card_clicked(self, sender=None, e=None):
        """Handle click on one of the suggestion prompt cards."""
        try:
            tag = getattr(sender, 'Tag', None)
            if not tag:
                return
            prompt_text = str(tag).strip()
            if prompt_text:
                self.chat_input.Text = prompt_text
                self._process_input()
        except Exception as ex:
            logger.debug(u"suggestion_card_clicked error: {}".format(_exc_text(ex)))

    def _get_history_sessions(self):
        """Retrieve list of saved session metadata for this document."""
        sessions = []
        try:
            sessions_dir = os.path.join(lib_dir, 'config', 'chat_history', 'sessions')
            if not os.path.exists(sessions_dir):
                return []
            prefix = "{}_".format(self._doc_key)
            files = [f for f in os.listdir(sessions_dir) if f.endswith('.json')]
            files.sort(reverse=True)
            for f in files:
                fpath = os.path.join(sessions_dir, f)
                try:
                    with io.open(fpath, 'r', encoding='utf-8') as sf:
                        data = json.load(sf)
                    data['fpath'] = fpath
                    if data.get('doc_key') == self._doc_key or f.startswith(prefix):
                        sessions.append(data)
                except Exception:
                    pass
        except Exception as ex:
            logger.debug(u"_get_history_sessions error: {}".format(_exc_text(ex)))
        return sessions

    def _render_history_sessions(self):
        """Populate the history view with saved conversation cards."""
        try:
            from System.Windows import Visibility, Thickness, CornerRadius, GridUnitType, GridLength
            from System.Windows.Controls import Border, Grid, StackPanel, TextBlock, Button, ColumnDefinition, Orientation
            from System.Windows.Media import Brushes, FontFamily

            self.history_sessions_panel.Children.Clear()
            sessions = self._get_history_sessions()

            if not sessions:
                self.history_empty_state.Visibility = Visibility.Visible
                self.history_sessions_panel.Visibility = Visibility.Collapsed
                return

            self.history_empty_state.Visibility = Visibility.Collapsed
            self.history_sessions_panel.Visibility = Visibility.Visible

            for sess in sessions:
                fpath = sess.get('fpath')
                title_text = sess.get('title') or u"Conversation"
                meta_text = u"{}  •  {} messages".format(
                    sess.get('timestamp') or u"",
                    sess.get('message_count') or len(sess.get('messages', []))
                )

                card = Border()
                card.Background = self.FindResource("T3.Surface")
                card.BorderBrush = self.FindResource("T3.Border")
                card.BorderThickness = Thickness(1)
                card.CornerRadius = CornerRadius(6)
                card.Margin = Thickness(0, 0, 0, 8)
                card.Padding = Thickness(12, 8, 12, 8)

                grid = Grid()
                col0 = ColumnDefinition()
                col0.Width = GridLength(1.0, GridUnitType.Star)
                col1 = ColumnDefinition()
                col1.Width = GridLength.Auto
                grid.ColumnDefinitions.Add(col0)
                grid.ColumnDefinitions.Add(col1)

                info_stack = StackPanel()
                info_stack.VerticalAlignment = System.Windows.VerticalAlignment.Center
                Grid.SetColumn(info_stack, 0)

                tb_title = TextBlock()
                tb_title.Text = title_text
                tb_title.Style = self.FindResource("T3.BodyStrong")
                tb_title.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis

                tb_meta = TextBlock()
                tb_meta.Text = meta_text
                tb_meta.Style = self.FindResource("T3.Caption")
                tb_meta.Margin = Thickness(0, 4, 0, 0)

                info_stack.Children.Add(tb_title)
                info_stack.Children.Add(tb_meta)
                grid.Children.Add(info_stack)

                act_stack = StackPanel()
                act_stack.Orientation = Orientation.Horizontal
                act_stack.VerticalAlignment = System.Windows.VerticalAlignment.Center
                Grid.SetColumn(act_stack, 1)

                # Open / Resume button
                btn_open = Button()
                btn_open.Style = self.FindResource("T3.Button.Ghost")
                btn_open.Height = 28
                btn_open.Padding = Thickness(8, 0, 8, 0)
                btn_open.ToolTip = u"Resume this conversation"
                btn_open.Content = u"Resume"

                def _make_resume_handler(path):
                    def _handler(s, ev):
                        self._resume_history_session(path)
                    return _handler
                btn_open.Click += _make_resume_handler(fpath)
                act_stack.Children.Add(btn_open)

                # Delete button
                btn_del = Button()
                btn_del.Style = self.FindResource("T3.Button.Ghost")
                btn_del.Height = 28
                btn_del.Width = 28
                btn_del.Padding = Thickness(0)
                btn_del.Margin = Thickness(4, 0, 0, 0)
                btn_del.ToolTip = u"Delete this session"

                tb_del_icon = TextBlock()
                tb_del_icon.Text = u"\uE74D"
                tb_del_icon.FontFamily = FontFamily("Segoe MDL2 Assets")
                tb_del_icon.FontSize = 11.5
                tb_del_icon.Foreground = self.FindResource("T3.Danger.Text")
                btn_del.Content = tb_del_icon

                def _make_del_handler(path):
                    def _handler(s, ev):
                        try:
                            if path and os.path.exists(path):
                                os.remove(path)
                            self._render_history_sessions()
                        except Exception:
                            pass
                    return _handler
                btn_del.Click += _make_del_handler(fpath)
                act_stack.Children.Add(btn_del)

                grid.Children.Add(act_stack)
                card.Child = grid
                self.history_sessions_panel.Children.Add(card)
        except Exception as ex:
            logger.debug(u"_render_history_sessions error: {}".format(_exc_text(ex)))

    def _resume_history_session(self, path):
        """Load and resume an archived session into the current chat."""
        try:
            if not path or not os.path.exists(path):
                return
            with io.open(path, 'r', encoding='utf-8') as f:
                sess_data = json.load(f)

            if getattr(self, '_persisted_msgs', None):
                self._archive_current_session()

            self._reset_session_state(clear_transcript=True)

            msgs = sess_data.get("messages", [])
            summary = sess_data.get("summary", u"")

            self._persisted_msgs = list(msgs)
            self._history_summary = summary

            save_chat_history(self._doc_key, self._persisted_msgs, summary=self._history_summary)

            for m in msgs:
                role = m.get("role", "")
                content = m.get("content", "")
                if not content:
                    continue
                if role == "user":
                    self._append_user_message(content)
                elif role == "assistant":
                    self._append_bot_message(content)
                self._conversation_history.append({"role": role, "content": content})

            self._update_welcome_greeting()
            self.tab_chat_clicked()
        except Exception as ex:
            logger.debug(u"_resume_history_session error: {}".format(_exc_text(ex)))

    def clear_all_history_clicked(self, sender=None, e=None):
        """Delete all saved sessions for this document."""
        try:
            sessions = self._get_history_sessions()
            for s in sessions:
                fp = s.get('fpath')
                if fp and os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except Exception:
                        pass
            self._render_history_sessions()
        except Exception as ex:
            logger.debug(u"clear_all_history_clicked error: {}".format(_exc_text(ex)))

    # ─── Saved Prompts & Skills Management ────────────────────────────────────

    def saved_prompts_clicked(self, sender=None, e=None):
        """Toggle the Saved Prompts & Skills popup."""
        try:
            is_open = getattr(self.saved_prompts_popup, 'IsOpen', False)
            if is_open:
                self.saved_prompts_popup.IsOpen = False
            else:
                self._populate_saved_prompts_list("")
                self.saved_prompts_popup.IsOpen = True
                try:
                    self.saved_prompts_search.Text = ""
                    self.saved_prompts_search.Focus()
                except Exception:
                    pass
        except Exception as ex:
            logger.debug(u"saved_prompts_clicked error: {}".format(_exc_text(ex)))

    def close_saved_prompts_clicked(self, sender=None, e=None):
        """Close the Saved Prompts & Skills popup."""
        try:
            self.saved_prompts_popup.IsOpen = False
        except Exception:
            pass

    def saved_prompts_search_changed(self, sender=None, e=None):
        """Filter prompts/skills list as the user types."""
        try:
            q = (self.saved_prompts_search.Text or u"").strip().lower()
            self._populate_saved_prompts_list(q)
        except Exception as ex:
            logger.debug(u"saved_prompts_search_changed error: {}".format(_exc_text(ex)))

    def open_skills_folder_clicked(self, sender=None, e=None):
        """Open the skills folder in Windows Explorer to add or edit custom prompt .md files."""
        try:
            from Intelligence.skills_engine import _user_skills_dir
            udir = _user_skills_dir()
            if not os.path.exists(udir):
                try:
                    os.makedirs(udir)
                except Exception:
                    pass
            _open_in_explorer(udir)
        except Exception as ex:
            logger.debug(u"open_skills_folder_clicked error: {}".format(_exc_text(ex)))

    def manage_skills_clicked(self, sender=None, e=None):
        """Open the Skills management tab in the LLMs Setting dialog."""
        try:
            self.saved_prompts_popup.IsOpen = False
        except Exception:
            pass
        self._open_llm_settings(tab='skills')

    def _populate_saved_prompts_list(self, query=u""):
        """Build clickable rows for each saved prompt / skill. UI THREAD."""
        try:
            from System.Windows import Thickness, CornerRadius, GridUnitType, GridLength
            from System.Windows.Controls import Border, Grid, StackPanel, TextBlock, ColumnDefinition
            from System.Windows.Input import Cursors
            from Intelligence.skills_engine import get_skills_engine

            self.saved_prompts_list_panel.Children.Clear()
            engine = get_skills_engine()
            skills = engine.all_skills() or []

            # Filter by query
            if query:
                q = query.lower()
                filtered = []
                for s in skills:
                    name = (s.get('name') or u'').lower()
                    desc = (s.get('description') or u'').lower()
                    sid = (s.get('id') or u'').lower()
                    trigs = u" ".join(s.get('triggers') or []).lower()
                    if q in name or q in desc or q in sid or q in trigs:
                        filtered.append(s)
                skills = filtered

            if not skills:
                tb_none = TextBlock()
                tb_none.Text = u"No matching prompts or skills found."
                tb_none.Style = self.FindResource("T3.Caption")
                tb_none.Margin = Thickness(12, 16, 12, 16)
                tb_none.HorizontalAlignment = System.Windows.HorizontalAlignment.Center
                self.saved_prompts_list_panel.Children.Add(tb_none)
                return

            bg_normal = self.FindResource("T3.Surface")
            bg_hover = self.FindResource("T3.SurfaceSunken")
            br_normal = self.FindResource("T3.Border")
            br_hover = self.FindResource("T3.BorderStrong")

            for s in skills:
                sid = s.get('id') or u''
                name = s.get('name') or sid
                desc = s.get('description') or u''

                card = Border()
                card.Background = bg_normal
                card.BorderBrush = br_normal
                card.BorderThickness = Thickness(1)
                card.CornerRadius = CornerRadius(6)
                card.Margin = Thickness(0, 0, 0, 4)
                card.Padding = Thickness(10, 7, 10, 7)
                card.Cursor = Cursors.Hand
                card.ToolTip = u"Click to insert: /{}\n{}".format(sid, desc)

                # Hover states
                def _make_hover(target_card):
                    def _enter(sender, ev):
                        target_card.Background = bg_hover
                        target_card.BorderBrush = br_hover
                    def _leave(sender, ev):
                        target_card.Background = bg_normal
                        target_card.BorderBrush = br_normal
                    return _enter, _leave

                _ent, _lev = _make_hover(card)
                card.MouseEnter += _ent
                card.MouseLeave += _lev

                grid = Grid()
                col0 = ColumnDefinition()
                col0.Width = GridLength(1.0, GridUnitType.Star)
                col1 = ColumnDefinition()
                col1.Width = GridLength.Auto
                grid.ColumnDefinitions.Add(col0)
                grid.ColumnDefinitions.Add(col1)

                info_stack = StackPanel()
                info_stack.VerticalAlignment = System.Windows.VerticalAlignment.Center
                Grid.SetColumn(info_stack, 0)

                tb_name = TextBlock()
                tb_name.Text = name
                tb_name.Style = self.FindResource("T3.BodyStrong")
                tb_name.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
                info_stack.Children.Add(tb_name)

                if desc:
                    tb_desc = TextBlock()
                    tb_desc.Text = desc
                    tb_desc.Style = self.FindResource("T3.Caption")
                    tb_desc.Foreground = self.FindResource("T3.TextSecondary")
                    tb_desc.TextTrimming = System.Windows.TextTrimming.CharacterEllipsis
                    tb_desc.Margin = Thickness(0, 2, 0, 0)
                    info_stack.Children.Add(tb_desc)

                grid.Children.Add(info_stack)

                # Command badge on the right
                badge = Border()
                badge.Background = self.FindResource("T3.SurfaceSunken")
                badge.BorderBrush = self.FindResource("T3.Border")
                badge.BorderThickness = Thickness(1)
                badge.CornerRadius = CornerRadius(4)
                badge.Padding = Thickness(6, 2, 6, 2)
                badge.Margin = Thickness(8, 0, 0, 0)
                badge.VerticalAlignment = System.Windows.VerticalAlignment.Center
                Grid.SetColumn(badge, 1)

                tb_action = TextBlock()
                tb_action.Text = u"/{}".format(sid)
                tb_action.Style = self.FindResource("T3.Mono")
                tb_action.FontSize = 10.5
                tb_action.Foreground = self.FindResource("T3.TextMuted")
                tb_action.VerticalAlignment = System.Windows.VerticalAlignment.Center
                badge.Child = tb_action

                grid.Children.Add(badge)
                card.Child = grid

                def _make_pick_handler(skill_id):
                    def _h(sender_btn, ev):
                        try:
                            self.saved_prompts_popup.IsOpen = False
                            self.tab_chat_clicked()
                            self.chat_input.Text = u"/{} ".format(skill_id)
                            self.chat_input.CaretIndex = len(self.chat_input.Text)
                            self.chat_input.Focus()
                        except Exception as ex:
                            logger.debug(u"pick skill error: {}".format(_exc_text(ex)))
                    return _h

                card.MouseLeftButtonUp += _make_pick_handler(sid)
                self.saved_prompts_list_panel.Children.Add(card)
        except Exception as ex:
            logger.debug(u"_populate_saved_prompts_list error: {}".format(_exc_text(ex)))

    def _reset_session_state(self, clear_transcript=True):
        """Drop everything tied to the OLD conversation scope. UI THREAD.

        There were three "switch scope" paths — reset_chat_clicked,
        _activate_project and _create_new_project — and each reset a different
        subset of the same state. _activate_project in particular detached the
        chat rows without clearing `_stream_row` / `_typing_row`, which is the
        exact defect already fixed for reset_chat_clicked: the answer lands in
        an orphaned TextBlock and `_show_typing_indicator` early-returns for the
        rest of the session. One implementation, used by all three.
        """
        try:
            if clear_transcript:
                # Keep the welcome greeting panel (first child)
                while self.chat_history_panel.Children.Count > 1:
                    self.chat_history_panel.Children.RemoveAt(1)
        except Exception:
            pass
        # Rows detached above must not stay referenced
        try:
            self._clear_stream_refs()
        except Exception:
            pass
        self._typing_row        = None
        self._typing_text_block = None
        self._quickreply_row    = None
        self._queued_inputs     = []      # their chips were just removed
        # Conversation + agent carryover
        self._conversation_history = []
        self._history_summary      = u''   # a new conversation starts blank
        self._persisted_msgs       = []
        self._prev_agent_decision  = None  # specialist choice must not carry over
        self._prev_turn_calls      = set()  # repeat guard is per-conversation
        self._prev_turn_call_order = []     # insertion order, for the cap
        # Composer staging: attachments and a forced /skill belong to the old
        # scope; carrying them into a new project silently mis-files documents.
        try:
            self._attached_files = []
            self._refresh_attachment_panel()
        except Exception:
            pass
        self._forced_skill_id   = None
        self._forced_skill_args = u""

    # ─── AI badge & provider switcher ────────────────────────────────────────

    # Provider brand colors — shared with the LLMs Setting dialog so a provider
    # can never render one colour here and another there.
    _BADGE_COLORS = _SHARED_PROVIDER_COLORS
    _BADGE_GRAY = _SHARED_PROVIDER_GRAY   # #A1A1AA — no provider / offline

    def _render_greeting(self, name):
        """Set the welcome greeting text for a given name (no settings read)."""
        try:
            self.welcome_greeting_text.Text = u"{}, {}".format(
                _time_greeting(), name or u"Thạnh")
        except Exception:
            pass

    def _update_welcome_greeting(self):
        """Refresh greeting text from saved settings and toggle panel visibility."""
        try:
            from config.user_profile import UserProfile
            name = UserProfile().get_name() or u"Thạnh"
            self._render_greeting(name)

            # The welcome banner only shows on a fresh chat (no history yet).
            if self._persisted_msgs:
                self.welcome_greeting_panel.Visibility = Visibility.Collapsed
            else:
                self.welcome_greeting_panel.Visibility = Visibility.Visible
        except Exception:
            pass

    # ─── First-run onboarding ─────────────────────────────────────────────────

    def _selected_onboarding_provider(self):
        """Return the provider tag selected in the onboarding combo."""
        try:
            item = self.onboarding_provider_combo.SelectedItem
            if item is not None and item.Tag:
                return str(item.Tag)
        except Exception:
            pass
        return "claude"

    def _sync_onboarding_key_panel(self):
        """Hide the API-key field for local providers; relabel for remote ones."""
        try:
            prov = self._selected_onboarding_provider()
            is_local = prov in ("ollama", "lmstudio")
            self.onboarding_key_panel.Visibility = (
                Visibility.Collapsed if is_local else Visibility.Visible)
            if not is_local:
                labels = {
                    "claude":   u"ANTHROPIC API KEY",
                    "openai":   u"OPENAI API KEY",
                    "deepseek": u"DEEPSEEK API KEY",
                }
                self.onboarding_key_label.Text = labels.get(prov, u"API KEY")
        except Exception:
            pass

    def _show_onboarding(self):
        """Display the first-run onboarding card."""
        try:
            from config.user_profile import UserProfile
            prof = UserProfile()
            try:
                nm = prof.get_name(fallback=False)
                if nm:
                    self.onboarding_name_box.Text = nm
            except Exception:
                pass

            try:
                self.onboarding_greeting.Text = u"{}!".format(_time_greeting())
            except Exception:
                pass

            self._sync_onboarding_key_panel()
            self.onboarding_overlay.Visibility = Visibility.Visible
            try:
                self.onboarding_name_box.Focus()
            except Exception:
                pass
        except Exception as ex:
            logger.debug(u"_show_onboarding error: {}".format(_exc_text(ex)))

    def _hide_onboarding(self):
        try:
            self.onboarding_overlay.Visibility = Visibility.Collapsed
        except Exception:
            pass

    def onboarding_provider_changed(self, sender, e):
        self._sync_onboarding_key_panel()

    def onboarding_skip_clicked(self, sender, e):
        """Dismiss onboarding without saving — won't show again."""
        try:
            from config.user_profile import UserProfile
            UserProfile().mark_setup_completed()
        except Exception:
            pass
        self._hide_onboarding()

    def onboarding_save_clicked(self, sender, e):
        """Persist the new user's profile + model setup, then dismiss onboarding."""
        try:
            from System.Windows.Media import SolidColorBrush, Color
            name = (self.onboarding_name_box.Text or u"").strip()
            if not name:
                # Gently require a name.
                try:
                    self.onboarding_name_box.BorderBrush = SolidColorBrush(
                        Color.FromRgb(239, 68, 68))      # rose
                    self.onboarding_name_box.Focus()
                except Exception:
                    pass
                return

            from config.user_profile import UserProfile
            prof     = UserProfile()
            provider = self._selected_onboarding_provider()

            prof.set_name(name)
            prof.set_model_setup(provider)

            # Optional API key for remote providers.
            key = u""
            try:
                key = (self.onboarding_key_box.Text or u"").strip()
            except Exception:
                pass
            if key and provider in ("claude", "openai", "deepseek"):
                key_map = {"claude": "Claude", "openai": "OpenAI", "deepseek": "DeepSeek"}
                try:
                    from config.settings import T3LabAISettings
                    T3LabAISettings().set_api_key(key_map[provider], key)
                except Exception:
                    pass

            prof.mark_setup_completed()

            # Activate the chosen provider live.
            try:
                from Intelligence.llm_router import LLMRouter
                router = LLMRouter()
                router.switch_provider(provider)
                p = router.get_active_provider()
                if p and hasattr(p, "reload_credentials"):
                    p.reload_credentials()
            except Exception:
                pass

            self._hide_onboarding()

            # Refresh greeting + badge to reflect the new profile.
            self._render_greeting(name)
            try:
                self._update_welcome_greeting()
            except Exception:
                pass
            try:
                self._update_ai_badge()
            except Exception:
                pass

            self._append_bot_message(
                u"Nice to meet you, {}!\n"
                u"Your profile is saved. Try 'open batchout' or ask me anything about Revit.".format(name),
                icon=_ICON_SUCCESS, icon_color=_ICON_GREEN)
        except Exception as ex:
            logger.debug(u"onboarding_save_clicked error: {}".format(_exc_text(ex)))

    def _update_ai_badge(self):
        """Refresh the composer model chip (the old header pill was removed —
        the chip in the composer is now the single provider indicator)."""
        try:
            self._update_composer_chips()
        except Exception:
            pass

    def _switch_provider(self, name):
        """Hot-swap the active LLM provider — instant UI, network probes in background."""
        try:
            from Intelligence.llm_router import LLMRouter
            router = LLMRouter()
            ok = router.switch_provider(name)
            if not ok:
                return

            # Instant UI: composer chip renders from cached/saved data (no network)
            self.Dispatcher.Invoke(Action(self._update_ai_badge))

            # Background: probe the newly-active provider + refresh its model list.
            # Guarded so rapid repeated provider switches don't pile up threads.
            if self._switching_provider:
                return
            self._switching_provider = True

            def _bg():
                try:
                    provider = router.get_active_provider()
                    router.probe_provider(name)
                    if provider:
                        try:
                            self._models_cache[name] = provider.get_models()
                        except Exception:
                            pass
                        # Switched TO a local provider → preload its model now so
                        # the user's first message on it isn't a cold VRAM load.
                        if name in ("ollama", "lmstudio") and \
                                hasattr(provider, "warm_up"):
                            try:
                                import time as _time
                                provider.warm_up()
                                self._warmed_provider = name
                                self._warmed_at = _time.time()
                            except Exception:
                                pass
                except Exception:
                    pass
                finally:
                    self._switching_provider = False

            t = Thread(ThreadStart(_bg))
            t.IsBackground = True
            t.SetApartmentState(ApartmentState.STA)
            t.Start()
        except Exception as ex:
            logger.debug(u"_switch_provider error: {}".format(_exc_text(ex)))

    # ─── Settings (LLMs Setting hub dialog) ──────────────────────────────────

    def settings_btn_clicked(self, sender, e):
        """Open the LLMs Setting hub — provider, model, API key, projects,
        knowledge and skills all live there now (the in-window sidebar was
        removed)."""
        self._open_llm_settings()

    def _open_llm_settings(self, tab=None, select_pid=None):
        """Show the LLMs Setting dialog modally, then refresh anything it
        may have changed (provider/model chip, action mode, display name,
        projects).

        tab: optional tab key ('general'/'provider'/'projects'/'knowledge'/
        'skills') to open on — 'Customize project…' used to always land on
        General, hiding where projects are edited. select_pid preselects a
        project in the Projects tab (used right after New project so the
        user lands in the name box, not a generic dialog).
        """
        try:
            from GUI.LLMSettingDialog import LLMSettingWindow
            dlg = LLMSettingWindow()
            try:
                if select_pid:
                    dlg._load_projects_tab(select_pid=select_pid)
                if tab:
                    _rb = getattr(dlg, 'tab_' + tab, None)
                    if _rb is not None:
                        _rb.IsChecked = True
                if tab == 'projects':
                    def _focus_name(s, ev):
                        try:
                            dlg.project_name_box.Focus()
                            dlg.project_name_box.SelectAll()
                        except Exception:
                            pass
                    dlg.Loaded += _focus_name
            except Exception:
                pass
            _scope_before = self._active_pid()
            dlg.ShowDialog()
        except Exception as ex:
            logger.debug(u"_open_llm_settings error: {}".format(_exc_text(ex)))
            _scope_before = self._active_pid()
        self._refresh_after_settings(scope_before=_scope_before)

    @staticmethod
    def _active_pid():
        try:
            from config.project_store import ProjectStore
            return ProjectStore().get_active_project_id()
        except Exception:
            return None

    def _refresh_after_settings(self, scope_before=None):
        """Sync the chat window with state edited in the settings dialog."""
        # A project can be DELETED in the dialog, which clears active_project.
        # The transcript on screen still belongs to the old scope, so the next
        # _persist_message would have written it into the global history file.
        # Re-scope before anything else touches history.
        try:
            _pid_now = self._active_pid()
            if _pid_now != scope_before:
                self._reset_session_state(clear_transcript=True)
                self._restore_history()
                self._kick_knowledge_scan()
            # Either way the project's provider/model override may have been
            # edited. Re-activation is blocked for an unchanged pid, so without
            # this the edit had no effect until the user switched away and back.
            self._apply_project_provider(_pid_now)
        except Exception as ex:
            logger.debug(u"scope re-sync error: {}".format(_exc_text(ex)))
        try:
            self._update_composer_chips()
        except Exception:
            pass
        try:
            self._update_action_mode_chip()
        except Exception:
            pass
        try:
            self._update_welcome_greeting()
        except Exception:
            pass
        # The project panel overlay, if open, was rendered from pre-edit data.
        try:
            if (getattr(self, 'project_panel_overlay', None) is not None
                    and self.project_panel_overlay.Visibility == Visibility.Visible):
                _pid = self._active_pid()
                if _pid:
                    self._build_project_panel(_pid)
                else:
                    self.project_panel_overlay.Visibility = Visibility.Collapsed
        except Exception as ex:
            logger.debug(u"project panel refresh error: {}".format(_exc_text(ex)))

    # ─── Projects (workspaces) ────────────────────────────────────────────────

    def _project_prompt_blocks(self):
        """(project_instructions, memory_block) for the active project.

        The single source of project grounding for a turn. It exists because
        the two prompt-building paths had drifted apart: the native
        tool-calling path injected both blocks, while the legacy JSON-intent
        path injected NEITHER. The legacy path is not a rare fallback — it is
        taken whenever the user attaches a document or RAG returns a lot of
        context, i.e. exactly when working with project material, so a project's
        instructions and remembered conventions silently vanished at the moment
        they mattered most.

        Never raises; a failure just means an unscoped prompt.
        """
        instructions = u""
        memory = u""
        try:
            from config.project_store import ProjectStore
            ps = ProjectStore()
            instructions = ps.get_active_prompt_addendum() or u""
            try:
                from Intelligence import assistant_memory
                memory = assistant_memory.build_memory_block(
                    ps.get_active_project_id()) or u""
            except Exception:
                memory = u""
        except Exception:
            pass
        return instructions, memory

    def _apply_project_blocks(self, system_prompt):
        """Append project instructions + memory to a plain system prompt."""
        instructions, memory = self._project_prompt_blocks()
        if instructions:
            system_prompt += u"\n\n## Project instructions\n" + instructions
        if memory:
            system_prompt += u"\n\n" + memory
        return system_prompt

    def _apply_project_provider(self, pid):
        """Apply (or undo) a project's provider/model override.

        A project override is SCOPED: switch_provider(persist=False) keeps it
        out of settings.json, so the user's global default survives a restart.
        The missing half was the undo — leaving a project used to strand its
        provider for the rest of the session because nothing switched back.
        `_pre_project_provider` remembers what was active before the first
        override so it can be restored.
        """
        try:
            from Intelligence.llm_router import LLMRouter
            router = LLMRouter()
            meta = None
            try:
                from config.project_store import ProjectStore
                meta = ProjectStore().get_project(pid) if pid else None
            except Exception:
                meta = None

            want = (meta or {}).get('provider')
            if want:
                if getattr(self, '_pre_project_provider', None) is None:
                    self._pre_project_provider = router.get_active_name()
                router.switch_provider(want, (meta or {}).get('model'),
                                       persist=False)
            else:
                prev = getattr(self, '_pre_project_provider', None)
                if prev and prev != router.get_active_name():
                    # Back to whatever the user had before any override.
                    router.switch_provider(prev, persist=False)
                self._pre_project_provider = None
            self._update_ai_badge()
        except Exception as ex:
            logger.debug(u"_apply_project_provider error: {}".format(_exc_text(ex)))

    def _project_overview_text(self, pid, header=None):
        """Markdown overview of what the given project scope actually
        changes (instructions / knowledge / memory / provider / chats) —
        posted on switch, create and on demand, so the project chip stops
        being a mystery. Cheap: small JSON reads + one os.walk of the
        project's own files dir (external knowledge_dirs are NOT walked —
        they may be big network shares)."""
        from config.project_store import ProjectStore
        ps = ProjectStore()
        meta = ps.get_project(pid) or {}
        lines = [header or u"**Project: {}**".format(
            meta.get('name', pid)), u""]

        instr = u" ".join(((meta.get('instructions') or u'')).split())
        if instr:
            if len(instr) > 110:
                instr = instr[:109] + u"…"
            lines.append(u"- **Instructions:** {}".format(instr))
        else:
            lines.append(u"- **Instructions:** none yet — add them via "
                         u"project popup → Customize project; they steer "
                         u"every reply in this project.")

        # One cached counter shared with the popup subtitle, the project panel
        # and the settings dialog — there used to be four separate os.walk
        # implementations with three different caps, and only one of them
        # counted linked folders at all.
        try:
            n_files, n_extra, _docs, _unscanned = ps.count_documents(pid)
        except Exception:
            n_files, n_extra = 0, 0
        if n_files or n_extra:
            lines.append(u"- **Knowledge:** {} — replies quote these documents "
                         u"(project popup → Open knowledge folder to add "
                         u"more).".format(ps.describe_documents(pid)))
        else:
            lines.append(u"- **Knowledge:** empty — attach files in chat or "
                         u"drop PDF/DOCX/MD via project popup → Open "
                         u"knowledge folder.")

        try:
            from Intelligence import assistant_memory as _am
            _facts = _am.list_facts(pid)
            _n_proj = len([1 for _s, _f in _facts
                           if _s == _am.PROJECT_SCOPE])
            lines.append(u"- **Memory:** {} project fact(s) + {} global — "
                         u'say "remember ..." to add.'.format(
                             _n_proj, len(_facts) - _n_proj))
        except Exception:
            pass

        if meta.get('provider'):
            lines.append(u"- **AI provider:** {}{} (applied whenever this "
                         u"project activates).".format(
                             meta['provider'],
                             u" / " + meta['model']
                             if meta.get('model') else u""))
        else:
            lines.append(u"- **AI provider:** follows the global setting.")

        # Only CHATS are per-document (ProjectStore.history_path). Attachments
        # and activity logs are filed per DAY — this line used to claim all
        # three were per-document, which is simply not what the store does.
        lines.append(u"- **Chats:** stored in this project, separate per "
                     u"Revit document.")
        lines.append(u"- **Attachments & activity logs:** stored in this "
                     u"project, filed by date.")
        return u"\n".join(lines)

    def _activate_project(self, pid):
        """Switch the active project: history, knowledge scope, provider.
        UI THREAD — callers handle the busy guard themselves."""
        try:
            from config.project_store import ProjectStore
            ps = ProjectStore()
            ps.set_active_project(pid)

            # Swap chat history to the new scope
            try:
                self._reset_session_state(clear_transcript=True)
                self._restore_history()
            except Exception:
                pass

            self._apply_project_provider(pid)

            # Rescan knowledge scope for the new project in background
            self._update_knowledge_status()
            self._kick_knowledge_scan()
            name = (ps.get_project(pid) or {}).get('name') if pid else None
            if pid and name:
                try:
                    _msg = self._project_overview_text(
                        pid, header=u"Switched to project **{}** — this "
                        u"scope is now active:".format(name))
                except Exception:
                    _msg = u"Switched to project **{}**.".format(name)
            else:
                _msg = (u"Project mode off — using the shared workspace: "
                        u"global knowledge & memory, per-document chat "
                        u"history, provider unchanged.")
            self._append_bot_message(_msg, icon=_ICON_REFRESH,
                                     icon_color=_ICON_SLATE)
            self._update_composer_chips()
        except Exception as ex:
            logger.debug(u"_activate_project error: {}".format(_exc_text(ex)))

    def _create_new_project(self):
        """Create a project and activate it (called from the project popup)."""
        try:
            if self._busy:
                return
            from config.project_store import ProjectStore
            ps = ProjectStore()
            n = len(ps.list_projects()) + 1
            meta = ps.create_project(u"Project {}".format(n))
            ps.set_active_project(meta['id'])
            # sync the rest of the scope like a manual switch — same reset,
            # same history re-scope and same knowledge rescan _activate_project
            # does, instead of the partial subset this used to do.
            try:
                self._reset_session_state(clear_transcript=True)
                self._restore_history()
                self._apply_project_provider(meta['id'])
                self._kick_knowledge_scan()
            except Exception:
                pass
            self._update_composer_chips()
            try:
                _msg = self._project_overview_text(
                    meta['id'],
                    header=u"Created & switched to project **{}**."
                    .format(meta['name']))
            except Exception:
                _msg = u"Created project **{}**.".format(meta['name'])
            self._append_bot_message(_msg, icon=_ICON_SUCCESS,
                                     icon_color=_ICON_GREEN)
            # Land the user straight in the rename box instead of telling
            # them to find Settings → Projects on their own.
            self._open_llm_settings(tab='projects', select_pid=meta['id'])
        except Exception as ex:
            logger.debug(u"_create_new_project error: {}".format(_exc_text(ex)))

    # ─── Knowledge (RAG v2 index) ─────────────────────────────────────────────

    def _update_knowledge_status(self):
        """Knowledge UI moved to the LLMs Setting dialog — kept as a no-op
        hook so project switches / scans can still call it safely."""
        pass

    def _kick_knowledge_scan(self):
        """(Re)scan the active knowledge store on a background thread."""
        if not HAS_KNOWLEDGE or getattr(self, '_kn_scan_busy', False):
            return
        self._kn_scan_busy = True

        def _scan():
            try:
                store = get_active_store()
                if store is not None:
                    store.scan()
                    try:
                        from Intelligence.knowledge.embeddings import (
                            get_default_embedder)
                        emb = get_default_embedder()
                        if emb is not None and emb.is_available():
                            store.embed_pending(emb, budget_sec=120)
                    except Exception:
                        pass
            except Exception as ex:
                logger.debug(u"knowledge scan error: {}".format(_exc_text(ex)))
            finally:
                self._kn_scan_busy = False
        _kt = Thread(ThreadStart(_scan))
        _kt.IsBackground = True
        _kt.SetApartmentState(ApartmentState.STA)
        _kt.Start()

    # ─── Skills chat chips ────────────────────────────────────────────────────

    def _append_skill_chips(self, skill_ids):
        """Small 'skill activated' chip row in the chat. UI THREAD."""
        try:
            from System.Windows.Controls import Border, TextBlock, StackPanel, Orientation
            from System.Windows import Thickness, CornerRadius
            from System.Windows.Media import SolidColorBrush, Color
            from Intelligence.skills_engine import get_skills_engine

            engine = get_skills_engine()
            row = StackPanel()
            row.Orientation = Orientation.Horizontal
            row.Margin = Thickness(0, 0, 8, 8)
            added = False
            for sid in skill_ids:
                meta = None
                try:
                    meta = engine._skills.get(sid)
                except Exception:
                    pass
                name = (meta or {}).get('name', sid)
                chip = Border()
                _bind_bg(chip, 'SelectedBg')
                _bind_border(chip, 'CardBorder')
                chip.BorderThickness = Thickness(1)
                chip.CornerRadius = CornerRadius(4)
                chip.Padding = Thickness(8, 2, 8, 3)
                chip.Margin = Thickness(0, 0, 4, 0)
                # MDL2 glyph run + label run, rather than a ⚡ emoji baked
                # into the body font — the emoji rendered in full colour and
                # was the one bit of the chip that ignored the theme.
                tb = TextBlock()
                tb.FontSize = 10
                tb.FontFamily = System.Windows.Media.FontFamily("Segoe UI")
                _bind_fg(tb, 'Muted')
                from System.Windows.Documents import Run as _WpfRun
                _gr = _WpfRun()
                _gr.Text = _ICON_ANALYZE + u"  "
                _gr.FontFamily = System.Windows.Media.FontFamily(u"Segoe MDL2 Assets")
                tb.Inlines.Add(_gr)
                _nr = _WpfRun()
                _nr.Text = name
                tb.Inlines.Add(_nr)
                chip.Child = tb
                chip.ToolTip = u"Skill applied to this reply"
                row.Children.Add(chip)
                added = True
            if added:
                self.chat_history_panel.Children.Add(row)
                self._scroll_to_bottom()
        except Exception as ex:
            logger.debug(u"_append_skill_chips error: {}".format(_exc_text(ex)))

    # ─── Quick-reply chips (confirm / retry / continue) ───────────────────────

    def _append_quick_replies(self, labels, send_texts=None):
        """Clickable reply chips under the last bot message. UI THREAD.

        labels: chip captions. send_texts: what each click actually sends
        (defaults to the caption). Only ONE quick-reply row exists at a
        time; it disappears once a chip is clicked or the user sends any
        other message (_remove_quick_replies in _process_input).
        """
        try:
            self._remove_quick_replies()
            from System.Windows.Controls import Border, TextBlock, StackPanel, Orientation
            from System.Windows import Thickness, CornerRadius
            from System.Windows.Media import SolidColorBrush, Color
            from System.Windows.Input import Cursors

            texts = list(send_texts) if send_texts else list(labels)
            row = StackPanel()
            row.Orientation = Orientation.Horizontal
            row.Margin = Thickness(0, 2, 8, 12)

            _bg      = _theme_color('AccentSoft')
            _bg_hov  = _theme_color('IconHoverBg')
            for i, label in enumerate(labels):
                chip = Border()
                chip.Background = SolidColorBrush(_bg)
                _bind_border(chip, 'AccentSoft')
                chip.BorderThickness = Thickness(1)
                chip.CornerRadius = CornerRadius(4)
                chip.Padding = Thickness(11, 4, 11, 5)
                chip.Margin = Thickness(0, 0, 6, 0)
                chip.Cursor = Cursors.Hand
                tb = TextBlock()
                tb.Text = label
                tb.FontSize = 11.5
                tb.FontFamily = System.Windows.Media.FontFamily("Segoe UI")
                tb.FontWeight = System.Windows.FontWeights.SemiBold
                _bind_fg(tb, 'Accent')
                chip.Child = tb

                def _click(s, ev, _t=texts[i] if i < len(texts) else label):
                    try:
                        self._remove_quick_replies()
                        self.chat_input.Text = _t
                        self._process_input()
                    except Exception:
                        pass
                    ev.Handled = True
                chip.MouseLeftButtonUp += _click

                def _enter(s, ev, _c=chip):
                    _c.Background = SolidColorBrush(_bg_hov)

                def _leave(s, ev, _c=chip):
                    _c.Background = SolidColorBrush(_bg)
                chip.MouseEnter += _enter
                chip.MouseLeave += _leave
                row.Children.Add(chip)

            self._quickreply_row = row
            self.chat_history_panel.Children.Add(row)
            self._scroll_to_bottom()
        except Exception as ex:
            logger.debug(u"_append_quick_replies error: {}".format(_exc_text(ex)))

    def _remove_quick_replies(self):
        """Drop the active quick-reply row (if any). UI THREAD."""
        try:
            if self._quickreply_row is not None:
                self.chat_history_panel.Children.Remove(self._quickreply_row)
        except Exception:
            pass
        self._quickreply_row = None

    # ─── Turn termination (cancel / error) ────────────────────────────────────

    def _ui_invoke(self, fn):
        """Run fn on the UI thread, swallowing a shut-down dispatcher.

        Marshalling to a closed window throws on a .NET background thread with
        no handler above it, which can take the process down.
        """
        try:
            self.Dispatcher.Invoke(Action(fn))
        except Exception:
            pass

    def _claim_turn(self, rid=None):
        """True if this caller owns the terminal message for the current turn.

        Guards against two racing finishers (worker + Stop watchdog) both
        posting a bubble and both releasing the busy lock.
        """
        if rid is not None and rid != getattr(self, '_request_id', 0):
            return False
        if getattr(self, '_replied', False):
            return False
        self._replied = True
        return True

    def _cancelled(self):
        """True once Stop has been pressed for the running turn."""
        return bool(getattr(self, '_cancel_requested', False))

    def _finish_cancelled(self, rid=None, note=None):
        """End the turn because the user pressed Stop. Any thread.

        Cancellation is checked BETWEEN steps — a Revit Transaction already in
        flight is never aborted — which is what the Stop tooltip promises.
        """
        if rid is not None and rid != getattr(self, '_request_id', 0):
            return
        if getattr(self, '_batchout_request_id', None) == getattr(self, '_request_id', 0):
            if getattr(self, '_batchout_running', False):
                # The native export must return its retained-file count. A
                # watchdog cannot truthfully discard that outcome mid-call.
                return
            # A queued request has not touched the model or written files.
            # Its eventual API callback observes cancellation/the stale id.
            self._batchout_request_id = None
        if not self._claim_turn(rid):
            return

        def _ui():
            try:
                ran = getattr(self, '_tool_runs', 0)
                if ran:
                    msg = (u"Stopped. {} step(s) had already run and were "
                           u"not undone.".format(ran))
                else:
                    msg = u"Stopped."
                if note:
                    msg += u" " + note
                try:
                    self._remove_stream_bubble()
                    self._clear_stream_refs()
                except Exception:
                    pass
                self._hide_typing_indicator()
                self._append_bot_message(msg, icon=_ICON_WARNING,
                                         icon_color=_ICON_SLATE)
            except Exception:
                pass
            finally:
                self._set_busy(False)
        self._ui_invoke(_ui)

    def _report_error(self, where, exc=None, rid=None, hint=None):
        """End the turn with a VISIBLE message. Any thread.

        Every one of these paths used to clear the busy flag and hide the
        typing dots while printing nothing at all: the user's message just sat
        there and the assistant never answered. logger.error goes to a pyRevit
        console that is not open.
        """
        try:
            logger.error(u"{}: {}".format(where, _exc_text(exc)))
        except Exception:
            pass
        # Grab the failing frame NOW — sys.exc_info() is only valid while the
        # except block that called us is still on the stack, and the message
        # below is built later, on the UI thread. "_route_input: <message>"
        # alone names the outermost handler, not the code that actually broke.
        _frame = u""
        try:
            import traceback as _tbmod
            _frames = _tbmod.extract_tb(sys.exc_info()[2])
            if _frames:
                _f = _frames[-1]
                _frame = u"{}:{} in {}()".format(
                    os.path.basename(u"{}".format(_f[0])), _f[1], _f[2])
        except Exception:
            _frame = u""
        if not self._claim_turn(rid):
            return

        def _ui():
            try:
                msg = (u"Something went wrong while handling that request — "
                       u"nothing else was changed in the model. Please try "
                       u"again; the details are in the pyRevit console.")
                # Show the actual failure inline too. "See the console" is
                # useless advice in a docked pane: the console is closed, the
                # message scrolls away, and the user has nothing to report.
                _detail = u""
                try:
                    _detail = u"{}".format(_exc_text(exc) or u"").strip()
                    _detail = _detail.splitlines()[-1] if _detail else u""
                except Exception:
                    _detail = u""
                if _detail:
                    msg += u"\n\n`{}: {}`".format(where, _detail[:300])
                if _frame:
                    msg += u"\n`at {}`".format(_frame)
                if hint:
                    msg = hint + u"\n\n" + msg
                try:
                    self._remove_stream_bubble()
                    self._clear_stream_refs()
                except Exception:
                    pass
                self._hide_typing_indicator()
                self._append_bot_message(msg, icon=_ICON_WARNING,
                                         icon_color=_ICON_AMBER)
            except Exception:
                pass
            finally:
                self._set_busy(False)
        self._ui_invoke(_ui)

    def _stop_stop_watchdog(self):
        """Cancel the Stop watchdog timer, if one is armed."""
        try:
            t = getattr(self, '_stop_timer', None)
            if t is not None:
                t.Stop()
        except Exception:
            pass
        self._stop_timer = None

    # ─── Turn telemetry ───────────────────────────────────────────────────────
    # _set_busy is the one point every routing path passes through on the way
    # in and out of a turn — deterministic answers, the native agent loop, the
    # legacy JSON-intent loop and Stop all go through it — so the timer is
    # started and flushed there rather than in each branch.

    def _begin_turn_timer(self):
        """Start measuring the turn about to run. UI thread. Never raises.

        Everything read here must be free. Notably NOT has_local_llm(): it
        calls provider.check_health(), which is an HTTP probe — running it at
        the head of every turn would add exactly the latency this measures.
        The local flag comes from the provider name instead, the same test the
        agent path already uses (`provider.NAME in ("ollama", "lmstudio")`).
        """
        self._turn_timer = None
        if not HAS_TELEMETRY:
            return
        try:
            name = get_active_provider_name()
            self._turn_timer = telemetry.TurnTimer(
                provider=name,
                model=self._active_model_name(),
                local=(name in ("ollama", "lmstudio")))
        except Exception:
            self._turn_timer = None

    def _mark_first_token(self):
        """First streamed delta reached the UI. Any thread; idempotent."""
        t = getattr(self, '_turn_timer', None)
        if t is not None:
            try:
                t.mark_first_token()
            except Exception:
                pass

    def _note_turn_route(self, specialist=None, skills=None):
        """Record which specialist/skills handled the turn. Any thread."""
        t = getattr(self, '_turn_timer', None)
        if t is None:
            return
        try:
            if specialist:
                t.specialist = specialist
            if skills:
                t.skills = list(skills)
        except Exception:
            pass

    def _end_turn_timer(self):
        """Flush the finished turn to the telemetry log. UI thread.

        The write itself is queued onto a pool thread — same reasoning as
        _log_activity: a slow disk must never stall the window.
        """
        t = getattr(self, '_turn_timer', None)
        self._turn_timer = None
        if t is None:
            return
        try:
            t.finish(u'cancelled' if self._cancelled() else u'done')
        except Exception:
            return
        try:
            from System.Threading import ThreadPool, WaitCallback

            def _write(_state, _t=t):
                try:
                    telemetry.record(_t)
                except Exception:
                    pass
            ThreadPool.QueueUserWorkItem(WaitCallback(_write))
        except Exception:
            try:
                telemetry.record(t)
            except Exception:
                pass

    _PROVIDER_LABELS = {
        'claude': u'Claude (Anthropic)', 'openai': u'OpenAI',
        'deepseek': u'DeepSeek', 'ollama': u'Ollama (local)',
        'lmstudio': u'LM Studio (local)',
    }

    def _try_which_model(self, raw):
        """Answer "which model/LLM are you running?" from fact. WORKER THREAD.

        Returns True when handled. The provider and model id are known
        exactly, so this must never reach the LLM — see the call site in
        _route_input for what happened when it did.
        """
        try:
            if not routing.asks_which_llm(raw):
                return False
        except Exception:
            return False

        try:
            return self._answer_which_model(raw)
        except Exception as ex:
            logger.debug(u'_try_which_model error: {}'.format(_exc_text(ex)))
            return False

    def _answer_which_model(self, raw):
        """Post the provider/model answer. Split out so a UI failure here
        falls back to the normal path instead of eating the turn."""
        viet = _is_viet_text(raw)
        try:
            prov = get_active_provider_name() or u''
        except Exception:
            prov = u''
        label = self._PROVIDER_LABELS.get(prov, prov or (
            u'chưa cấu hình' if viet else u'not configured'))
        model = self._active_model_name()

        if not prov:
            msg = (u"Hiện chưa có AI provider nào được cấu hình. "
                   u"Mở **LLMs Setting** để kết nối một provider."
                   if viet else
                   u"No AI provider is configured yet. Open **LLMs Setting** "
                   u"to connect one.")
        elif model:
            msg = (u"Mình đang chạy trên **{}**, model `{}`.\n"
                   u"Đổi trong **LLMs Setting**.".format(label, model)
                   if viet else
                   u"I'm running on **{}**, model `{}`.\n"
                   u"You can change it in **LLMs Setting**.".format(label, model))
        else:
            # Provider set but the model id has not been resolved yet
            # (get_active_model is cache-only). Say that, do not invent one.
            msg = (u"Mình đang chạy trên **{}**. Tên model cụ thể chưa được "
                   u"nạp — mở **LLMs Setting** để xem và đổi.".format(label)
                   if viet else
                   u"I'm running on **{}**. The exact model id isn't loaded "
                   u"yet — open **LLMs Setting** to see or change it."
                   .format(label))

        def _show():
            self._hide_typing_indicator()
            self._append_bot_message(msg, icon=_ICON_INFO,
                                     icon_color=_ICON_BLUE)
            self._add_to_history("assistant", msg)
            self._set_busy(False)

        self.Dispatcher.Invoke(Action(_show))
        return True

    def _active_model_name(self):
        """Model id of the active provider, or ''.

        Reads the provider's already-resolved model — get_active_model() is
        documented as cache-only ("No HTTP; None until models were fetched",
        claude_provider.pick_fast_model) — so this stays free. Never raises.
        """
        try:
            from Intelligence.llm_router import get_router
            prov = get_router().get_active_provider()
            return u'{}'.format(prov.get_active_model() or u'')
        except Exception:
            return u''

    # ─── Session guard & UI state ─────────────────────────────────────────────

    def _set_busy(self, busy):
        """Lock/unlock the input area. Call from UI thread only.

        While busy the send button STAYS ENABLED and becomes a Stop button so
        the user can cancel a running agent request mid-flight. The text
        input also stays enabled (Claude-style): the user composes the next
        message while the agent works — Enter queues it, and the queue
        drains automatically when the busy state releases.
        """
        if (not busy and getattr(self, '_batchout_request_id', None)
                == getattr(self, '_request_id', 0)):
            # Generic routing finally blocks finish before the queued API
            # event. BatchOut's completion callback owns this turn's release.
            return
        self._busy = busy
        if busy:
            self._cancel_requested = False
            self._request_id = getattr(self, '_request_id', 0) + 1
            self._replied    = False
            self._tool_runs  = 0
            self._begin_turn_timer()
            # Tell the app-level self-study loop the assistant is active, so it
            # backs off while the user is working (see Intelligence/learning).
            try:
                from Intelligence.learning import activity as _study_activity
                _study_activity.note_active()
            except Exception:
                pass
        else:
            self._stop_stop_watchdog()
            self._end_turn_timer()
        try:
            self.chat_input.IsEnabled = True
            self._render_send_button(busy)
            self.btn_attach.IsEnabled = not busy
        except Exception:
            pass
        for btn in self._DYNAMIC_BTNS:
            try:
                btn.IsEnabled = not busy
            except Exception:
                pass
        
        # Toggle top ProgressBar visibility
        try:
            from System.Windows import Visibility
            if busy:
                self.top_loading_bar.Visibility = Visibility.Visible
            else:
                self.top_loading_bar.Visibility = Visibility.Collapsed
        except Exception:
            pass

        if busy:
            self._show_typing_indicator()
        else:
            self._hide_typing_indicator()
            # Freeze avatar spins — rotation is the "thinking" indicator,
            # so a finished turn must leave every icon standing still.
            self._stop_avatar_spins()
            # A "Try again" whose turn ended without a plain reply (it ran a
            # tool, or was stopped) must not leave the target armed — the next
            # unrelated answer would be filed as a version of an old message.
            self._retry_target_row = None

        # Auto-send the next queued message once this request is fully done.
        # BeginInvoke lets the current finish-handler stack unwind first.
        if not busy and self._queued_inputs:
            try:
                self.Dispatcher.BeginInvoke(Action(self._drain_queued_input))
            except Exception:
                pass

    def _drain_queued_input(self):
        """Send the oldest queued message. UI THREAD (via BeginInvoke)."""
        try:
            if self._busy or not self._queued_inputs:
                return
            item = self._queued_inputs.pop(0)
            try:
                if item.get('row') is not None:
                    self.chat_history_panel.Children.Remove(item['row'])
            except Exception:
                pass
            # Preserve anything the user is still typing — swap it out,
            # send the queued text, swap the draft back in.
            draft = self.chat_input.Text
            self.chat_input.Text = item['text']
            self._process_input()
            try:
                self.chat_input.Text = draft
                self.chat_input.CaretIndex = len(draft or u"")
            except Exception:
                pass
        except Exception as ex:
            logger.debug(u"_drain_queued_input error: {}".format(_exc_text(ex)))

    def _queue_input(self, raw):
        """Queue a message typed while a request is running. UI THREAD.

        Shows a muted 'Queued' chip in the transcript; clicking the chip
        cancels that queued message.
        """
        try:
            if len(self._queued_inputs) >= 3:
                self._append_bot_message(
                    u"Queue is full (3 messages) — please wait for the "
                    u"current request to finish.",
                    icon=_ICON_WARNING, icon_color=_ICON_AMBER)
                return
            from System.Windows.Controls import Border, TextBlock
            from System.Windows import Thickness, CornerRadius, TextTrimming
            from System.Windows.Media import SolidColorBrush, Color
            from System.Windows.Input import Cursors

            row = Border()
            _bind_bg(row, 'SelectedBg')
            _bind_border(row, 'CardBorder')
            row.BorderThickness = Thickness(1)
            row.CornerRadius = CornerRadius(4)
            row.Padding = Thickness(10, 4, 10, 5)
            row.Margin = Thickness(40, 0, 0, 8)
            row.HorizontalAlignment = System.Windows.HorizontalAlignment.Right
            row.Cursor = Cursors.Hand
            row.ToolTip = (u"Will send automatically when the current "
                           u"request finishes — click to cancel")
            tb = TextBlock()
            _short = raw if len(raw) <= 70 else raw[:70] + u"…"
            tb.Text = u"Queued: {}".format(_short)
            tb.FontSize = 11
            tb.FontFamily = System.Windows.Media.FontFamily("Segoe UI")
            _bind_fg(tb, 'Muted')
            tb.TextTrimming = TextTrimming.CharacterEllipsis
            row.Child = tb
            item = {'text': raw, 'row': row}

            def _cancel_queued(s, ev, _item=item):
                try:
                    if _item in self._queued_inputs:
                        self._queued_inputs.remove(_item)
                    self.chat_history_panel.Children.Remove(_item['row'])
                except Exception:
                    pass
                ev.Handled = True
            row.MouseLeftButtonUp += _cancel_queued

            self._queued_inputs.append(item)
            self.chat_history_panel.Children.Add(row)
            self.chat_input.Text = u""
            self._scroll_to_bottom()
        except Exception as ex:
            logger.debug(u"_queue_input error: {}".format(_exc_text(ex)))

    def _render_send_button(self, busy):
        """Swap the round send button between arrow-up ↑ and Stop ⏹. UI thread only.

        The icon is a Claude-style stroke Path — its Data is swapped here."""
        try:
            from System.Windows.Media import Geometry
            btn = self.send_button
            if not busy:
                self.send_icon.Data = Geometry.Parse(u"M12 19V5 M5 12l7-7 7 7")
                btn.ToolTip = u"Send (Enter)"
            else:
                self.send_icon.Data = Geometry.Parse(u"M8 8 h8 v8 h-8 Z")
                btn.ToolTip = u"Stop the running task"
            btn.IsEnabled = True
        except Exception as ex:
            logger.debug(u"_render_send_button error: {}".format(_exc_text(ex)))

    def _request_stop(self):
        """Stop button pressed while a request is running (UI thread)."""
        try:
            self._cancel_requested = True
            loop = self._agent_loop
            if loop is not None:
                loop.cancel()
            try:
                self.send_button.IsEnabled = False   # re-enabled by _set_busy(False)
            except Exception:
                pass
            self._safe_update_typing_text(u"● ● ●  Stopping after the current step…")
            self._arm_stop_watchdog()
        except Exception as ex:
            logger.debug(u"_request_stop error: {}".format(_exc_text(ex)))

    def _arm_stop_watchdog(self, seconds=20.0):
        """Release the UI even if the worker never reaches a cancel checkpoint.

        Without this, Stop pressed while a provider call is hung (server killed
        mid-generation) leaves the send button disabled and the composer stuck
        on "Stopping…" for the rest of the session. The _request_id guard means
        that if the worker DOES finish first, this fires into a no-op.
        """
        self._stop_stop_watchdog()
        try:
            from System.Windows.Threading import DispatcherTimer
            from System import TimeSpan
            rid = getattr(self, '_request_id', 0)
            timer = DispatcherTimer()
            timer.Interval = TimeSpan.FromSeconds(seconds)

            def _tick(s, ev):
                try:
                    timer.Stop()
                    if self._busy and self._cancelled():
                        self._finish_cancelled(
                            rid,
                            note=u"A background step is still finishing; "
                                 u"its result will be discarded.")
                except Exception:
                    pass
            timer.Tick += _tick
            timer.Start()
            self._stop_timer = timer
        except Exception:
            self._stop_timer = None

    @staticmethod
    def _quality_mode_on():
        """True when the Opus-parity / maximum-quality switch is enabled."""
        try:
            from config.settings import get_settings
            return bool(get_settings().is_quality_mode_enabled())
        except Exception:
            return False

    def _safe_update_typing_text(self, text):
        """Thread-safe update of the typing indicator text."""
        def action():
            try:
                if hasattr(self, "_typing_text_block") and self._typing_text_block is not None:
                    self._typing_text_block.Text = text
            except Exception:
                pass
        try:
            self.Dispatcher.Invoke(Action(action))
        except Exception:
            pass

    def _show_typing_indicator(self):
        """Add an animated '● ● ●' bubble to the chat."""
        try:
            if self._typing_row is not None:
                return  # already shown
            self._typing_row = self._make_typing_row()
            self.chat_history_panel.Children.Add(self._typing_row)
            self._scroll_to_bottom()

            # Start dynamic text timer
            from System.Windows.Threading import DispatcherTimer
            from System import TimeSpan
            self._typing_elapsed = 0
            self._update_typing_text() # initial text
            
            self._typing_timer = DispatcherTimer()
            self._typing_timer.Interval = TimeSpan.FromSeconds(1)
            self._typing_timer.Tick += self._on_typing_timer_tick
            self._typing_timer.Start()
        except Exception:
            pass

    def _hide_typing_indicator(self):
        """Remove the typing indicator bubble."""
        try:
            if self._typing_timer is not None:
                self._typing_timer.Stop()
                self._typing_timer = None
            if self._typing_row is not None:
                self.chat_history_panel.Children.Remove(self._typing_row)
                self._typing_row = None
                self._typing_text_block = None
        except Exception:
            pass

    def _on_typing_timer_tick(self, sender, e):
        self._typing_elapsed += 1
        self._update_typing_text()

    def _update_typing_text(self):
        try:
            if not hasattr(self, "_typing_text_block") or self._typing_text_block is None:
                return
            
            # Select Vietnamese text if the input was Vietnamese, else English
            is_vn = _is_viet_text(self._last_raw)

            # Quality mode = extended reasoning (Claude adaptive thinking /
            # Qwen3 thinking). Surface it so the extra latency reads as the
            # model thinking deeply, not as a stall.
            if self._quality_mode_on():
                if self._typing_elapsed < 2:
                    txt = u"● ● ●  Đang suy luận sâu..." if is_vn else "● ● ●  Reasoning deeply..."
                else:
                    txt = u"● ● ●  Đang suy luận & xử lý..." if is_vn else "● ● ●  Thinking it through..."
                self._typing_text_block.Text = txt
                return

            if self._typing_elapsed < 1:
                txt = u"● ● ●  Đang đọc dữ liệu Revit..." if is_vn else "● ● ●  Reading Revit data..."
            elif self._typing_elapsed < 3:
                txt = u"● ● ●  Đang phân tích yêu cầu..." if is_vn else "● ● ●  Formulating response..."
            else:
                txt = u"● ● ●  Đang phản hồi..." if is_vn else "● ● ●  Responding..."

            self._typing_text_block.Text = txt
        except Exception:
            pass

    def _make_typing_row(self):
        """Build the typing indicator: spinning brand mark + muted status text.

        Replies themselves no longer carry an avatar (see _append_bot_message),
        so the spinning T3Lab mark — the "working right now" signal — lives
        here instead: ONE animated mark for the turn, shown exactly where the
        answer will appear and removed the moment it arrives. Same role the
        animated logo plays in the reference chat UI.
        """
        from System.Windows.Controls import TextBlock, Grid, ColumnDefinition
        from System.Windows import Thickness, GridLength, VerticalAlignment

        row = Grid()
        row.Margin = Thickness(0, 0, 8, 12)

        col_av = ColumnDefinition()
        col_av.Width = GridLength.Auto
        col_msg = ColumnDefinition()
        col_msg.Width = GridLength(1, System.Windows.GridUnitType.Star)
        row.ColumnDefinitions.Add(col_av)
        row.ColumnDefinitions.Add(col_msg)

        av = self._make_avatar("T3", size=24)
        av.Margin = Thickness(0, 0, 9, 0)
        av.VerticalAlignment = VerticalAlignment.Center
        Grid.SetColumn(av, 0)
        row.Children.Add(av)

        dots = TextBlock()
        dots.Text       = u"● ● ●"
        dots.FontSize   = 12.5
        dots.FontFamily = System.Windows.Media.FontFamily("Hanken Grotesk, Inter")
        _bind_fg(dots, 'Muted')
        dots.VerticalAlignment = VerticalAlignment.Center

        self._typing_text_block = dots

        Grid.SetColumn(dots, 1)
        row.Children.Add(dots)
        return row


    def _safe_append_bot(self, msg, icon=None, icon_color=None):
        """Thread-safe bot message append (can be called from background threads)."""
        try:
            self.Dispatcher.Invoke(Action(
                lambda: self._append_bot_message(msg, icon=icon, icon_color=icon_color)))
        except Exception:
            pass

    def _add_to_history(self, role, content):
        """Add a message to conversation history and persist to disk.

        Turns leaving the window are folded into a rolling summary rather than
        dropped — a long session used to silently forget how it started. The
        window size comes from Intelligence.conversation so it can no longer
        disagree with agent_loop._sanitize_history, which took the last 24
        while this truncated to 16, making the larger number dead.
        """
        self._conversation_history.append({"role": role, "content": content})
        try:
            from Intelligence import conversation as _conv
            self._conversation_history, self._history_summary = _conv.trim_history(
                self._conversation_history,
                getattr(self, '_history_summary', u''),
                viet=_ui_viet())
        except Exception:
            if len(self._conversation_history) > 24:
                self._conversation_history = self._conversation_history[-24:]
        # Persist to disk so it survives window close/reopen
        self._persist_message(role, content)
        # Daily activity journal — assistant side only (the user side is
        # logged in _process_input's routing thread with attachment info).
        if role == "assistant" and content:
            one_line = u" ".join((content or u"").split())
            if len(one_line) > 400:
                one_line = one_line[:400] + u"…"
            self._log_activity(u"Assistant: {}".format(one_line))

    # ─── File attachment ──────────────────────────────────────────────────────

    def attach_clicked(self, sender, e):
        """Open a file picker and add selected file to attachment list."""
        try:
            import clr
            clr.AddReference('System.Windows.Forms')
            from System.Windows.Forms import OpenFileDialog, DialogResult

            _docs  = "*.pdf;*.txt;*.md;*.json;*.csv;*.tsv;*.log;*.xml;*.html;*.htm;*.yaml;*.yml;*.ini"
            _imgs  = "*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.webp"
            exts = ("Documents & Images|{};{}|"
                    "Documents (PDF, text, JSON...)|{}|"
                    "Images|{}|All files|*.*").format(_docs, _imgs, _docs, _imgs)
            dlg = OpenFileDialog()
            dlg.Title  = u"Choose documents or images to attach"
            dlg.Filter = exts
            dlg.Multiselect = True

            if dlg.ShowDialog() == DialogResult.OK:
                self._attach_paths(list(dlg.FileNames))
        except Exception as ex:
            logger.error(u"attach_clicked error: {}".format(_exc_text(ex)))

    def _attach_paths(self, paths):
        """Shared intake for picker / Ctrl+V / drag-drop. Returns count added.

        Filters to supported formats (PDF + images khi RAG module có mặt),
        skips duplicates, updates the chip strip. UI THREAD.
        """
        if self._busy:
            self._append_bot_message(
                u"A request is running — attach more files once it finishes.",
                icon=_ICON_SYNC, icon_color=_ICON_SLATE)
            return 0
        added, skipped = 0, []
        for path in (paths or []):
            try:
                if not path or not os.path.isfile(path):
                    continue
                if HAS_RAG and not is_supported(path):
                    skipped.append(os.path.basename(path))
                    continue
                if path not in self._attached_files:
                    self._attached_files.append(path)
                    self._add_attachment_chip(path)
                    added += 1
            except Exception:
                pass
        if added:
            self._refresh_attachment_panel()
        if skipped:
            self._append_bot_message(
                u"Skipped {} unsupported file(s) ({}) — only PDF and "
                u"images are accepted.".format(len(skipped), u", ".join(skipped[:5])),
                icon=_ICON_SYNC, icon_color=_ICON_SLATE)
        return added

    def _input_preview_keydown(self, sender, e):
        """Ctrl+V with files/image on the clipboard → attach thay vì paste text."""
        try:
            from System.Windows.Input import Key, Keyboard, ModifierKeys
            if e.Key != Key.V:
                return
            if (Keyboard.Modifiers & ModifierKeys.Control) != ModifierKeys.Control:
                return
            if self._paste_from_clipboard():
                e.Handled = True
        except Exception as ex:
            logger.debug(u"_input_preview_keydown error: {}".format(_exc_text(ex)))

    def _paste_from_clipboard(self):
        """Attach clipboard files (copy từ Explorer) hoặc ảnh clipboard.

        Returns True when something was attached (text paste = False so the
        TextBox keeps its normal behavior). UI THREAD.
        """
        from System.Windows import Clipboard
        # 1) Files copied in Explorer (Ctrl+C on files)
        try:
            if Clipboard.ContainsFileDropList():
                files = [p for p in Clipboard.GetFileDropList()]
                return self._attach_paths(files) > 0
        except Exception:
            pass
        # 2) Raw bitmap (PrtScn / Snipping Tool / copy image)
        try:
            if Clipboard.ContainsImage():
                img = Clipboard.GetImage()
                if img is not None:
                    path = self._save_clipboard_image(img)
                    if path:
                        return self._attach_paths([path]) > 0
        except Exception as ex:
            logger.debug(u"clipboard image paste error: {}".format(_exc_text(ex)))
        return False

    def _save_clipboard_image(self, bmp_source):
        """Write a clipboard BitmapSource as PNG vào folder attachments hôm nay.

        Saving straight into the dated archive folder means send-time
        archive_attachments sees src == dst and keeps the file in place.
        """
        try:
            import time as _time
            from System.Windows.Media.Imaging import PngBitmapEncoder, BitmapFrame
            from System.IO import FileStream, FileMode
            from config.project_store import ProjectStore
            ps = ProjectStore()
            dest = ps.attachments_dir(ps.get_active_project_id())
            path = os.path.join(
                dest, u"pasted_{}.png".format(_time.strftime('%H%M%S')))
            enc = PngBitmapEncoder()
            enc.Frames.Add(BitmapFrame.Create(bmp_source))
            fs = FileStream(path, FileMode.Create)
            try:
                enc.Save(fs)
            finally:
                fs.Close()
            return path
        except Exception as ex:
            logger.debug(u"_save_clipboard_image error: {}".format(_exc_text(ex)))
            return None

    def _file_drag_over(self, sender, e):
        """Show the Copy cursor for file drags anywhere over the window."""
        try:
            from System.Windows import DragDropEffects, DataFormats
            if e.Data.GetDataPresent(DataFormats.FileDrop):
                e.Effects = DragDropEffects.Copy
                e.Handled = True
        except Exception:
            pass

    def _file_drop(self, sender, e):
        """Drop files anywhere on the window → attach."""
        try:
            from System.Windows import DataFormats
            if not e.Data.GetDataPresent(DataFormats.FileDrop):
                return
            files = e.Data.GetData(DataFormats.FileDrop)
            if self._attach_paths(list(files)):
                e.Handled = True
                try:
                    self.chat_input.Focus()
                except Exception:
                    pass
        except Exception as ex:
            logger.debug(u"_file_drop error: {}".format(_exc_text(ex)))

    def clear_attachments_clicked(self, sender, e):
        """Remove all attachments."""
        self._attached_files = []
        try:
            self.attachment_chips_panel.Children.Clear()
            self._refresh_attachment_panel()
        except Exception:
            pass

    def _add_attachment_chip(self, file_path):
        """Add a small chip label for an attached file."""
        try:
            from System.Windows.Controls import Button, StackPanel, TextBlock
            from System.Windows import Thickness
            import os as _os

            name = _os.path.basename(file_path)

            btn = Button()
            try:
                btn.Style = self.FindResource('AttachChipBtn')
            except Exception:
                pass
            btn.Margin = Thickness(0, 0, 4, 4)

            sp = StackPanel()
            sp.Orientation = System.Windows.Controls.Orientation.Horizontal

            # Minimal MDL2 Attach glyph (same one used on the attach button
            # itself) — replaces the old colored 🖼️/📄 emoji pair.
            icon_lbl = TextBlock()
            icon_lbl.Text = _ICON_ATTACH + u" "
            icon_lbl.FontFamily = System.Windows.Media.FontFamily(u"Segoe MDL2 Assets")
            icon_lbl.FontSize = 11
            icon_lbl.VerticalAlignment = System.Windows.VerticalAlignment.Center
            sp.Children.Add(icon_lbl)

            name_lbl = TextBlock()
            name_lbl.Text = name if len(name) <= 22 else name[:19] + u"..."
            name_lbl.VerticalAlignment = System.Windows.VerticalAlignment.Center
            sp.Children.Add(name_lbl)

            x_lbl = TextBlock()
            x_lbl.Text = u"  ✕"
            x_lbl.FontSize = 9
            x_lbl.Foreground = System.Windows.Media.SolidColorBrush(
                System.Windows.Media.Color.FromRgb(150, 150, 150))
            x_lbl.VerticalAlignment = System.Windows.VerticalAlignment.Center
            sp.Children.Add(x_lbl)

            btn.Content = sp
            btn.ToolTip = file_path

            _path = file_path

            def _on_remove(s, ev, p=_path):
                if p in self._attached_files:
                    self._attached_files.remove(p)
                try:
                    self.attachment_chips_panel.Children.Remove(s)
                except Exception:
                    pass
                self._refresh_attachment_panel()

            btn.Click += _on_remove
            self.attachment_chips_panel.Children.Add(btn)
        except Exception as ex:
            logger.debug(u"_add_attachment_chip error: {}".format(_exc_text(ex)))

    def _refresh_attachment_panel(self):
        """Show or hide the attachment preview border depending on file list."""
        try:
            self.attachment_preview_border.Visibility = (
                Visibility.Visible if self._attached_files else Visibility.Collapsed
            )
        except Exception:
            pass

    # ─── Chat input ───────────────────────────────────────────────────────────

    def send_clicked(self, sender, e):
        if self._busy:
            # While busy the button is a Stop button. Effective on the native
            # agent path; the legacy path finishes its current step regardless.
            self._request_stop()
            return
        self._process_input()

    def input_keydown(self, sender, e):
        from System.Windows.Input import Key, Keyboard, ModifierKeys

        # ── Slash-skills popup navigation (Claude-style) ──────────────────
        if self._slash_open:
            if e.Key == Key.Up:
                self._slash_move(-1)
                e.Handled = True
                return
            if e.Key == Key.Down:
                self._slash_move(1)
                e.Handled = True
                return
            if e.Key in (Key.Return, Key.Enter, Key.Tab):
                self._slash_accept()
                e.Handled = True
                return
            if e.Key == Key.Escape:
                self._close_skills_popup()
                e.Handled = True
                return

        # Ctrl+N starts a new conversation
        if (Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control and e.Key == Key.N:
            self.new_chat_clicked()
            e.Handled = True
            return

        if e.Key == Key.Return or e.Key == Key.Enter:
            # Shift+Enter inserts a newline (multi-line input)
            if (Keyboard.Modifiers & ModifierKeys.Shift) == ModifierKeys.Shift:
                caret = self.chat_input.CaretIndex
                text = self.chat_input.Text or ""
                self.chat_input.Text = text[:caret] + "\n" + text[caret:]
                self.chat_input.CaretIndex = caret + 1
                e.Handled = True
                return
            
            # Enter sends.
            self._process_input()
            e.Handled = True
        elif e.Key == Key.Up:
            # Don't hijack Up while editing a multi-line draft.
            if u"\n" in (self.chat_input.Text or u""):
                return
            if self._input_history:
                if self._history_index == -1:
                    self._current_input_temp = self.chat_input.Text
                    self._history_index = len(self._input_history) - 1
                else:
                    self._history_index = max(0, self._history_index - 1)
                self.chat_input.Text = self._input_history[self._history_index]
                self.chat_input.CaretIndex = len(self.chat_input.Text)
                e.Handled = True
        elif e.Key == Key.Down:
            if self._input_history and self._history_index != -1:
                if self._history_index == len(self._input_history) - 1:
                    self.chat_input.Text = self._current_input_temp
                    self._history_index = -1
                else:
                    self._history_index += 1
                    self.chat_input.Text = self._input_history[self._history_index]
                self.chat_input.CaretIndex = len(self.chat_input.Text)
                e.Handled = True

    # ─── Claude-style composer: slash-skills popup + project/model chips ──────

    def input_text_changed(self, sender, e):
        """Placeholder visibility + slash-skills popup filtering. UI THREAD."""
        try:
            text = self.chat_input.Text or u""
            self.composer_placeholder.Visibility = (
                Visibility.Collapsed if text else Visibility.Visible)
            # Slash mode: "/" as first char, no whitespace typed yet
            m = re.match(r'^/([\w\-]*)$', text)
            if m is not None:
                self._refresh_skills_popup(m.group(1))
            elif self._slash_open:
                self._close_skills_popup()
        except Exception as ex:
            logger.debug(u"input_text_changed error: {}".format(_exc_text(ex)))

    def _refresh_skills_popup(self, query):
        """(Re)build the skills popup filtered by `query`. UI THREAD."""
        try:
            from Intelligence.skills_engine import get_skills_engine
            skills = [s for s in get_skills_engine().all_skills()
                      if s.get('enabled')]
        except Exception:
            skills = []
        q = (query or u"").lower()
        items = [s for s in skills
                 if q in s['id'].lower() or q in (s['name'] or u"").lower()]
        # Built-in /memory command (not a skill) — listed in the same popup
        # so it is discoverable the same way. _process_input won't find it
        # in the skills catalog, so the text routes to _try_memory_command.
        # Substring test was REVERSED ("is the query inside the word memory"),
        # so /e, /r, /o and even a bare / surfaced the Memory row.
        if u'memory'.startswith(q):
            items.append({'id': u'memory', 'name': u'Memory',
                          'description': (u'View or manage what the '
                                          u'assistant remembers'),
                          'source': None, 'enabled': True})
        # Same deal for /skills — install or update Claude skills from a
        # GitHub repo link (routes to _try_skills_command).
        if u'skills'.startswith(q):
            items.append({'id': u'skills', 'name': u'Skills',
                          'description': (u'Install or update skills from a '
                                          u'GitHub repo'),
                          'source': None, 'enabled': True})
        if not items:
            self._close_skills_popup()
            return
        self._slash_items = items
        self._slash_rows = []
        self._slash_sel = 0
        panel = self.skills_popup_panel
        panel.Children.Clear()
        for i, meta in enumerate(items):
            row = self._make_skill_row(i, meta)
            self._slash_rows.append(row)
            panel.Children.Add(row)
        self._slash_highlight()
        self.skills_popup.IsOpen = True
        self._slash_open = True

    def _make_skill_row(self, idx, meta):
        """One row of the slash-skills popup. UI THREAD."""
        subtitle = meta.get('description') or u""
        src = {'builtin': u"built-in", 'user': u"yours",
               'project': u"project"}.get(meta.get('source'))
        if src:
            subtitle = (subtitle + u"  ·  " + src) if subtitle else src

        def _click(s, ev, _i=idx):
            self._slash_sel = _i
            self._slash_accept()
            ev.Handled = True

        row = self._popup_row(u"/" + meta['id'], subtitle or None,
                              hover=False, handler=_click)

        def _enter(s, ev, _i=idx):
            self._slash_sel = _i
            self._slash_highlight()
        row.MouseEnter += _enter
        return row

    def _slash_highlight(self):
        """Paint the highlighted popup row. UI THREAD."""
        from System.Windows.Media import Brushes, SolidColorBrush, Color
        for i, row in enumerate(self._slash_rows):
            if i == self._slash_sel:
                _bind_bg(row, 'SelectedBg')
            else:
                row.Background = Brushes.Transparent

    def _slash_move(self, delta):
        """Move popup selection with Up/Down (wraps). UI THREAD."""
        if not self._slash_items:
            return
        self._slash_sel = (self._slash_sel + delta) % len(self._slash_items)
        self._slash_highlight()
        try:
            self._slash_rows[self._slash_sel].BringIntoView()
        except Exception:
            pass

    def _slash_accept(self):
        """Insert the highlighted skill as '/id ' into the input. UI THREAD."""
        try:
            meta = self._slash_items[self._slash_sel]
        except Exception:
            self._close_skills_popup()
            return
        self._close_skills_popup()
        self.chat_input.Text = u"/" + meta['id'] + u" "
        self.chat_input.CaretIndex = len(self.chat_input.Text)
        self.chat_input.Focus()

    def _close_skills_popup(self):
        self._slash_open = False
        self._slash_items = []
        self._slash_rows = []
        try:
            self.skills_popup.IsOpen = False
        except Exception:
            pass

    def _apply_forced_skill(self):
        """Inject the /slash-forced skill into the dispatcher decision.

        Idempotent — safe to call from several points along _route_input.
        skill_forced=True later bypasses filter_for_specialist so an explicit
        user invocation always wins over the agents: frontmatter filter.

        The specialist is ALSO constrained to the skill's `agents:` list.
        The dispatcher classifies the routed message, which for a bare
        "/skill-id" is synthetic boilerplate: its word "MODIFY" matched the
        action-verb table, so every slash-only invocation was handed the
        revit_action role (write tools + the "tô đỏ tường" few-shot) and a
        local model replayed the previous turn's edit instead of running
        the playbook. The frontmatter decides now.
        """
        sid = getattr(self, '_forced_skill_id', None)
        if not sid:
            return
        if not self._agent_decision:
            self._agent_decision = {'specialist': 'general',
                                    'source': 'slash', 'confidence': 1.0}
        self._agent_decision['skill'] = sid
        self._agent_decision['skill_forced'] = True
        try:
            from Intelligence.skills_engine import get_skills_engine
            _cur = self._agent_decision.get('specialist')
            _spec = get_skills_engine().specialist_for(sid, _cur)
            if _spec and _spec != _cur:
                logger.debug("slash /{}: specialist {} -> {} "
                             "(skill frontmatter)".format(sid, _cur, _spec))
                self._agent_decision['specialist'] = _spec
                self._agent_decision['source'] = 'slash'
        except Exception as _sp_ex:
            logger.debug(u"specialist_for error: {}".format(_exc_text(_sp_ex)))

    def _popup_row(self, title, subtitle=None, icon=None, active=False,
                   dot=None, hover=True, handler=None):
        """Claude-style popup menu row (used by all composer popups). UI THREAD."""
        from System.Windows.Controls import (Grid, ColumnDefinition,
                                             StackPanel, TextBlock, Border)
        from System.Windows.Shapes import Ellipse
        from System.Windows import (Thickness, CornerRadius, GridLength,
                                    GridUnitType, TextTrimming,
                                    VerticalAlignment)
        from System.Windows.Media import (Brushes, SolidColorBrush, Color,
                                          FontFamily)
        from System.Windows.Input import Cursors

        row = Border()
        row.CornerRadius = CornerRadius(8)
        row.Padding = Thickness(10, 7, 10, 7)
        row.Background = Brushes.Transparent
        row.Cursor = Cursors.Hand

        g = Grid()
        c0 = ColumnDefinition(); c0.Width = GridLength.Auto
        c1 = ColumnDefinition(); c1.Width = GridLength(1, GridUnitType.Star)
        c2 = ColumnDefinition(); c2.Width = GridLength.Auto
        g.ColumnDefinitions.Add(c0)
        g.ColumnDefinitions.Add(c1)
        g.ColumnDefinitions.Add(c2)

        lead = None
        if dot is not None:
            lead = Ellipse()
            lead.Width = 8
            lead.Height = 8
            lead.Fill = SolidColorBrush(Color.FromRgb(dot[0], dot[1], dot[2]))
            lead.VerticalAlignment = VerticalAlignment.Center
            lead.Margin = Thickness(0, 0, 8, 0)
        elif icon:
            lead = TextBlock()
            lead.Text = icon
            lead.FontFamily = FontFamily(u"Segoe MDL2 Assets")
            lead.FontSize = 11
            _bind_fg(lead, 'Muted')
            lead.VerticalAlignment = VerticalAlignment.Center
            lead.Margin = Thickness(0, 1, 8, 0)
        if lead is not None:
            Grid.SetColumn(lead, 0)
            g.Children.Add(lead)

        body = StackPanel()
        t = TextBlock()
        t.Text = title
        t.FontSize = 12
        t.FontFamily = FontFamily(u"Hanken Grotesk")
        t.FontWeight = System.Windows.FontWeights.SemiBold
        _bind_fg(t, 'Ink')
        t.TextTrimming = TextTrimming.CharacterEllipsis
        body.Children.Add(t)
        if subtitle:
            st = TextBlock()
            st.Text = subtitle
            st.FontSize = 10.5
            st.FontFamily = FontFamily(u"Hanken Grotesk")
            _bind_fg(st, 'Muted')
            st.TextTrimming = TextTrimming.CharacterEllipsis
            st.Margin = Thickness(0, 1, 0, 0)
            body.Children.Add(st)
        Grid.SetColumn(body, 1)
        g.Children.Add(body)

        if active:
            chk = TextBlock()
            chk.Text = u""
            chk.FontFamily = FontFamily(u"Segoe MDL2 Assets")
            chk.FontSize = 11
            _bind_fg(chk, 'Blue')
            chk.VerticalAlignment = VerticalAlignment.Center
            chk.Margin = Thickness(10, 0, 0, 0)
            Grid.SetColumn(chk, 2)
            g.Children.Add(chk)

        row.Child = g

        if hover:
            def _enter(s, ev):
                _bind_bg(row, 'IconHoverBg')

            def _leave(s, ev):
                row.Background = Brushes.Transparent
            row.MouseEnter += _enter
            row.MouseLeave += _leave
        if handler is not None:
            row.PreviewMouseLeftButtonDown += handler
        return row

    def _popup_separator(self):
        from System.Windows.Controls import Border
        from System.Windows import Thickness
        from System.Windows.Media import SolidColorBrush, Color
        sep = Border()
        sep.Height = 1
        _bind_bg(sep, 'Divider')
        sep.Margin = Thickness(6, 5, 6, 5)
        return sep

    def _update_composer_chips(self):
        """Refresh the project + model chips of the composer. UI THREAD."""
        from System.Windows.Media import SolidColorBrush, Color
        try:
            from config.project_store import ProjectStore
            ps = ProjectStore()
            pid = ps.get_active_project_id()
            meta = ps.get_project(pid) if pid else None
            if meta:
                self.project_chip_text.Text = meta.get('name') or u"Project"
                self.project_chip_text.Foreground = SolidColorBrush(
                    _theme_color('Accent'))
            else:
                self.project_chip_text.Text = u"Project"
                self.project_chip_text.Foreground = SolidColorBrush(
                    _theme_color('Muted'))
        except Exception as ex:
            logger.debug(u"_update_composer_chips project error: {}".format(_exc_text(ex)))
        try:
            from Intelligence.llm_router import LLMRouter
            router = LLMRouter()
            self.model_chip_text.Text = router.get_display_label()
            rgb = self._BADGE_COLORS.get(router.get_active_name(),
                                         self._BADGE_GRAY)
            self.model_chip_dot.Fill = SolidColorBrush(
                Color.FromRgb(rgb[0], rgb[1], rgb[2]))
        except Exception as ex:
            logger.debug(u"_update_composer_chips model error: {}".format(_exc_text(ex)))

    def project_chip_clicked(self, sender, e):
        """Open the Claude-style project picker popup."""
        try:
            self._build_project_popup()
            self.project_popup.IsOpen = True
        except Exception as ex:
            logger.debug(u"project_chip_clicked error: {}".format(_exc_text(ex)))

    def _project_row_subtitle(self, ps, pid):
        """Content hint for a project popup row: 'N files · instructions ✓'.

        Uses the store's cached counter. This ran once per project while
        building the popup, so with the old private os.walk + its own
        get_project() a popup open cost 2N JSON reads and N directory walks on
        the UI thread.
        """
        try:
            meta = ps.get_project(pid) or {}
            n, n_dirs, _docs, _un = ps.count_documents(pid, cap=99)
            has_instr = bool((meta.get('instructions') or u'').strip())
            return u"{} file{}{} · {}".format(
                u"99+" if n > 99 else n, u"" if n == 1 else u"s",
                u" +{} linked".format(n_dirs) if n_dirs else u"",
                u"instructions ✓" if has_instr else u"no instructions")
        except Exception:
            return None

    def _build_project_popup(self):
        """Fill the project popup: none + projects (with content hints) +
        actions for the active project + new/customize. UI THREAD."""
        from config.project_store import ProjectStore
        ps = ProjectStore()
        active = ps.get_active_project_id()
        panel = self.project_popup_panel
        panel.Children.Clear()

        panel.Children.Add(self._popup_row(
            u"No project", u"Shared workspace",
            active=(not active),
            handler=lambda s, ev: self._project_popup_select(None)))
        for meta in ps.list_projects():
            panel.Children.Add(self._popup_row(
                meta['name'], self._project_row_subtitle(ps, meta['id']),
                active=(meta['id'] == active),
                handler=(lambda s, ev, _p=meta['id']:
                         self._project_popup_select(_p))))
        panel.Children.Add(self._popup_separator())
        if active:
            panel.Children.Add(self._popup_row(
                u"Project panel",
                u"Instructions · Memory · Context · Scheduled",
                icon=u"",
                handler=lambda s, ev: self._open_project_panel()))
            panel.Children.Add(self._popup_row(
                u"Open knowledge folder",
                u"Drop PDF/DOCX/MD here for replies",
                icon=u"",
                handler=lambda s, ev: self._project_popup_open_folder()))
        panel.Children.Add(self._popup_row(
            u"New project", None, icon=u"",
            handler=lambda s, ev: self._project_popup_new()))
        panel.Children.Add(self._popup_row(
            u"Customize project…", u"Name, instructions, knowledge",
            icon=u"",
            handler=lambda s, ev: self._project_popup_settings()))

    def _project_popup_select(self, pid):
        try:
            self.project_popup.IsOpen = False
        except Exception:
            pass
        try:
            if self._busy:
                self._append_bot_message(
                    u"A request is running — switch projects once it finishes.",
                    icon=_ICON_SYNC, icon_color=_ICON_SLATE)
                return
            from config.project_store import ProjectStore
            if ProjectStore().get_active_project_id() == pid:
                return
            self._activate_project(pid)
        except Exception as ex:
            logger.debug(u"_project_popup_select error: {}".format(_exc_text(ex)))

    def _project_popup_new(self):
        try:
            self.project_popup.IsOpen = False
        except Exception:
            pass
        self._create_new_project()

    # ─── Project panel (Claude-style: Instructions/Memory/Context/Scheduled) ──

    def _open_project_panel(self):
        """Open the project panel overlay for the active project."""
        try:
            self.project_popup.IsOpen = False
        except Exception:
            pass
        try:
            from config.project_store import ProjectStore
            pid = ProjectStore().get_active_project_id()
            if not pid:
                return
            self._build_project_panel(pid)
            self.project_panel_overlay.Visibility = Visibility.Visible
        except Exception as ex:
            logger.debug(u"_open_project_panel error: {}".format(_exc_text(ex)))

    def project_panel_close_clicked(self, sender, e):
        try:
            self.project_panel_overlay.Visibility = Visibility.Collapsed
        except Exception:
            pass

    def _edit_project_in_settings(self, pid):
        """Close the panel and open LLMs Setting → Projects on this project."""
        try:
            self.project_panel_overlay.Visibility = Visibility.Collapsed
        except Exception:
            pass
        self._open_llm_settings(tab='projects', select_pid=pid)

    def _build_project_panel(self, pid):
        """Render a READ-ONLY overview of the project scope into
        project_panel_host. UI THREAD.

        Everything editable moved to LLMs Setting → Projects (single edit
        surface). What is left here answers "what does this project scope
        actually cover right now", including the linked external folders the
        old version never showed."""
        from System.Windows.Controls import (Border, Button, Grid as WGrid,
                                             ColumnDefinition, TextBlock)
        from System.Windows import (Thickness, TextWrapping, GridLength,
                                    TextDecorations)
        from System.Windows.Media import SolidColorBrush, Color
        from System.Windows.Input import Cursors
        from config.project_store import ProjectStore

        ps = ProjectStore()
        meta = ps.get_project(pid) or {}
        host = self.project_panel_host
        host.Children.Clear()
        try:
            self.project_panel_title.Text = meta.get('name') or u"Project"
        except Exception:
            pass

        _font  = System.Windows.Media.FontFamily("Hanken Grotesk, Inter")

        def _txt(text, size=12, fg=None, bold=False, wrap=True, margin=None):
            # NOT named _tb: a local of that name made the module-level theme
            # helper local to this whole function, so the brush lookups above
            # raised UnboundLocalError before the def was ever reached — and
            # _open_project_panel swallows the exception, so the panel simply
            # never appeared. `fg` is a theme TOKEN NAME now, bound live.
            t = TextBlock()
            t.Text = text
            t.FontSize = size
            t.FontFamily = _font
            _bind_fg(t, fg or 'Ink')
            if bold:
                t.FontWeight = System.Windows.FontWeights.SemiBold
            if wrap:
                t.TextWrapping = TextWrapping.Wrap
            if margin is not None:
                t.Margin = margin
            return t

        def _two_col(left, right=None, top=6):
            g = WGrid()
            g.Margin = Thickness(0, top, 0, 0)
            g.ColumnDefinitions.Add(ColumnDefinition())
            c1 = ColumnDefinition()
            c1.Width = GridLength.Auto
            g.ColumnDefinitions.Add(c1)
            g.Children.Add(left)
            if right is not None:
                WGrid.SetColumn(right, 1)
                g.Children.Add(right)
            return g

        def _section(title, right_widget=None):
            host.Children.Add(_two_col(
                _txt(title, size=13.5, bold=True, wrap=False),
                right_widget, top=0))

        def _sep():
            b = Border()
            b.Height = 1
            _bind_bg(b, 'SelectedBg')
            b.Margin = Thickness(0, 14, 0, 14)
            host.Children.Add(b)

        def _small_btn(label, handler, primary=False):
            b = Button()
            b.Content = label
            # ComposerChipBtn, not the shared PrimaryButton / SecondaryButton.
            # Those come from the auto-synced Lumina block and are pinned to
            # #0F172A, which follows no theme at all: in Revit's dark theme
            # SecondaryButton paints near-black text on a near-black panel —
            # invisible. The chip style is theme-bound.
            try:
                b.Style = self.FindResource("ComposerChipBtn")
            except Exception:
                pass
            if primary:
                _bind_bg(b, 'AccentSoft')
                _bind_fg(b, 'Ink')
            b.FontSize = 11
            b.Padding = Thickness(12, 4, 12, 4)
            b.Margin = Thickness(8, 0, 0, 0)
            b.Click += handler
            return b

        # Read-only by design. Editing a project lives in ONE place — LLMs
        # Setting → Projects — so the two surfaces can no longer write the same
        # project.json in different shapes (this panel patched `instructions`
        # alone while the dialog always rewrote name/instructions/provider/model
        # together) or race each other's whole-file rewrites.

        # ── 1. Instructions ────────────────────────────────────────────────
        instr = (meta.get('instructions') or u'').strip()
        _section(u"Instructions",
                 _small_btn(u"Edit in LLMs Setting",
                            lambda s, ev: self._edit_project_in_settings(pid)))
        if instr:
            _prev = u" ".join(instr.split())
            if len(_prev) > 260:
                _prev = _prev[:259] + u"\u2026"
            host.Children.Add(_txt(_prev, size=11.5, fg='Muted',
                                  margin=Thickness(0, 6, 0, 0)))
        else:
            host.Children.Add(_txt(
                u"None yet \u2014 instructions steer every reply in this project.",
                size=11.5, fg='Faint', margin=Thickness(0, 6, 0, 0)))

        _sep()

        # ── 2. Memory (project-scope facts) ────────────────────────────────
        # Read-only here too; forgetting a fact now lives in the Projects tab.
        _section(u"Memory")
        try:
            from Intelligence import assistant_memory as _am
            _facts = _am.list_facts(pid)
            _proj = [f for _s, f in _facts if _s == _am.PROJECT_SCOPE]
            _glob = len(_facts) - len(_proj)
            if _proj:
                for _f in _proj[:8]:
                    host.Children.Add(_txt(
                        u"\u2022 " + (_f.get('text') or u''), size=11.5,
                        fg='Muted', margin=Thickness(0, 4, 0, 0)))
                if len(_proj) > 8:
                    host.Children.Add(_txt(
                        u"\u2026 and {} more".format(len(_proj) - 8),
                        size=11, fg='Faint', margin=Thickness(0, 4, 0, 0)))
            else:
                host.Children.Add(_txt(
                    u'No project facts yet \u2014 say "remember \u2026" in chat.',
                    size=11.5, fg='Faint', margin=Thickness(0, 6, 0, 0)))
            if _glob:
                host.Children.Add(_txt(
                    u"+ {} global fact(s) apply to every project.".format(_glob),
                    size=10.5, fg='Faint', margin=Thickness(0, 6, 0, 0)))
        except Exception as _mex:
            logger.debug(u"panel memory error: {}".format(_exc_text(_mex)))

        _sep()

        # ── 3. Knowledge (what the RAG index answers from) ─────────────────
        _section(u"Knowledge")
        try:
            host.Children.Add(_txt(ps.describe_documents(pid), size=11.5,
                                  fg='Muted', margin=Thickness(0, 6, 0, 0)))
        except Exception:
            pass

        # Linked external folders were never shown here, so the document list
        # looked empty for projects whose knowledge lives on a share.
        try:
            for _d in (ps.get_knowledge_dirs(pid) or [])[:8]:
                _missing = not os.path.isdir(_d)
                _row = _txt(u"\U0001F517 " + (os.path.basename(_d.rstrip(u"\\/")) or _d)
                           + (u"  (not found)" if _missing else u""),
                           size=11, fg='Danger' if _missing else 'Faint',
                           margin=Thickness(0, 4, 0, 0))
                _row.ToolTip = _d
                host.Children.Add(_row)
        except Exception:
            pass

        lnk = _txt(u"Open knowledge folder", size=11.5, fg='Blue',
                  margin=Thickness(0, 8, 0, 0))
        lnk.Cursor = Cursors.Hand
        lnk.TextDecorations = TextDecorations.Underline
        lnk.MouseLeftButtonUp += (
            lambda s, ev: self._open_active_project_folder())
        host.Children.Add(lnk)

        _sep()

        # ── 4. Scheduled (daily prompts while the window is open) ─────────
        _section(u"Scheduled prompts")
        _sched = meta.get('scheduled') or []
        if _sched:
            for it in _sched:
                _pv = u" ".join((it.get('prompt') or u'').split())
                if len(_pv) > 70:
                    _pv = _pv[:69] + u"\u2026"
                _on = bool(it.get('enabled', True))
                host.Children.Add(_txt(
                    u"{} daily \u2014 {}{}".format(
                        it.get('time') or u'?', _pv,
                        u"" if _on else u"   (paused)"),
                    size=11.5, fg='Muted' if _on else 'Faint',
                    margin=Thickness(0, 4, 0, 0)))
            host.Children.Add(_txt(
                u"Runs once per day at the set time while the assistant "
                u"window is open (skipped while a request is busy).",
                size=10.5, fg='Faint', margin=Thickness(0, 8, 0, 0)))
        else:
            host.Children.Add(_txt(
                u"None yet \u2014 add recurring daily prompts in LLMs Setting "
                u"\u2192 Projects.", size=11.5, fg='Faint',
                margin=Thickness(0, 6, 0, 0)))

    # ─── Scheduled prompts runner ─────────────────────────────────────────────

    def _start_schedule_timer(self):
        """30s UI-thread timer that fires due per-project scheduled prompts
        (project panel → Scheduled) while the window is open."""
        try:
            from System.Windows.Threading import DispatcherTimer
            from System import TimeSpan
            t = DispatcherTimer()
            t.Interval = TimeSpan.FromSeconds(30)
            t.Tick += self._schedule_tick
            t.Start()
            self._sched_timer = t
        except Exception as ex:
            logger.debug(u"_start_schedule_timer error: {}".format(_exc_text(ex)))

    def _schedule_tick(self, sender, e):
        """Fire at most ONE due scheduled prompt per tick.

        A task is due when enabled, not yet run today, and its HH:MM has
        passed. The due test itself lives in ProjectStore.due_schedule so it is
        testable without WPF and cannot drift from the settings UI.
        """
        try:
            if self._busy:
                return
            # The tick drives the real composer: _process_input() snapshots
            # staged attachments and the forced /skill, then clears both. Firing
            # now would send the user's files with the scheduled prompt and wipe
            # their chips. Wait for the next tick instead.
            if getattr(self, '_attached_files', None):
                return
            if getattr(self, '_forced_skill_id', None):
                return
            if (self.chat_input.Text or u"").strip():
                # A half-typed message would be swapped out and back; leave the
                # user alone until the composer is idle.
                return

            from config.project_store import ProjectStore
            import time as _t
            ps = ProjectStore()
            pid = ps.get_active_project_id()
            if not pid:
                return
            items = (ps.get_project(pid) or {}).get('scheduled') or []
            if not items:
                return
            fired = ps.due_schedule(items, _t.strftime('%H:%M'),
                                    _t.strftime('%Y-%m-%d'))
            if not fired:
                return

            self.chat_input.Text = fired.get('prompt') or u''
            _before = self._request_id
            self._process_input()
            # Stamp last_run ONLY if the turn actually started. It used to be
            # written before sending, so a _process_input that returned early
            # (busy re-check, an exception) marked the task done for the day
            # without it ever reaching the model.
            if self._request_id != _before:
                ps.update_project(pid, {'scheduled': [
                    dict(t, last_run=_t.strftime('%Y-%m-%d'))
                    if t.get('id') == fired.get('id') else t
                    for t in items]})
            else:
                self.chat_input.Text = u""
        except Exception as ex:
            logger.debug(u"_schedule_tick error: {}".format(_exc_text(ex)))
    def _open_active_project_folder(self):
        """Open the active project's knowledge folder (files/) in Explorer —
        the drop target for PDF/DOCX/MD the RAG index answers from."""
        d = u""
        try:
            from config.project_store import ProjectStore
            ps = ProjectStore()
            pid = ps.get_active_project_id()
            if not pid:
                self._append_bot_message(
                    u"No active project — pick one from the project chip first.",
                    icon=_ICON_WARNING, icon_color=_ICON_AMBER)
                return
            d = os.path.join(ps.project_dir(pid), 'files')
            # Shared opener: carries the .NET 8 UseShellExecute fallback this
            # copy used to be missing.
            if _open_in_explorer(d):
                return
        except Exception as ex:
            logger.debug(u"_open_active_project_folder error: {}".format(_exc_text(ex)))
        try:
            self._append_bot_message(
                u"Couldn't open the knowledge folder:\n`{}`".format(d),
                icon=_ICON_WARNING, icon_color=_ICON_AMBER)
        except Exception:
            pass

    def _project_popup_open_folder(self):
        try:
            self.project_popup.IsOpen = False
        except Exception:
            pass
        self._open_active_project_folder()

    def _project_popup_settings(self):
        try:
            self.project_popup.IsOpen = False
        except Exception:
            pass
        self._open_llm_settings(tab='projects')

    def model_chip_clicked(self, sender, e):
        """Open the Claude-style provider/model picker popup."""
        try:
            self._build_model_popup()
            self.model_popup.IsOpen = True
        except Exception as ex:
            logger.debug(u"model_chip_clicked error: {}".format(_exc_text(ex)))

    def _build_model_popup(self, _skip_refresh=False):
        """Fill the model popup from the instant router snapshot. UI THREAD.

        Only providers that are actually READY (configured for remote /
        probed-reachable for local) are listed. Showing every provider,
        including ones marked "Not set up", confused users about what they
        could actually pick, so unconfigured providers are hidden. The active
        provider is always kept as a safety net, and a background probe keeps
        local providers (Ollama / LM Studio) accurate since the instant
        snapshot reports them unavailable until probed.
        """
        from Intelligence.llm_router import LLMRouter
        router = LLMRouter()
        status = router.get_status_instant()
        panel = self.model_popup_panel
        panel.Children.Clear()
        shown = 0
        for name in router.get_provider_names():
            info = status.get(name, {})
            if not info.get('available') and not info.get('active'):
                continue                       # hide "Not set up" providers
            model = info.get('model') or u""
            subtitle = model if model else (
                None if info.get('available')
                else u"Not configured — open Settings")
            # Flag a local model that is too small for reliable tool-calling, at
            # the exact point the user is choosing one.
            if model and name in ("ollama", "lmstudio"):
                try:
                    from Intelligence import local_llm as _ll
                    if not _ll.is_tool_capable_size(model):
                        subtitle = u"{} · small, may misfire tools".format(model)
                except Exception:
                    pass
            panel.Children.Add(self._popup_row(
                info.get('display_name', name), subtitle,
                active=info.get('active', False),
                dot=self._BADGE_COLORS.get(name, self._BADGE_GRAY),
                handler=(lambda s, ev, _n=name:
                         self._model_popup_select(_n))))
            shown += 1
        if shown == 0:
            # Never leave the menu blank - point the user at Settings.
            panel.Children.Add(self._popup_row(
                u"No providers ready - open Settings to configure",
                None, icon=u"", hover=False,
                handler=lambda s, ev: self._model_popup_settings()))
        if not _skip_refresh:
            self._refresh_model_popup_async(status)
        panel.Children.Add(self._popup_separator())
        panel.Children.Add(self._popup_row(
            u"Configure API key & model…", None, icon=u"",
            handler=lambda s, ev: self._model_popup_settings()))

    def _refresh_model_popup_async(self, prev_status):
        """Probe local providers (fast, localhost) off-thread and rebuild the
        popup once if their Ready status changed. Ollama / LM Studio report
        unavailable in the instant snapshot until probed, so a just-started
        local engine would otherwise stay hidden until the next open.
        """
        if getattr(self, '_model_popup_refreshing', False):
            return
        self._model_popup_refreshing = True

        def _work():
            changed = False
            try:
                from Intelligence.llm_router import LLMRouter
                router = LLMRouter()
                names = router.get_provider_names()
                for name in ('ollama', 'lmstudio'):
                    if name not in names:
                        continue
                    before = bool((prev_status.get(name) or {}).get('available'))
                    try:
                        info = router.probe_provider(name)
                    except Exception:
                        info = None
                    if bool((info or {}).get('available')) != before:
                        changed = True
            except Exception:
                changed = False

            def _rebuild():
                self._model_popup_refreshing = False
                try:
                    if (changed and getattr(self, 'model_popup', None) is not None
                            and self.model_popup.IsOpen):
                        self._build_model_popup(_skip_refresh=True)
                except Exception:
                    pass
            try:
                self.Dispatcher.Invoke(Action(_rebuild))
            except Exception:
                self._model_popup_refreshing = False

        t = Thread(ThreadStart(_work))
        t.IsBackground = True
        t.Start()

    def _model_popup_select(self, name):
        try:
            self.model_popup.IsOpen = False
        except Exception:
            pass
        try:
            from Intelligence.llm_router import LLMRouter
            if LLMRouter().get_active_name() == name:
                return
            self._switch_provider(name)
        except Exception as ex:
            logger.debug(u"_model_popup_select error: {}".format(_exc_text(ex)))

    def _model_popup_settings(self):
        try:
            self.model_popup.IsOpen = False
        except Exception:
            pass
        self._open_llm_settings()

    # ─── Harness: action mode (auto / confirm-first) + activity log ──────────

    def action_mode_clicked(self, sender, e):
        """Toggle between 'auto' (act immediately) and 'confirm' (plan first)."""
        try:
            from config.settings import get_settings
            settings = get_settings()
            new_mode = ('confirm' if settings.get_action_mode() == 'auto'
                        else 'auto')
            settings.set_agent_option('action_mode', new_mode)
            # The chip itself renders the new state — no chat bubble, it just
            # piled up in the transcript every time the mode was toggled.
            self._update_action_mode_chip()
        except Exception as ex:
            logger.debug(u"action_mode_clicked error: {}".format(_exc_text(ex)))

    def _update_action_mode_chip(self):
        """Render the action-mode chip state (Claude-style stroke icons:
        shield = ask-before-edits, zap = auto). UI THREAD."""
        try:
            from System.Windows.Media import SolidColorBrush, Color, Geometry
            from config.settings import get_settings
            confirm = (get_settings().get_action_mode() == 'confirm')
            _SHIELD = (u"M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01"
                       u"C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 "
                       u"6.24-2.72a1 1 0 0 1 1.52 0C14.5 3.8 17 5 19 5a1 1 0 0 1 1 1z")
            _ZAP = (u"M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46"
                    u"l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63"
                    u"l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z")
            if confirm:
                self.action_mode_text.Text = u"Ask before edits"
                self.action_mode_text.Foreground = SolidColorBrush(
                    _theme_color('Accent'))
                self.action_mode_icon.Data = Geometry.Parse(_SHIELD)
                self.action_mode_icon.Stroke = SolidColorBrush(
                    _theme_color('Blue'))
            else:
                self.action_mode_text.Text = u"Auto"
                self.action_mode_text.Foreground = SolidColorBrush(
                    _theme_color('Muted'))
                self.action_mode_icon.Data = Geometry.Parse(_ZAP)
                self.action_mode_icon.Stroke = SolidColorBrush(
                    _theme_color('Muted'))
        except Exception as ex:
            logger.debug(u"_update_action_mode_chip error: {}".format(_exc_text(ex)))

    def activity_log_clicked(self, sender, e):
        """Open today's activity log (notepad) or the logs folder (Explorer).

        Never Process.Start the .md file directly: on machines without a
        markdown association that throws Win32Exception ("No application is
        associated...") which used to be swallowed silently — the button
        looked dead. notepad.exe always exists and reads UTF-8 fine.
        """
        try:
            from config.project_store import ProjectStore
            ps = ProjectStore()
            pid = ps.get_active_project_id()
            path = ps.activity_log_path(pid)
            folder = os.path.dirname(path)
            # Shared openers — both carry the shell fallbacks.
            if os.path.isfile(path):
                if _open_file_shell(path):
                    return
            elif _open_in_explorer(folder):
                return
            raise RuntimeError(u"shell refused {}".format(path))
        except Exception as ex:
            logger.error("activity_log_clicked error: {}".format(_exc_text(ex)))
            try:
                self._append_bot_message(
                    u"Could not open the activity log.",
                    icon=_ICON_WARNING, icon_color=_ICON_AMBER)
            except Exception:
                pass

    def _log_activity(self, text):
        """Append to today's activity log on a pool thread. Never raises.

        File appends must never run on the UI thread — a slow disk or an
        AV scan on the log file would freeze the window per message.
        """
        try:
            from System.Threading import ThreadPool, WaitCallback

            def _write(_state):
                try:
                    from config.project_store import ProjectStore
                    ps = ProjectStore()
                    ps.append_activity(text, ps.get_active_project_id())
                except Exception:
                    pass
            ThreadPool.QueueUserWorkItem(WaitCallback(_write))
        except Exception:
            pass

    # NOTE — 2026-07-28: the DB-only fast path was deleted here.
    # _FAST_CONTEXT_ENABLED had been False since 2026-07-06 (its keyword
    # matching hijacked unrelated queries — "check lỗi tiếng Anh trong dự án"
    # answered with project info), so _try_fast_context_answer, _count_elements
    # and their keyword tables were ~220 lines of unreachable code. The agent
    # path answers these questions from real tool results instead, under the
    # "NUMBERS MUST COME FROM TOOLS" rule in agent_loop._AGENT_PROMPT.

    # ─── Persistent memory: /memory command + "remember ..." triggers ─────────

    def _try_memory_command(self, raw):
        """Deterministic persistent-memory handling. WORKER THREAD.

        Handles the /memory command family (show / forget N / clear) and
        explicit "remember that ..." / "nhớ là ..." saves without an LLM
        round-trip, so memory works even fully offline. Returns True when
        the request was completely handled here (UI updated, busy released).
        """
        try:
            text = (raw or u'').strip()
            if not text:
                return False
            from Intelligence import assistant_memory
            pid = None
            try:
                from config.project_store import ProjectStore
                pid = ProjectStore().get_active_project_id()
            except Exception:
                pass
            viet = _is_viet_text(text)
            low = text.lower()

            msg = None
            icon, color = _ICON_SUCCESS, _ICON_GREEN

            # ── /memory [show | forget N | clear] ─────────────────────────
            m = re.match(r'^/memory(?:\s+(.*))?$', low, re.S)
            if m:
                sub = (m.group(1) or u'').strip()
                if sub in (u'', u'show', u'list'):
                    msg = assistant_memory.format_memory_report(pid, viet)
                    icon, color = _ICON_LIST, _ICON_SLATE
                elif sub.startswith((u'forget', u'xoa', u'xóa')):
                    # A bare number removes fact #N (list order); anything else
                    # is treated as the fact's text and matched by content.
                    arg = re.sub(u'^(?:forget|xóa|xoa)\\s*', u'', sub).strip()
                    if re.match(r'^\d+$', arg):
                        ok, removed = assistant_memory.remove_fact(
                            int(arg), pid)
                    elif arg:
                        ok, removed = assistant_memory.forget_fact(arg, pid)
                    else:
                        ok, removed = False, None
                    if ok:
                        msg = (u'Đã xóa ghi nhớ: "{}"'.format(removed) if viet
                               else u'Forgot: "{}"'.format(removed))
                    else:
                        msg = (u'Không tìm thấy mục đó — gõ `/memory` để xem '
                               u'danh sách đánh số.' if viet else
                               u'No such item — type `/memory` to see the '
                               u'numbered list.')
                        icon, color = _ICON_WARNING, _ICON_AMBER
                elif sub.startswith(u'clear'):
                    n = assistant_memory.clear_facts(pid, everything=True)
                    msg = (u'Đã xóa toàn bộ {} ghi nhớ.'.format(n) if viet
                           else u'Cleared all {} remembered facts.'.format(n))
                    icon, color = _ICON_REFRESH, _ICON_SLATE
                else:
                    msg = (u'Lệnh memory: `/memory` · '
                           u'`/memory forget <số|nội dung>` · `/memory clear`'
                           if viet else
                           u'Memory commands: `/memory` · '
                           u'`/memory forget <number|text>` · `/memory clear`')
                    icon, color = _ICON_INFO, _ICON_SLATE

            # ── "what do you remember?" ───────────────────────────────────
            if msg is None:
                _shows = (u'what do you remember', u'what have you remembered',
                          u'what do you know about me', u'ban nho gi',
                          u'bạn nhớ gì', u'ban nho nhung gi',
                          u'bạn nhớ những gì', u'ban dang nho gi',
                          u'bạn đang nhớ gì')
                if low.rstrip(u'? .').strip() in _shows:
                    msg = assistant_memory.format_memory_report(pid, viet)
                    icon, color = _ICON_LIST, _ICON_SLATE

            # ── "remember that X" / "nhớ là X" / "từ nay X" ───────────────
            if msg is None:
                m2 = _MEM_SAVE_RX.match(text)
                if m2:
                    fact = (m2.group(1) or u'').strip()
                    fact = re.sub(r'^(?:that\s+|r[aằ]ng\s+|l[aà]\s+)', u'',
                                  fact, flags=re.IGNORECASE).strip(u' .!')
                    if fact:
                        _gl = (u'always', u'luôn', u'luon', u'every project',
                               u'all projects', u'mọi dự án', u'moi du an',
                               u'from now on', u'từ nay', u'tu nay',
                               u'prefer', u'mọi project', u'moi project',
                               u'mọi model', u'moi model')
                        scope = ('global' if any(k in low for k in _gl)
                                 else 'project')
                        ok, note = assistant_memory.add_fact(
                            fact, scope=scope, project_id=pid)
                        if ok and viet:
                            msg = u'Đã ghi nhớ ({}): {}'.format(
                                u'mọi dự án' if scope == 'global'
                                else u'dự án này', fact)
                        elif ok:
                            msg = note
                        else:
                            msg = note
                            icon, color = _ICON_WARNING, _ICON_AMBER

            if msg is None:
                return False

            self._log_activity(u'Memory: {}'.format(text))

            def _show(_m=msg, _i=icon, _c=color):
                self._hide_typing_indicator()
                self._append_bot_message(_m, icon=_i, icon_color=_c)
                self._add_to_history('assistant', _m)
                self._set_busy(False)
            self.Dispatcher.Invoke(Action(_show))
            return True
        except Exception as ex:
            logger.debug('_try_memory_command error: {}'.format(ex))
            return False

    # ─── Self-study: /train command ───────────────────────────────────────────

    def _try_train_command(self, raw):
        """Deterministic `/train` handling. WORKER THREAD.

        `/train`      → show the self-study dataset stats + readiness.
        `/train now`  → launch the external LoRA fine-tune (detached process).
        Weight training runs OUTSIDE Revit (CPython+GPU, tools/train/), so this
        only reports status and kicks off that job. Returns True when handled.
        """
        try:
            text = (raw or u'').strip()
            low = text.lower()
            m = re.match(r'^/train(?:\s+(.*))?$', low, re.S)
            if not m:
                return False
            sub = (m.group(1) or u'').strip()
            viet = _is_viet_text(text)

            from Intelligence.learning import trainer as _trainer
            st = _trainer.dataset_stats()
            count = st.get('count', 0)
            last = _trainer.last_train()

            icon, color = _ICON_INFO, _ICON_SLATE
            if sub in (u'exemplars', u'exemplar', u'portable'):
                # Rebuild the PORTABLE few-shot layer (no GPU): distils teacher
                # data into the git-tracked teacher_exemplars.json so a plain
                # local model answers in the taught style on any machine.
                try:
                    from Intelligence.learning import exemplars as _ex
                    res = _ex.promote_from_dataset()
                    n = res.get('count', 0)
                    if res.get('status') == 'ok':
                        msg = (u'Đã dựng {} ví dụ mẫu portable vào '
                               u'`teacher_exemplars.json`. Commit file này để '
                               u'mọi máy dùng model local đều trả lời theo phong '
                               u'cách đã dạy — không cần GPU/train.'.format(n)
                               if viet else
                               u'Built {} portable exemplars into '
                               u'`teacher_exemplars.json`. Commit it so every '
                               u'machine\'s local model answers in the taught '
                               u'style — no GPU/training needed.'.format(n))
                        icon, color = _ICON_SYNC, _ICON_SLATE
                    else:
                        msg = (u'Không dựng được exemplars: {}'.format(
                            res.get('status')) if viet else
                            u'Could not build exemplars: {}'.format(
                                res.get('status')))
                        icon, color = _ICON_WARNING, _ICON_AMBER
                except Exception as _ex_err:
                    msg = u'Could not build exemplars: {}'.format(_ex_err)
                    icon, color = _ICON_WARNING, _ICON_AMBER
            elif sub in (u'now', u'chay', u'chạy', u'start'):
                ok, note = _trainer.launch()
                if ok:
                    msg = (u'Đã khởi động huấn luyện nền ({} mẫu). Quá trình '
                           u'chạy ngoài Revit và có thể mất hàng giờ; xem '
                           u'`tools/train/README.md`.'.format(count) if viet else
                           u'Started background training on {} examples. It runs '
                           u'outside Revit and can take hours; see '
                           u'`tools/train/README.md`.'.format(count))
                    icon, color = _ICON_SYNC, _ICON_SLATE
                else:
                    msg = (u'Không khởi động được: {}'.format(note) if viet
                           else u'Could not launch training: {}'.format(note))
                    icon, color = _ICON_WARNING, _ICON_AMBER
            else:
                by_src = u', '.join(u'{}: {}'.format(k, v)
                                    for k, v in sorted(
                                        (st.get('by_source') or {}).items()))
                last_line = (u'Lần cuối: {} ({} mẫu)'.format(
                    last.get('trained_at'), last.get('examples'))
                    if last else (u'Chưa huấn luyện lần nào' if viet
                                  else u'Never trained yet'))
                if viet:
                    msg = (u'**Tự học — dữ liệu huấn luyện**\n\n'
                           u'- Tổng số mẫu: **{}**\n- Nguồn: {}\n- {}\n\n'
                           u'Gõ `/train now` để chạy fine-tune nền (cần GPU, '
                           u'chạy ngoài Revit).'.format(
                               count, by_src or u'(trống)', last_line))
                else:
                    msg = (u'**Self-study — training data**\n\n'
                           u'- Total examples: **{}**\n- Sources: {}\n- {}\n\n'
                           u'Type `/train now` to run a background fine-tune '
                           u'(needs a GPU, runs outside Revit).'.format(
                               count, by_src or u'(empty)', last_line))

            self._log_activity(u'Train: {}'.format(text))

            def _show(_m=msg, _i=icon, _c=color):
                self._hide_typing_indicator()
                self._append_bot_message(_m, icon=_i, icon_color=_c)
                self._add_to_history('assistant', _m)
                self._set_busy(False)
            self.Dispatcher.Invoke(Action(_show))
            return True
        except Exception as ex:
            logger.debug('_try_train_command error: {}'.format(ex))
            return False

    def _try_skills_command(self, raw):
        """Install / update skills from a GitHub repo. WORKER THREAD.

        Handles both the explicit command family (`/skills`,
        `/skills install <url>`, `/skills update`, `/skills remove <id>`) and
        the natural request ("cài skill từ https://github.com/owner/repo").
        Deterministic — no LLM round-trip, so it works with no provider
        configured. Returns True when the request was fully handled here.
        """
        try:
            text = (raw or u'').strip()
            if not text:
                return False
            from Intelligence import skill_installer as installer
            parsed = installer.parse_skills_command(text)
            if not parsed:
                return False
            action, arg = parsed
            viet = _is_viet_text(text)

            if action == 'help':
                msg = (u'Lệnh skills: `/skills` · '
                       u'`/skills install <link GitHub>` · `/skills update` · '
                       u'`/skills remove <id>`\nHoặc chỉ cần nói: "cài skill '
                       u'từ https://github.com/owner/repo".' if viet else
                       u'Skills commands: `/skills` · '
                       u'`/skills install <github link>` · `/skills update` · '
                       u'`/skills remove <id>`\nOr just say: "install skills '
                       u'from https://github.com/owner/repo".')
                self._post_bot(msg, _ICON_INFO, _ICON_SLATE)
                return True

            if action == 'list':
                msg = installer.format_inventory(
                    installer.list_installed(), viet)
                self._post_bot(msg, _ICON_LIST, _ICON_SLATE)
                return True

            if action == 'remove':
                if not arg:
                    self._post_bot(
                        u'Cần id skill: `/skills remove <id>`' if viet else
                        u'Missing skill id: `/skills remove <id>`',
                        _ICON_WARNING, _ICON_AMBER)
                    return True
                ok, note = installer.remove_installed(arg)
                self._rescan_skills()
                self._post_bot(note,
                               _ICON_SUCCESS if ok else _ICON_WARNING,
                               _ICON_GREEN if ok else _ICON_AMBER)
                return True

            # ── install / update: network work, so say so first ───────────
            if action == 'install':
                notice = (u'Đang tải skill từ {}…'.format(arg.get('url'))
                          if viet else
                          u'Downloading skills from {}…'.format(arg.get('url')))
            else:
                notice = (u'Đang cập nhật các skill đã cài…' if viet else
                          u'Updating installed skills…')
            self._log_activity(u'Skills: {}'.format(text))

            def _notice(_m=notice):
                self._append_bot_message(_m, icon=_ICON_SYNC,
                                         icon_color=_ICON_SLATE)
            self.Dispatcher.Invoke(Action(_notice))

            if action == 'install':
                report = installer.install_from_github(arg)
            else:
                report = installer.update_all()

            self._rescan_skills()
            self._post_bot(installer.format_report(report, viet),
                           _ICON_SUCCESS if (report.get('installed') or
                                             report.get('updated'))
                           else _ICON_WARNING,
                           _ICON_GREEN if (report.get('installed') or
                                           report.get('updated'))
                           else _ICON_AMBER)
            return True
        except Exception as ex:
            logger.debug('_try_skills_command error: {}'.format(ex))
            return False

    def _rescan_skills(self):
        """Re-read the skill folders so new skills reach /slash immediately."""
        try:
            from Intelligence.skills_engine import get_skills_engine
            get_skills_engine().scan()
        except Exception as ex:
            logger.debug('_rescan_skills error: {}'.format(ex))

    def _post_bot(self, msg, icon, color):
        """Finish a deterministic turn: show `msg`, record it, release busy."""
        def _show():
            self._hide_typing_indicator()
            self._append_bot_message(msg, icon=icon, icon_color=color)
            self._add_to_history('assistant', msg)
            self._set_busy(False)
        self.Dispatcher.Invoke(Action(_show))

    def _process_input(self):
        """Read input (+ any attachments), dispatch to NLP or keyword fallback."""
        try:
            raw = self.chat_input.Text.strip()
            attached = list(self._attached_files)   # snapshot

            # Read-and-clear: the echo suppression set by "Try again" is a
            # ONE-SHOT for this call. Left on the instance it would survive
            # every early return below (empty input, busy queue) and silently
            # swallow the next real message the user typed.
            suppress_echo = self._suppress_user_echo
            self._suppress_user_echo = False

            # Must have text OR attachments
            if not raw and not attached:
                self._retry_target_row = None
                return

            # Language of THIS turn steers every string the assistant emits
            # while handling it, including ones with no text of their own
            # (tool cards, confirm cards, background notices).
            _note_user_language(raw)

            # ── Concurrency: queue instead of reject ──────────────────────────
            # Runs BEFORE the slash parse below — a queued "/skill" message
            # must not overwrite _forced_skill_id while the current request
            # is still routing on the worker thread.
            if self._busy:
                if attached:
                    self._append_bot_message(
                        u"Still working on the previous request — attachments "
                        u"can be sent again once it finishes.",
                        icon=_ICON_SYNC, icon_color=_ICON_SLATE)
                elif raw:
                    self._queue_input(raw)
                return

            # A new send supersedes any quick-reply chips still on screen.
            self._remove_quick_replies()
            # ...and starts a fresh compact tool group, so this turn's cards
            # never fold into the previous turn's summary row.
            self._reset_tool_group()

            # ── Slash-skill invocation: "/skill-id [rest of message]" ─────────
            # The skill id must exist in the registry; anything else is sent
            # through unchanged (e.g. "/абв" or a plain path-like string).
            self._close_skills_popup()
            self._forced_skill_id = None
            self._forced_skill_args = u""
            route_text = raw
            m = re.match(r'^/([\w\-]+)(?:\s+(.*))?$', raw, re.S) if raw else None
            if m:
                try:
                    from Intelligence.skills_engine import get_skills_engine
                    _cat = get_skills_engine().get_catalog(enabled_only=False)
                    if any(s['id'] == m.group(1) for s in _cat):
                        self._forced_skill_id = m.group(1)
                        route_text = (m.group(2) or u"").strip()
                        # Keep the user's own words (pre-boilerplate) so the
                        # router can tell a scan request from a fix request.
                        self._forced_skill_args = route_text
                        if not route_text:
                            # Slash-only invocation: the literal "/skill-id"
                            # reads as gibberish to the model and it guesses a
                            # target (e.g. renaming walls). Send an explicit
                            # context-first instruction instead.
                            # Phrased as "follow the workflow in your system
                            # prompt" — both the native path and the legacy
                            # JSON-intent path inject the skill body there.
                            # The old "Apply the '<id>' playbook" wording made
                            # JSON-intent models answer {"intent":
                            # "apply_playbook"} → "Tool does not exist".
                            #
                            # THREE modes, because a bare "/skill" carries no
                            # target at all:
                            #  * reference  (no `tools:` — iso19650-naming,
                            #    bep-guideline): told to "start scanning
                            #    immediately" they have nothing to scan, and a
                            #    small local model filled the gap by replaying
                            #    the previous turn's tool calls.
                            #  * read-only  (qa-checklist, lod-standard,
                            #    shared-coordinates): nothing to lose, scan now.
                            #  * modifying  (17 of 25 skills): plan first.
                            #    "/annotation-standard" — a CONVENTION document
                            #    that declares create_dimension/tag_* so a real
                            #    "dim mặt bằng này" has tools — used to land on
                            #    the read-only wording's "act NOW, do NOT ask
                            #    about scope" and the model went dimensioning
                            #    every floor plan in the project unprompted.
                            #    Declaring tools says what a skill CAN reach,
                            #    never that a bare invocation should fire them.
                            _eng = get_skills_engine()
                            _sid = m.group(1)
                            if _eng.is_reference_skill(_sid):
                                route_text = (
                                    u"Answer from the '{}' reference (the "
                                    u"Active skill in your system prompt) "
                                    u"for THIS message only. Summarise the "
                                    u"rules it defines and ask me for the "
                                    u"input it needs (file/sheet list, "
                                    u"project code...). Do NOT call any "
                                    u"Revit tool and do NOT repeat or "
                                    u"re-run any earlier request in this "
                                    u"conversation."
                                ).format(_sid)
                            elif _eng.modifies_model(_sid):
                                route_text = (
                                    u"Follow the '{}' workflow (the Active "
                                    u"skill in your system prompt) for THIS "
                                    u"message. I gave you NO target, so run "
                                    u"its READ/diagnostic steps only — over "
                                    u"the ENTIRE project unless the workflow "
                                    u"says otherwise — then STOP and present "
                                    u"the plan: what you would change, on "
                                    u"which targets, how many, and anything "
                                    u"you still need me to decide. Do NOT "
                                    u"create, modify, delete, rename, tag, "
                                    u"dimension, override or export anything "
                                    u"in this turn; wait for my confirmation. "
                                    u"Ignore every earlier request in this "
                                    u"conversation — do not repeat it."
                                ).format(_sid)
                            else:
                                route_text = (
                                    u"Follow the '{}' workflow (the Active "
                                    u"skill in your system prompt) NOW, "
                                    u"using its DEFAULT scope. This workflow "
                                    u"only reads the model, so start "
                                    u"scanning the ENTIRE project "
                                    u"immediately — do NOT ask about scope. "
                                    u"Ignore every earlier request in this "
                                    u"conversation — do not repeat it."
                                ).format(_sid)
                except Exception:
                    pass

            # Record in command history
            if raw:
                if not self._input_history or self._input_history[-1] != raw:
                    self._input_history.append(raw)
                    if len(self._input_history) > 50:
                        self._input_history.pop(0)
                self._history_index = -1
                self._current_input_temp = ""

            self.chat_input.Text = ""
            self._last_raw = raw or u"[attached documents]"

            # ── Show user message in chat ──────────────────────────────────────
            # A "Try again" re-sends the SAME prompt, so the message is already
            # on screen — echoing it again would grow a wall of duplicates.
            display_text     = raw
            attachment_note  = summarize_attachments(attached) if attached else None
            if not suppress_echo:
                # A normal send also drops any stale retry target, so an
                # abandoned "Try again" can never capture an unrelated answer.
                self._retry_target_row = None
                self._append_user_message(display_text,
                                          attachment_note=attachment_note)
            # History/LLM context still gets plain text — no raw icon glyph.
            # A retry records the prompt again on purpose: the conversation
            # must keep alternating user/assistant roles (Anthropic rejects two
            # assistant turns in a row), and "…user X, assistant Y, user X" is
            # exactly the shape that asks the model to answer X afresh.
            history_text = (u"{}\n[attached: {}]".format(display_text, attachment_note)
                            if attachment_note else display_text)
            self._add_to_history("user", history_text)

            # Slash-forced skill → show the activation chip right away
            if self._forced_skill_id:
                try:
                    self._append_skill_chips([self._forced_skill_id])
                except Exception:
                    pass

            # ── Clear attachments from UI after sending ────────────────────────
            if attached:
                self._attached_files = []
                try:
                    self.attachment_chips_panel.Children.Clear()
                    self._refresh_attachment_panel()
                except Exception:
                    pass

            # Lock UI + show the typing indicator IMMEDIATELY (set_busy does
            # both). Everything further down — PDF text extraction, the
            # Ollama HTTP probe, Revit-DB fast-answer collectors, NLU catalog
            # scoring — takes tens of ms to SECONDS. It used to run right
            # here on the UI thread, so every Enter press froze the window
            # until routing finished. It now runs on a routing worker thread;
            # only the results marshal back onto the dispatcher.
            self._set_busy(True)

            _rid = getattr(self, '_request_id', 0)

            def _route(_rt=route_text):
                try:
                    # Archive attachments into the project's dated folder and
                    # journal the request — on the worker thread so a big PDF
                    # copy never freezes the UI.
                    _files = attached
                    if _files:
                        try:
                            from config.project_store import ProjectStore
                            _ps = ProjectStore()
                            _pid = _ps.get_active_project_id()
                            _files = _ps.archive_attachments(_files, _pid)
                            _ps.append_activity(
                                u"Attached ({}): {}".format(
                                    len(_files),
                                    u", ".join(os.path.basename(x)
                                               for x in _files)), _pid)
                        except Exception:
                            _files = attached
                    _req = _rt if _rt else u"[attached documents]"
                    _skill_note = (u"  _(skill: /{})_".format(
                        self._forced_skill_id)
                        if self._forced_skill_id else u"")
                    self._log_activity(u"User: {}{}".format(
                        _req, _skill_note))
                    self._route_input(_rt, _files)
                except Exception as ex:
                    # Used to hide the dots and release busy while printing
                    # NOTHING: the user's message sat in the transcript and the
                    # assistant simply never answered.
                    if self._cancelled():
                        self._finish_cancelled(_rid)
                    else:
                        self._report_error(u"_route_input", ex, _rid)
                finally:
                    # Belt and braces: every terminal path releases busy before
                    # returning, so this normally does nothing — but if one ever
                    # leaks, the window must not stay locked forever.
                    # Deliberately does NOT claim the turn: releasing busy twice
                    # is harmless, whereas claiming would suppress a message a
                    # legitimate finisher still wanted to post.
                    if self._busy and _rid == getattr(self, '_request_id', 0):
                        self._ui_invoke(lambda: self._set_busy(False))

            rt = Thread(ThreadStart(_route))
            rt.IsBackground = True
            rt.SetApartmentState(ApartmentState.STA)
            rt.Start()

        except Exception as ex:
            # UI thread — Dispatcher.Invoke from here executes inline, so
            # _report_error is safe and cannot deadlock.
            self._report_error(u"_process_input", ex)

    def _provider_unreachable_note(self, viet=True):
        """Message for 'a provider is selected but its health probe failed'.

        Returns u"" when there genuinely is no provider configured — the stock
        setup guidance is the right answer then. WORKER THREAD (does HTTP via
        check_health, same probe the gate above already ran).
        """
        try:
            from Intelligence.llm_router import LLMRouter
            provider = LLMRouter().get_active_provider()
            if provider is None:
                return u""
            label = (getattr(provider, 'DISPLAY_NAME', None)
                     or getattr(provider, 'NAME', u'AI'))
            err = u""
            try:
                err = provider.get_last_error() or u""
            except Exception:
                err = u""
            if viet:
                msg = (u"**{}** đang được chọn nhưng không kết nối được, nên "
                       u"tôi phải trả lời ở chế độ offline (chỉ hiểu lệnh cụ "
                       u"thể).\n\nKiểm tra: API key còn hạn/còn credit, mạng "
                       u"công ty hoặc proxy có chặn không, rồi bấm **Cài đặt → "
                       u"LLMs Setting** để thử lại kết nối.").format(label)
            else:
                msg = (u"**{}** is selected but unreachable, so I answered in "
                       u"offline mode (specific commands only).\n\nCheck the "
                       u"API key (valid, still has credit) and whether a "
                       u"corporate proxy is blocking it, then re-test the "
                       u"connection in **Settings → LLMs Setting**.").format(label)
            if err:
                msg += u"\n\n`{}`".format(err[:200])
            return msg
        except Exception as ex:
            logger.debug(u"_provider_unreachable_note error: {}".format(_exc_text(ex)))
            return u""

    def _link_cache_dir(self):
        """Where downloaded links are stored. Project attachments folder when
        there is an active project, otherwise a temp folder — never the user's
        Documents root."""
        try:
            from config.project_store import ProjectStore
            ps = ProjectStore()
            d = ps.attachments_dir(ps.get_active_project_id())
            if d:
                return d
        except Exception:
            pass
        try:
            import tempfile
            d = os.path.join(tempfile.gettempdir(), 'T3LabAssistant', 'links')
            if not os.path.isdir(d):
                os.makedirs(d)
            return d
        except Exception:
            return None

    def _resolve_message_links(self, raw, attached):
        """Fetch every link in `raw`, append the readable files to `attached`
        (in place) and return the context block for the rest. WORKER THREAD.

        Returns u"" when the message carries no links, so an ordinary turn
        pays nothing for this.
        """
        try:
            links = link_reader.find_links(raw)
            if not links:
                return u""

            viet = _is_viet_text(raw)
            self._safe_update_typing_text(
                u"● ● ●  Đang đọc {} liên kết…".format(len(links)) if viet
                else u"● ● ●  Reading {} link(s)…".format(len(links)))

            res = link_reader.resolve(raw, self._link_cache_dir())

            added = []
            for path in res.get('files') or []:
                if path not in attached:
                    attached.append(path)
                    added.append(path)

            if added or res.get('notes'):
                self._log_activity(u"Links ({}): {}".format(
                    len(res.get('links') or []),
                    u", ".join(res.get('links') or [])))

            parts = []
            header = link_reader.source_header(res.get('links') or [])
            if header:
                parts.append(header)
            parts.extend(res.get('notes') or [])
            # Files are read by _build_attachment_context; only say WHICH ones
            # came from a link so the model can cite the source properly.
            if added:
                parts.append(u'[Đã tải về từ liên kết: {}]'.format(
                    u', '.join(os.path.basename(p) for p in added)))
            return u"\n\n".join(p for p in parts if p)
        except Exception as ex:
            logger.debug(u"_resolve_message_links error: {}".format(_exc_text(ex)))
            return u""

    def _build_attachment_context(self, raw, attached):
        """Build the attachment context string. WORKER THREAD.

        Large PDFs (more than a few chunks of text) are indexed into the
        active knowledge store and answered via retrieval — top-5 excerpts
        with file + page citations — instead of the legacy 12k-char
        truncate-and-stuff. Small PDFs and images keep the legacy path
        (full text / vision placeholder). Any failure degrades to the
        legacy behavior, never to an error.
        """
        pdfs = [p for p in attached if is_pdf(p)]
        if not pdfs or not HAS_KNOWLEDGE:
            return build_text_context(attached)
        try:
            store = get_active_store()
            if store is None:
                return build_text_context(attached)
            doc_ids, total_chunks, failed_pdfs = [], 0, []
            for path in pdfs:
                entry = store.index_file(path)
                if entry and entry.get('chunks'):
                    doc_ids.append(entry['doc_id'])
                    total_chunks += entry['chunks']
                else:
                    failed_pdfs.append(path)
            # Small docs fit whole — the model seeing everything beats
            # retrieval. ~3 chunks ≈ 2000 words ≈ the old 12k-char cap.
            if not doc_ids or total_chunks <= 3:
                return build_text_context(attached)

            # Semantic channel (bounded): vectorize fresh chunks for ~20s
            # max, then search hybrid. Degrades to BM25-only silently.
            embedder = None
            try:
                from Intelligence.knowledge.embeddings import get_default_embedder
                embedder = get_default_embedder()
                if embedder is not None and embedder.is_available():
                    store.embed_pending(embedder, budget_sec=20)
                else:
                    embedder = None
            except Exception:
                embedder = None

            query = raw or u"main content of the document"
            hits = store.search(query, top_k=5, embedder=embedder,
                                doc_ids=set(doc_ids))
            if not hits:
                return build_text_context(attached)

            parts = [u'=== Relevant excerpts from the attached documents ===']
            for i, hit in enumerate(hits):
                page_note = (u' — page {}'.format(hit['page'])
                             if hit.get('page') else u'')
                parts.append(u'[{}] {}{}:\n{}'.format(
                    i + 1, hit['file'], page_note, hit['text'][:900]))
            parts.append(
                u'=== End of excerpts (the full document is indexed; '
                u'answer from the excerpts, cite sources [n]) ===')

            # Images + unreadable PDFs still described the legacy way
            rest = [p for p in attached if (not is_pdf(p)) or p in failed_pdfs]
            if rest:
                extra = build_text_context(rest)
                if extra:
                    parts.append(extra)
            return u'\n\n'.join(parts)
        except Exception as ex:
            logger.debug(u"attachment RAG failed, legacy path: {}".format(_exc_text(ex)))
            return build_text_context(attached)

    def _run_knowledge_agent(self, raw, history):
        """Answer from the knowledge index with citations. WORKER THREAD.

        Streams through _stream_llm_turn (live bubble). Returns True when
        the request was fully handled; False lets _route_input fall through
        to the normal LLM path (no retrieval hits, or every provider mute).
        """
        try:
            from Intelligence.knowledge.knowledge_agent import (
                KnowledgeAgent, format_citation_line)
            from Intelligence.llm_router import LLMRouter

            embedder = None
            try:
                from Intelligence.knowledge.embeddings import get_default_embedder
                embedder = get_default_embedder()
                if embedder is not None and not embedder.is_available():
                    embedder = None
            except Exception:
                embedder = None

            router = LLMRouter()
            provider = router.get_active_provider()
            agent = KnowledgeAgent(embedder=embedder)

            # Same grounding as every other path. Remembered facts are project
            # conventions ("sheet prefix is WH-"), so a knowledge answer must
            # respect them too — this path only ever got the instructions half.
            _p_instr, _p_mem = self._project_prompt_blocks()
            proj_instructions = u"\n\n".join(
                [b for b in (_p_instr, _p_mem) if b])

            skills_block = u''
            try:
                _dec = getattr(self, '_agent_decision', None)
                if _dec and _dec.get('skill'):
                    from Intelligence.skills_engine import (
                        build_skills_block, get_skills_engine)
                    _ids = [_dec['skill']]
                    # explicit /slash invocation bypasses the agents: filter
                    if not _dec.get('skill_forced'):
                        _ids = get_skills_engine().filter_for_specialist(
                            _ids, 'knowledge')
                    skills_block = build_skills_block(_ids)
            except Exception:
                pass

            _rid = getattr(self, '_request_id', 0)
            if self._cancelled():
                self._finish_cancelled(_rid)
                return True          # MUST be True — see below

            def _chat(system_prompt, query):
                if self._cancelled():
                    return None
                return self._stream_llm_turn(
                    provider, router, list(history), system_prompt, query,
                    max_tokens=900)

            result = agent.answer(raw, history, _chat,
                                  project_instructions=proj_instructions,
                                  skills_block=skills_block,
                                  viet=_is_viet_text(raw))

            # Every cancel exit from this function returns True. Returning
            # False makes _route_input fall through to the normal LLM path,
            # i.e. it would start a brand-new request seconds after the user
            # pressed Stop.
            if self._cancelled():
                try:
                    self._ui_invoke(self._remove_stream_bubble)
                except Exception:
                    pass
                self._finish_cancelled(_rid)
                return True

            if result.get('status') != 'done':
                # remove any half-made bubble before falling through
                if result.get('status') == 'llm_failed':
                    def _cleanup():
                        self._remove_stream_bubble()
                    try:
                        self.Dispatcher.Invoke(Action(_cleanup))
                    except Exception:
                        pass
                return False

            text = result['text']
            shown = text + format_citation_line(result.get('citations'))

            def _done():
                self._hide_typing_indicator()
                if self._stream_tb is not None:
                    self._finalize_stream_bubble(shown)
                    self._clear_stream_refs()
                else:
                    self._append_bot_message(shown)
                self._add_to_history("assistant", text)
                self._set_busy(False)
            self.Dispatcher.Invoke(Action(_done))
            return True
        except Exception as ex:
            # Falling through to the legacy LLM path is the DESIGNED
            # degradation (the user still gets an answer), so no bubble here —
            # but a real crash must not be invisible at debug level.
            logger.error("_run_knowledge_agent error: {}".format(_exc_text(ex)))
            return False

    def _run_comment_agent(self, pdf_path, history):
        """PDF markup-comment workflow. WORKER THREAD.

        Extract annotations → trace sheet/model qua MCP → propose per item
        → render report card with Run/Note/Skip buttons. Returns True when
        handled; False (e.g. no annotations) lets the normal attachment
        path analyze the PDF as a document instead.
        """
        try:
            from Intelligence.comments.comment_agent import CommentAgent
            from Intelligence.llm_router import LLMRouter
            try:
                from core.server import get_t3labai_server
                srv = get_t3labai_server()
            except Exception:
                return False

            router = LLMRouter()
            provider = router.get_active_provider()

            skills_block = u''
            try:
                from Intelligence.skills_engine import build_skills_block
                skills_block = build_skills_block(
                    ['comment-resolution-playbook'])
            except Exception:
                pass

            agent = CommentAgent()
            _rid = getattr(self, '_request_id', 0)
            if self._cancelled():
                self._finish_cancelled(_rid)
                return True

            # Resolving a markup comment usually means applying THIS project's
            # naming/annotation conventions, so the comment agent needs the
            # same project grounding every other path gets. extra_context is
            # already appended to its BỐI CẢNH block.
            _p_instr, _p_mem = self._project_prompt_blocks()
            _extra = u"\n\n".join([b for b in (_p_instr, _p_mem) if b])

            report = agent.analyze(
                pdf_path, srv._execute_tool, provider, router,
                progress_cb=self._safe_update_typing_text,
                skills_block=skills_block,
                extra_context=_extra)

            # True, not False: False falls through to normal document analysis,
            # which would start fresh work right after the user pressed Stop.
            if self._cancelled():
                self._finish_cancelled(_rid)
                return True

            if report.get('error') == 'no_annotations':
                return False   # analyze as a normal document instead

            self._comment_agent = agent
            self._comment_report = report

            def _done():
                try:
                    self._hide_typing_indicator()
                    rendered = False
                    try:
                        from GUI.AssistantCards import build_comment_report_card
                        card = build_comment_report_card(
                            report,
                            on_run=lambda item, setter:
                                self._execute_comment_item(item, setter, 'run'),
                            on_note=lambda item, setter:
                                self._execute_comment_item(item, setter, 'note'),
                            on_skip=lambda item, setter:
                                self._skip_comment_item(item, setter),
                            viet=_ui_viet())
                        self.chat_history_panel.Children.Add(card)
                        self._scroll_to_bottom()
                        rendered = True
                    except Exception as cex:
                        logger.debug(u"comment card render failed: {}".format(_exc_text(cex)))
                    md = agent.report_to_markdown(report)
                    if not rendered:
                        self._append_bot_message(md)
                    self._add_to_history("assistant", md)
                    if report.get('needs_switch'):
                        self._append_bot_message(
                            u"This sheet is not in the active model — "
                            u"ask me to \"switch to model ...\" first, "
                            u"then press Run.",
                            icon=_ICON_SYNC, icon_color=_ICON_SLATE)
                    self._set_busy(False)
                except Exception as dex:
                    self._report_error(
                        u"comment report render", dex,
                        hint=u"The comment report could not be displayed.")
            self.Dispatcher.Invoke(Action(_done))
            return True
        except Exception as ex:
            # Same designed degradation as _run_knowledge_agent — the PDF is
            # still analysed as a plain document — but log it visibly.
            logger.error("_run_comment_agent error: {}".format(_exc_text(ex)))
            return False

    def _skip_comment_item(self, item, setter):
        """Skip button on a comment row — mark only. UI THREAD."""
        try:
            _v = _ui_viet()
            item['status'] = 'skipped'
            setter(u"Đã bỏ qua" if _v else u"Skipped", False)
        except Exception:
            pass

    def _execute_comment_item(self, item, setter, mode):
        """Run/Note button on a comment row. UI THREAD entry — spawns the
        standard revit_action specialist on a worker thread."""
        try:
            _v = _ui_viet()
            if self._busy:
                setter(u"Đang bận — chờ yêu cầu hiện tại" if _v
                       else u"Busy — wait for the current request", False)
                return
            agent = getattr(self, '_comment_agent', None)
            report = getattr(self, '_comment_report', None)
            if agent is None or report is None:
                setter(u"Báo cáo này không còn hiệu lực" if _v
                       else u"This report is no longer valid", False)
                return
            instruction = (agent.build_run_instruction(item, report)
                           if mode == 'run'
                           else agent.build_note_instruction(item, report))
            setter(u"Đang chạy..." if _v else u"Running...", False)
            self._set_busy(True)

            def _work():
                handled = False
                try:
                    from Intelligence.llm_router import LLMRouter
                    provider = LLMRouter().get_active_provider()
                    spec = get_spec('revit_action') if HAS_SPECIALISTS else None
                    handled = self._run_native_agent(
                        provider, list(self._conversation_history),
                        instruction, spec=spec)
                except Exception as wex:
                    logger.debug(u"comment exec error: {}".format(_exc_text(wex)))

                def _after():
                    try:
                        if handled:
                            item['status'] = ('done' if mode == 'run'
                                              else 'noted')
                            if mode == 'run':
                                setter(u"Xong" if _v else u"Done", True)
                            else:
                                setter(u"Đã ghi chú" if _v else u"Noted", True)
                        else:
                            setter(u"Thất bại — thử lại" if _v
                                   else u"Failed — try again", False)
                            self._set_busy(False)
                    except Exception:
                        pass
                try:
                    self.Dispatcher.Invoke(Action(_after))
                except Exception:
                    pass
            _ct = Thread(ThreadStart(_work))
            _ct.IsBackground = True
            _ct.SetApartmentState(ApartmentState.STA)
            _ct.Start()
        except Exception as ex:
            logger.debug(u"_execute_comment_item error: {}".format(_exc_text(ex)))

    def _route_input(self, raw, attached):
        """Classify + dispatch one user request. WORKER THREAD.

        Runs the whole routing ladder (RAG context build → fast Revit-DB
        answer → learned patterns → offline NLU → LLM turn) off the UI
        thread, so pressing Enter stays instant no matter how heavy the
        request is. All UI feedback and _execute_result calls marshal back
        through the Dispatcher.
        """
        try:
            attached = list(attached or [])
            # ── If attachments present and no tool-like text, go straight to RAG ─
            has_attach = bool(attached) and HAS_RAG

            # ── Persistent memory (deterministic, no LLM) ──────────────────
            # /memory commands and explicit "remember that ..." saves are
            # answered instantly here — before learned patterns or NLU can
            # hijack the wording.
            if not attached and raw and self._try_memory_command(raw):
                return

            # ── Self-study /train (deterministic, no LLM) ──────────────────
            # Shows the curated training-data stats, or launches the external
            # fine-tune. Answered here so it never reaches the model.
            if not attached and raw and self._try_train_command(raw):
                return

            # ── "Which model are you using?" (deterministic, no LLM) ───────
            # The assistant knows its own provider and model id for certain,
            # so this is never a question for the LLM. Left to it, "model bạn
            # đang dùng là gì" got read as a question about the .rvt and came
            # back "Tôi chưa mở bất kỳ model Revit nào" — wrong noun, and an
            # ungrounded claim about the open document on top.
            if not attached and raw and self._try_which_model(raw):
                return

            # ── Skill packs from GitHub (deterministic, no LLM) ────────────
            # "/skills …" and "cài skill từ <repo link>" install or refresh
            # Claude-format skills. Answered here so it works offline-ish
            # (network for the download only) and can never be re-read as a
            # Revit command by the NLU.
            if not attached and raw and self._try_skills_command(raw):
                return

            # ── Links in the message → real files ──────────────────────────
            # A pasted URL / UNC path / local path is fetched here and pushed
            # onto `attached`, so everything downstream treats it exactly like
            # a dropped file: PDFs reach the knowledge index and the comment
            # agent, images reach the vision blocks, .json/.txt/.md reach the
            # text context. Runs AFTER the skills command above, which owns
            # "cài skill từ <github link>" and must keep its own URL.
            _link_ctx = u""
            if HAS_LINKS and raw:
                _link_ctx = self._resolve_message_links(raw, attached)
                has_attach = bool(attached) and HAS_RAG

            # ── PDF markup-comment workflow (R4) ───────────────────────────
            # Route to the CommentAgent when the user asks about comments
            # (cmt/markup/bluebeam keywords) with a PDF attached, or drops
            # an annotated PDF without any text. No annotations found →
            # falls through to the normal document analysis.
            if HAS_AGENTS and has_attach:
                try:
                    _pdfs = [p for p in attached if is_pdf(p)]
                    if _pdfs:
                        from Intelligence.comments import pdf_annots as _pa
                        _annotated = [p for p in _pdfs
                                      if _pa.has_annotations(p)]
                        _kw_dec = AgentDispatcher().classify(
                            raw, allow_llm=False)
                        _wants = (_kw_dec.get('specialist') == 'comment'
                                  or (_annotated and not (raw or u'').strip()))
                        if _annotated and _wants:
                            if self._run_comment_agent(
                                    _annotated[0],
                                    list(self._conversation_history[:-1])):
                                return
                except Exception as _cm_ex:
                    logger.debug(u"comment route error: {}".format(_exc_text(_cm_ex)))

            if has_attach and not raw:
                # No text — summarise the documents
                raw = u"Analyze and summarize the attached document."

            # Build context-enriched prompt for NLP / Claude.
            # RAG v2: large attached PDFs are indexed and only the top-k
            # relevant excerpts (with page citations) are injected — small
            # files and images keep the legacy full-text/vision path.
            rag_context = ''
            if has_attach:
                rag_context = self._build_attachment_context(raw, attached)
            # Folder listings and "couldn't read that link" notes carry no file
            # of their own, so they ride in front of the attachment context.
            if _link_ctx:
                rag_context = (u"{}\n\n{}".format(_link_ctx, rag_context)
                               if rag_context else _link_ctx)

            # For NLP routing we use ONLY the raw user text. Prepending the
            # ContextScout model summary here poisoned the offline NLU and
            # keyword scoring (its words — "sheet", "view", "wall"... — leak
            # into intent triggers) and duplicated context the LLM already
            # receives via the system prompt in do_nlp().
            captured = raw

            # One language analysis for the whole turn: the dispatcher, the
            # graph planner and the per-turn prompt hint all read it, so the
            # normalisation and entity tables run once instead of three times
            # and every stage agrees about what the sentence said.
            _utterance = None
            if HAS_LANGUAGE and raw:
                try:
                    _utterance = _lang_analyzer.analyze(raw)
                except Exception as _ln_ex:
                    logger.debug("language analysis error: {}".format(_ln_ex))

            history  = list(self._conversation_history[:-1])

            use_local        = HAS_NLP and has_local_llm()
            use_claude       = HAS_NLP and has_api_key()   # True for any configured provider
            _active_provider = get_active_provider_name()  # "claude" | "openai" | "ollama"

            # An explicit /slash skill invocation must reach the agent with
            # its playbook — the learned-pattern and NLU stages would hijack
            # it (e.g. "/english-spellcheck" text matches the deterministic
            # check_spelling triggers and the skill never runs).
            _skill_forced = bool(getattr(self, '_forced_skill_id', None))

            # EXCEPTION — /english-spellcheck SCAN requests. The agent
            # playbook can only ever see one ai_element_filter page (50 of
            # potentially thousands of notes) and then reports fake
            # full-project coverage ("4495 scanned, 0 errors" after reading
            # 50). Scans therefore go to the deterministic batch pipeline
            # (_run_spellcheck), which reads EVERY string. Explicit
            # fix/apply wording still reaches the agent, which owns
            # set_parameter / rename_element.
            if _skill_forced and self._forced_skill_id == 'english-spellcheck':
                _args = getattr(self, '_forced_skill_args', u'') or u''
                _fix_req = routing.wants_spellcheck_fix(_args)
                if not _fix_req:
                    def _run_spell():
                        self._execute_result({'intent': 'check_spelling',
                                              'params': {}})
                    self.Dispatcher.Invoke(Action(_run_spell))
                    return

            # ── Context carryover (Claude-style continuation) ─────────────────
            # When the assistant just asked a clarifying question, a short
            # reply ("1", "entire project", "ok"...) is the ANSWER to that
            # question — it must continue the same task with the same skill/
            # specialist and full history, not be re-routed from scratch
            # (where it matches nothing and the model loses the thread).
            _prev_dec = getattr(self, '_prev_agent_decision', None)
            _continuation = False
            if not _skill_forced and not has_attach and _prev_dec:
                try:
                    _last_bot = u''
                    for _h in reversed(history):
                        if _h.get('role') == 'assistant':
                            _last_bot = u'{}'.format(_h.get('content') or u'')
                            break
                    # An input that independently matches a command pattern
                    # (action verb, tô/bôi color phrase, export/build/QA
                    # keywords...) is a NEW command even right after a real
                    # clarifying question — "tô vàng sàn" typed after "tô đỏ
                    # tường" finished must route fresh; riding the old thread
                    # here replayed the completed red-walls task.
                    _fresh_kw = False
                    if HAS_AGENTS:
                        _fresh = AgentDispatcher().classify(
                            raw, allow_llm=False)
                        _fresh_kw = bool(_fresh
                                         and _fresh.get('source') == 'keyword')
                    _continuation = routing.is_continuation(
                        _last_bot, raw, _prev_dec, fresh_keyword_hit=_fresh_kw)
                except Exception:
                    _continuation = False
            if _continuation:
                logger.debug("carryover: continuing {} / {}".format(
                    _prev_dec.get('specialist'), _prev_dec.get('skill')))

            # ── 1/2. Deterministic stages: NLU first, learned patterns second ─
            # find_learned_match() used to run BEFORE the NLU and take the turn
            # outright on a Jaccard >= 0.8 phrasing match. That let a
            # remembered mapping outrank a confident read of the real tool
            # catalog — and learned mappings go stale, so a phrase learned for
            # a tool that has since been renamed kept winning. The NLU is
            # computed first now and routing.learned_pattern_wins() arbitrates.
            nlu_result = None
            learned = None
            _deterministic = (HAS_NLP and not has_attach and not _skill_forced
                              and not _continuation)
            if _deterministic:
                nlu_result = parse_command_nlu(captured, history)
                learned = find_learned_match(raw)

            if learned and routing.learned_pattern_wins(learned, nlu_result,
                                                        raw=raw):
                def _run_learned(_r=learned):
                    self._execute_result(_r)
                self.Dispatcher.Invoke(Action(_run_learned))
                return

            if _deterministic and nlu_result \
                    and nlu_result.get("intent") not in (None, "unknown"):
                # _authoritative = answered from the real tool catalog
                # (capability questions, ambiguity clarifications) — the
                # LLM must not get a chance to override it with a guess.
                # _instant = pure small talk (greeting, thanks, "how are you"...)
                # answered from the canned reply even with a provider online —
                # skipping the model here is the biggest single latency win on a
                # slow local model. Emotional/complaint chat is NOT _instant and
                # still flows to the LLM below for a warmer answer.
                if nlu_result["intent"] not in ("chat", "help") \
                        or nlu_result.get("_authoritative") \
                        or nlu_result.get("_instant") \
                        or not (use_local or use_claude):
                    # A provider that IS configured but failed its health probe
                    # lands here and answers from the offline canned replies,
                    # whose text tells the user to "connect an AI in Settings"
                    # — wrong and baffling when they just did. Say what really
                    # happened instead.
                    if nlu_result["intent"] in ("chat", "help") \
                            and not (use_local or use_claude):
                        _note = self._provider_unreachable_note(
                            viet=_is_viet_text(raw))
                        if _note:
                            nlu_result = dict(nlu_result)
                            nlu_result["message"] = _note
                    def _run_nlu(_r=nlu_result):
                        self._execute_result(_r)
                    self.Dispatcher.Invoke(Action(_run_nlu))
                    return

            # ── 2.5 Specialist dispatch (multi-agent layer) ────────────────
            # Keyword stage always; one tiny LLM classify call only for
            # non-conversational messages the keywords couldn't place.
            # Anything ambiguous falls through to the unchanged legacy
            # path. Kill switch: agents.multi_agent.
            self._agent_decision = None
            if HAS_AGENTS and not has_attach and (use_local or use_claude):
                try:
                    from config.settings import get_settings as _get_settings
                    _settings = _get_settings()
                    _multi_on = _settings.is_multi_agent_enabled()
                    _llm_clf_on = _settings.is_llm_classify_enabled()
                except Exception:
                    _multi_on, _llm_clf_on = True, True
                if _multi_on:
                    # Classify the USER's words. A slash-forced skill routes
                    # the user's args (empty for a bare "/skill-id"), never
                    # the synthetic boilerplate — classifying that text put
                    # every slash-only invocation on the action specialist.
                    # Empty text scores nothing, so the specialist comes
                    # from the skill frontmatter (_apply_forced_skill).
                    _clf_text = raw
                    if _skill_forced:
                        _clf_text = (getattr(self, '_forced_skill_args', u'')
                                     or u'').strip()
                    _clf_provider = None
                    _is_chat_msg = bool(
                        nlu_result and nlu_result.get("intent") == "chat")
                    if _llm_clf_on and not _is_chat_msg and _clf_text:
                        try:
                            from Intelligence.llm_router import LLMRouter as _LLMR
                            _clf_provider = _LLMR().get_active_provider()
                        except Exception:
                            _clf_provider = None
                    _skills_eng = None
                    try:
                        from Intelligence.skills_engine import SkillsEngine
                        _skills_eng = SkillsEngine()
                    except Exception:
                        pass
                    try:
                        self._agent_decision = AgentDispatcher().classify(
                            _clf_text, provider=_clf_provider,
                            skills_engine=_skills_eng,
                            allow_llm=bool(_clf_provider),
                            # Only when the classifier is reading the user's
                            # own words: a /slash invocation classifies the
                            # skill args, which this analysis is not of.
                            analysis=(_utterance if _clf_text == raw
                                      else None))
                    except Exception as _disp_ex:
                        logger.debug(u"dispatcher error: {}".format(_exc_text(_disp_ex)))
                    if self._agent_decision:
                        logger.debug("dispatcher: {} ({}, {:.2f})".format(
                            self._agent_decision.get('specialist'),
                            self._agent_decision.get('source'),
                            self._agent_decision.get('confidence', 0.0)))
                    # /slash skill beats whatever the dispatcher matched
                    self._apply_forced_skill()
                    if HAS_KNOWLEDGE and self._agent_decision and \
                            self._agent_decision.get('specialist') == 'knowledge':
                        if self._run_knowledge_agent(raw, history):
                            return
                        # no hits / LLM mute → continue down the normal path
                    if self._agent_decision and \
                            self._agent_decision.get('specialist') == 'comment':
                        # Comment workflow needs a PDF — guide the user.
                        _guide = (u"To process drawing comments: attach the "
                                  u"marked-up PDF (attach button) and resend. "
                                  u"I will read each comment, trace which "
                                  u"model its sheet belongs to, propose a "
                                  u"fix, and execute it once you confirm.")

                        def _need_pdf(_g=_guide):
                            self._hide_typing_indicator()
                            self._append_bot_message(
                                _g, icon=_ICON_SYNC, icon_color=_ICON_SLATE)
                            self._add_to_history("assistant", _g)
                            self._set_busy(False)
                        self.Dispatcher.Invoke(Action(_need_pdf))
                        return

            # Slash-forced skill also applies when the dispatcher is off or
            # never ran (multi_agent kill-switch, attachments, no provider).
            self._apply_forced_skill()

            # Carryover: the fresh dispatcher run saw only the short reply
            # ("1") and matched nothing — reuse the previous turn's decision
            # so the same specialist + skill playbook steer this turn too.
            if _continuation and not (self._agent_decision
                                      and self._agent_decision.get('skill')):
                self._agent_decision = dict(_prev_dec)
                self._agent_decision['source'] = 'carryover'

            # Remember the decision that actually steered this turn — the
            # next turn's carryover check needs it.
            if self._agent_decision and (
                    self._agent_decision.get('skill')
                    or self._agent_decision.get('specialist')
                    not in (None, 'general')):
                self._prev_agent_decision = dict(self._agent_decision)

            # ── 2.7 Graph path (multi-goal turns) ──────────────────────────
            # "thống kê tường rồi xuất pdf sheet A-101" is TWO requests. The
            # single-specialist path below can only serve one of them, and
            # historically answered the first and dropped the rest. The planner
            # decomposes the message; it declines anything it cannot split
            # confidently, so a normal one-goal turn never reaches this code
            # and keeps its exact previous behaviour.
            # Kill switch: agents.graph_mode.
            if (not has_attach and not _skill_forced and not _continuation
                    and (use_local or use_claude)):
                try:
                    if self._run_graph_plan(raw, history,
                                            utterance=_utterance):
                        return
                except Exception as _gp_ex:
                    logger.debug("graph path error: {}".format(_gp_ex))

            if use_local or use_claude or has_attach:
                # ── 3/4. LLM path (typing indicator already showing) ──────────
                nlu_hint = nlu_result if (HAS_NLP and not has_attach) else None

                def do_nlp():
                    result = None
                    # Owns the terminal message for THIS turn; a later turn
                    # bumps _request_id and invalidates any stale finisher.
                    rid = getattr(self, '_request_id', 0)
                    # Distinguishes "the model returned nothing at all" (timeout,
                    # connection error, empty body) from "the model answered but
                    # picked an unrecognised intent" — without this, both looked
                    # identical to the user: the same generic offline fallback
                    # text, no matter what they typed.
                    llm_call_failed = False
                    from Intelligence.llm_router import LLMRouter
                    import json as _json

                    _router = LLMRouter()
                    _provider = _router.get_active_provider()

                    # ── Native function-calling agent path ─────────────────────
                    # Providers with SUPPORTS_NATIVE_TOOLS run the real agentic
                    # loop: tool schemas travel through the API `tools` param,
                    # replies are plain text (no JSON-in-prompt). If the path
                    # can't even start (registry unavailable, provider mute on
                    # turn 1 — e.g. an Ollama model without tool support), it
                    # returns False and the legacy JSON-intent loop below runs.
                    # Compact retrieval context (RAG v2 excerpts) no longer
                    # blocks the native path — only bulky legacy stuffing
                    # still goes to the JSON-intent loop.
                    #
                    # Attached IMAGES used to force the legacy path too, and
                    # that path can only send a string: the model received the
                    # bare "[Hình ảnh đính kèm: x.png]" placeholder and quite
                    # correctly answered "I can't see images". A vision-capable
                    # provider now takes the native path and gets the real
                    # image blocks (see _run_native_agent's image_files).
                    _img_files = [p for p in attached if is_image(p)]
                    _can_see = bool(_img_files) and bool(
                        getattr(_provider, 'SUPPORTS_VISION', False))
                    _compact_rag = bool(
                        rag_context and len(rag_context) < 4000
                        and (not _img_files or _can_see))

                    # Text-only provider (Ollama / LM Studio / DeepSeek): be
                    # explicit about WHY the image can't be read and what to do
                    # about it, instead of leaving the model to improvise an
                    # excuse from a bare "[image attached]" placeholder.
                    _rag_query = rag_context
                    if _img_files and not _can_see:
                        _note = (
                            u"[System: the user attached {} image(s): {}. The "
                            u"active model ({}) has no vision capability, so "
                            u"you cannot see them. Say this plainly in the "
                            u"user's language, tell them to switch to Claude "
                            u"or OpenAI in the model picker to analyse "
                            u"images, then answer whatever else you can.]"
                        ).format(
                            len(_img_files),
                            u", ".join(os.path.basename(p) for p in _img_files),
                            getattr(_provider, 'DISPLAY_NAME', None)
                            or getattr(_provider, 'NAME', u'local'))
                        _rag_query = (u"{}\n\n{}".format(_note, rag_context)
                                      if rag_context else _note)

                    if (not has_attach and not rag_context) or _compact_rag:
                        _handled = False
                        # Specialist spec + matched skills from the dispatcher
                        # decision (multi-agent layer). None = legacy behavior.
                        _spec = None
                        _skill_ids = None
                        _dec = getattr(self, '_agent_decision', None)
                        if HAS_SPECIALISTS and _dec:
                            # Every AgentLoop-backed specialist rides the
                            # native path with its own prompt/tools/budget
                            # ('knowledge'/'comment' have dedicated
                            # pipelines handled earlier, 'general' means
                            # provider default = _spec None).
                            if _dec.get('specialist') in (
                                    'revit_data', 'revit_action',
                                    'multi_doc', 'modeling',
                                    'qa_check', 'export'):
                                _spec = get_spec(_dec['specialist'])
                            if _dec.get('skill'):
                                # build_skills_block injects up to two bodies;
                                # send it the two best-ranked matches rather
                                # than only the single winner, so a message
                                # that legitimately spans two playbooks gets
                                # both. A forced /slash skill stays alone.
                                _skill_ids = [_dec['skill']]
                                if not _dec.get('skill_forced'):
                                    _skill_ids = self._ranked_skill_ids(
                                        captured, _dec['skill'])
                                # keep only skills declared for this specialist
                                # (explicit /slash invocation skips the filter)
                                if not _dec.get('skill_forced'):
                                    try:
                                        from Intelligence.skills_engine import (
                                            get_skills_engine)
                                        _spec_name = (_spec.name if _spec
                                                      else 'general')
                                        _skill_ids = get_skills_engine() \
                                            .filter_for_specialist(_skill_ids,
                                                                   _spec_name)
                                    except Exception:
                                        pass
                        self._note_turn_route(
                            specialist=(_spec.name if _spec else 'general'),
                            skills=_skill_ids)
                        try:
                            _handled = self._run_native_agent(
                                _provider, list(history), captured,
                                spec=_spec, skill_ids=_skill_ids,
                                rag_context=(rag_context if _compact_rag
                                             else None),
                                image_files=(_img_files if _can_see else None),
                                analysis=_utterance)
                        except Exception as _na_ex:
                            # Also record it where it can actually be READ.
                            # logger.debug alone goes to pyRevit's debug log,
                            # which is off by default — so a native-path crash
                            # looked to everyone (including the activity log)
                            # like the legacy path simply having been chosen,
                            # and the user only ever saw the legacy path's
                            # "Could not read data from the model."
                            logger.debug(u"native agent path error: {}".format(
                                _exc_text(_na_ex)))
                            self._log_activity(
                                u"native agent path FAILED, falling back to the "
                                u"legacy JSON path — {}: {}".format(
                                    type(_na_ex).__name__,
                                    _exc_text(_na_ex))[:600])
                        if _handled:
                            return

                    # Skill playbook for the LEGACY path too. When the native
                    # agent path is unavailable (provider without native
                    # tools, MCP server not up, turn-1 provider mute), a
                    # /slash skill used to reach this JSON-intent loop as the
                    # bare boilerplate with NO playbook injected — the model
                    # then hallucinated pseudo-intents ("apply_playbook") and
                    # the skill never ran.
                    _legacy_skills_block = u""
                    try:
                        _dec_lg = getattr(self, '_agent_decision', None) or {}
                        _sk_lg = _dec_lg.get('skill') or getattr(
                            self, '_forced_skill_id', None)
                        if _sk_lg:
                            from Intelligence.skills_engine import (
                                build_skills_block as _bsb)
                            # Same ranked top-2 the native path gets, so the
                            # two prompt paths cannot drift apart. A forced
                            # /slash skill stays alone.
                            _ids_lg = ([_sk_lg]
                                       if getattr(self, '_forced_skill_id', None)
                                       else self._ranked_skill_ids(captured, _sk_lg))
                            _legacy_skills_block = _bsb(_ids_lg)
                    except Exception:
                        _legacy_skills_block = u""

                    # Step budget. 5 was set before specialists existed and is
                    # now the outlier: the native path gives 6–14 depending on
                    # the specialist. Five is not enough for an ordinary
                    # multi-step read ("check the model name against ISO
                    # 19650" spent one step on context and two on sheet lists,
                    # then hit the wall and told the user to split the request
                    # — work the assistant should just finish). Follow the
                    # specialist's own budget, with the native path's default.
                    max_iterations = 10
                    try:
                        _spec_budget = getattr(_spec, 'max_iterations', None)
                        if _spec_budget:
                            max_iterations = int(_spec_budget)
                    except Exception:
                        pass
                    current_iteration = 0
                    current_history = list(history)
                    # Deterministic repeat guards — small local models ignore
                    # prompt rules and re-emit calls: the same call again in
                    # the same turn ("tô xanh sàn" ran 3x), or a finished
                    # PREVIOUS turn's call mid-turn ("tô tím cửa" replayed).
                    # Prompt rules alone don't stop this; the loop does.
                    _turn_calls = set()          # (intent, args) run THIS turn
                    _prev_calls = self._earlier_turn_calls()
                    _blocked_repeats = 0
                    _announce_nudges = 0         # "I'm about to…" with no call

                    # Initial user prompt. _rag_query == rag_context except
                    # when a text-only provider was handed images (see above).
                    current_query = (_rag_query + u"\n\n" + captured) if _rag_query else captured

                    while current_iteration < max_iterations:
                        current_iteration += 1

                        # Stop before spending another LLM turn. This loop used
                        # to ignore _cancel_requested entirely, so Stop greyed
                        # out the button while the agent kept issuing up to five
                        # more calls and executing real write tools.
                        if self._cancelled():
                            self._finish_cancelled(rid)
                            return

                        # Dynamically query Revit context on each iteration
                        _ctx_block = u""
                        if HAS_SCOUT:
                            try:
                                _ctx_block = ContextScout.get_context_summary_for_ai()
                            except Exception:
                                pass

                        # Retrieve registered tools from server.py — compact
                        # catalog (C4): names + one-line purpose only. The full
                        # JSON schemas of all ~75 tools used to be inlined here
                        # on EVERY iteration, which crushed small local models
                        # (token bloat + broken JSON). The model now requests a
                        # schema on demand via the describe_tool meta-intent.
                        server_tools_str = u""
                        tools_list = []
                        try:
                            from core.server import get_t3labai_server
                            srv = get_t3labai_server()
                            tools_list = srv._handle_tools_list().get('tools', [])
                            # Local models: shrink the catalog to the curated
                            # essential subset (same rationale as the native
                            # agent path — accuracy + context budget).
                            if tools_list and getattr(_provider, 'NAME', '') in ("ollama", "lmstudio"):
                                from Intelligence.tool_schema import ESSENTIAL_TOOL_NAMES
                                _ess = [t for t in tools_list
                                        if t.get('name') in ESSENTIAL_TOOL_NAMES]
                                tools_list = _ess or tools_list
                            if tools_list:
                                server_tools_str = u"\n\nLocal MCP Server Tools (name: purpose):\n"
                                for tool in tools_list:
                                    desc = (tool.get('description') or u'').strip()
                                    desc = desc.splitlines()[0][:110] if desc else tool['name']
                                    server_tools_str += u"- `{}`: {}\n".format(
                                        tool['name'], desc)
                                server_tools_str += (
                                    u"\nTool calls need correct parameters. If you are "
                                    u"unsure of a tool's parameters, FIRST reply exactly "
                                    u'{"intent": "describe_tool", "params": {"name": "<tool_name>"}} '
                                    u"to receive its full JSON schema, then call the tool "
                                    u"in your next reply.\n")
                        except Exception as tool_err:
                            logger.debug(u"Failed to list server tools: {}".format(_exc_text(tool_err)))

                        system_prompt = _build_system_prompt(revit_context=_ctx_block)
                        # Project instructions + remembered facts. This path is
                        # reached whenever the user attaches a document or RAG
                        # returns a lot of context (see the native/legacy gate
                        # above) — precisely when the project's conventions
                        # matter — yet it carried NEITHER block until now.
                        system_prompt = self._apply_project_blocks(system_prompt)
                        if server_tools_str:
                            system_prompt += server_tools_str
                        if _legacy_skills_block:
                            system_prompt += (
                                u"\n\n" + _legacy_skills_block +
                                u"\n\nIMPORTANT: the Active skill above is a "
                                u"set of INSTRUCTIONS for you — 'playbook', "
                                u"'skill' and 'apply_playbook' are NOT intent "
                                u"or tool names. Execute its steps now using "
                                u"ONLY the intents and MCP tools listed in "
                                u"this prompt.")

                        if has_attach or rag_context:
                            system_prompt = _RAG_SYSTEM_PREFIX + system_prompt

                        # Perform the chat completion.
                        # Iteration 1 streams live into a growing bubble (smooth,
                        # low time-to-first-token). Subsequent tool-loop turns are
                        # internal, so they use a plain blocking call.
                        _resp = None
                        try:
                            if current_iteration == 1:
                                _resp = self._stream_llm_turn(
                                    _provider, _router, current_history,
                                    system_prompt, current_query, max_tokens=1200,
                                    response_format={"type": "json_object"}
                                )
                            elif _provider and _provider_usable(_provider):
                                _resp = _provider.chat(current_history[-16:], system_prompt, current_query, max_tokens=1200, response_format={"type": "json_object"})
                            else:
                                _resp = _router.chat(current_history[-16:], system_prompt, current_query, max_tokens=1200, response_format={"type": "json_object"})
                        except Exception as chat_ex:
                            logger.debug(u"Router chat error: {}".format(_exc_text(chat_ex)))

                        if not _resp or not _resp.strip():
                            llm_call_failed = True
                            break

                        # Parse response JSON
                        _cleaned = self._clean_bot_response(_resp)
                        _parsed = None
                        try:
                            import re as _re
                            # First try extracting JSON block using regex if model included extra text
                            _m = _re.search(r'\{[\s\S]*\}', _resp)
                            if _m:
                                _parsed = _json.loads(_m.group())
                            else:
                                # Fallback to direct parsing
                                _parsed = _json.loads(_resp)
                                
                            # Ensure intent is present
                            if "intent" not in _parsed:
                                _parsed["intent"] = "unknown"
                        except Exception:
                            # Fallback if model returns corrupted JSON/text
                            _parsed = {
                                "intent": "unknown",
                                "message": u"Could not read data from the model. Please try again."
                            }

                        if _parsed and _parsed.get("intent"):
                            intent = _parsed.get("intent")
                            params = _parsed.get("params", {}) or {}
                            message = _parsed.get("message", "")

                            # C4 meta-intent: the model asks for one tool's full
                            # schema (the catalog above only carries one-liners).
                            # Feed the schema back and let it produce the real
                            # tool call on the next iteration.
                            if intent == "describe_tool":
                                self.Dispatcher.Invoke(Action(self._remove_stream_bubble))
                                tname = u"{}".format(
                                    (params or {}).get("name")
                                    or (params or {}).get("tool") or u"").strip()
                                match = None
                                for _t in (tools_list or []):
                                    if _t.get('name') == tname:
                                        match = _t
                                        break
                                if match:
                                    info = _json.dumps(
                                        {"name": match['name'],
                                         "description": match.get('description', ''),
                                         "inputSchema": match.get('inputSchema', {})},
                                        ensure_ascii=False)
                                else:
                                    info = _json.dumps(
                                        {"error": "unknown tool", "name": tname},
                                        ensure_ascii=False)
                                current_history.append(
                                    {"role": "assistant",
                                     "content": _json_text(_parsed)})
                                current_history.append(
                                    {"role": "user",
                                     "content": u"Tool schema: {}".format(info)})
                                current_query = (
                                    u"Schema: {}. Now respond with the actual tool call as "
                                    u'{{"intent": "<tool_name>", "params": {{...}}, '
                                    u'"message": "..."}}.'.format(info))
                                continue

                            # Determine if it's a local MCP tool call — check
                            # against the real registry, not a "revit_" prefix
                            # guess, since several real tool names don't start
                            # with "revit_" (place_wall, create_grid, etc.) and
                            # a hallucinated "revit_*" name that ISN'T
                            # registered would otherwise be sent into the
                            # External Event round-trip for nothing.
                            is_local_tool = False
                            try:
                                from core.server import get_t3labai_server
                                srv = get_t3labai_server()
                                if intent in srv._tools:
                                    is_local_tool = True
                            except Exception:
                                pass

                            if is_local_tool:
                                _call_key = (intent, _json_text(
                                    params or {}, sort_keys=True))
                                _dup_now = _call_key in _turn_calls
                                # A previous turn's call re-emitted AFTER this
                                # turn already did its own different work =
                                # old-context drift. (The FIRST call of a turn
                                # matching the previous turn stays allowed —
                                # the user may legitimately repeat a command.)
                                _old_ctx = (not _dup_now and bool(_turn_calls)
                                            and _call_key in _prev_calls)
                                if _dup_now or _old_ctx:
                                    _blocked_repeats += 1
                                    if _blocked_repeats >= 2:
                                        # Model is looping — conclude for it.
                                        result = {"intent": "chat",
                                                  "message": (
                                            u"Đã thực hiện xong yêu cầu của bạn."
                                            if _is_viet_text(captured) else
                                            u"Done — the request has been completed.")}
                                        break
                                    if _dup_now:
                                        _guard_msg = (
                                            u"Tool `{}` with IDENTICAL arguments already ran "
                                            u"successfully in THIS turn — never repeat a "
                                            u"completed call. If the request \"{}\" is "
                                            u"fulfilled, reply with the final JSON now: "
                                            u'{{"intent": "chat", "message": "<short summary>"}}.'
                                            .format(intent, captured))
                                    else:
                                        _guard_msg = (
                                            u"Tool `{}` with these arguments already ran in a "
                                            u"PREVIOUS completed turn — that work is DONE. Act "
                                            u"ONLY on the current request: \"{}\". Reply with "
                                            u"the next NEW tool call for it, or the final "
                                            u'summary JSON {{"intent": "chat", "message": '
                                            u'"..."}} if it is fulfilled.'.format(intent, captured))
                                    current_history.append(
                                        {"role": "assistant",
                                         "content": _json_text(_parsed)})
                                    current_history.append(
                                        {"role": "user", "content": _guard_msg})
                                    current_query = _guard_msg
                                    continue
                                # The streamed preview (if any) is superseded by
                                # explicit tool-execution feedback below.
                                self.Dispatcher.Invoke(Action(self._remove_stream_bubble))
                                # Update typing indicator UI to show tool execution
                                is_vn = _is_viet_text(captured)
                                status_msg = u"● ● ●  Đang chạy công cụ `{}`...".format(intent) if is_vn else "● ● ●  Executing tool `{}`...".format(intent)
                                self._safe_update_typing_text(status_msg)

                                # The model's own "what I'm doing" line, if it
                                # wrote one. The tool NAME and ARGS used to be
                                # printed here too, but the card below says all
                                # of that and also shows the RESULT.
                                if message:
                                    self._safe_append_bot(
                                        message, icon=_ICON_SYNC,
                                        icon_color=_ICON_BLUE)

                                # Real tool card — the same one the native agent
                                # path draws. This path used to print only a
                                # "[Tool Call] …" line BEFORE execution and
                                # nothing after, so the tool's data reached the
                                # user only if the model chose to repeat it in
                                # prose. It often does not: "list levels" ran
                                # list_levels, then answered "Danh sách các mức
                                # đã được liệt kê. Bạn muốn làm gì tiếp?" and
                                # the levels themselves were never shown. The
                                # card puts the result on screen deterministically,
                                # whatever the model says afterwards.
                                _card = {"cur": None}

                                def _open_card(_n=intent, _a=params):
                                    _card["cur"] = self._append_tool_card(_n, _a)

                                try:
                                    self.Dispatcher.Invoke(Action(_open_card))
                                except Exception:
                                    pass

                                # Execute the tool in the Revit context using the external event handler
                                import time as _time
                                _t0 = _time.time()
                                tool_result = None
                                try:
                                    from core.server import get_t3labai_server
                                    srv = get_t3labai_server()
                                    tool_result = srv._execute_tool(intent, params)
                                except Exception as execute_err:
                                    # _exc_text, never str(): a Revit error
                                    # carrying a non-ASCII message made str()
                                    # raise UnicodeEncodeError here, killing the
                                    # whole turn from inside the error handler.
                                    tool_result = {"error": _exc_text(execute_err),
                                                   "tool": intent}

                                _ok = not (isinstance(tool_result, dict)
                                           and tool_result.get('error'))
                                _secs = _time.time() - _t0

                                def _close_card(_ok=_ok, _s=_secs,
                                                _r=tool_result):
                                    self._update_tool_card(_card["cur"], _ok,
                                                           _s, _r)

                                try:
                                    self.Dispatcher.Invoke(Action(_close_card))
                                except Exception:
                                    pass

                                # Register the successful call (errors stay
                                # unregistered — rule 4 allows ONE retry with
                                # corrected/same args after a failure).
                                if not (isinstance(tool_result, dict)
                                        and tool_result.get('error')):
                                    _turn_calls.add(_call_key)
                                    self._tool_runs += 1

                                # Tool execution is where the seconds go, so
                                # this is the checkpoint that makes Stop feel
                                # immediate. Returning here skips finish(), so
                                # there is no double release.
                                if self._cancelled():
                                    self._finish_cancelled(rid)
                                    return

                                # Log tool call and result to current_history using portable roles
                                _result_text = _json_text(tool_result)
                                current_history.append({"role": "assistant", "content": _json_text(_parsed)})
                                current_history.append({"role": "user", "content": u"Tool `{}` returned: {}".format(intent, _result_text)})

                                # Setup subsequent query — restate WHICH request
                                # is being executed. A bare "proceed to the next
                                # step" let small models drift into the chat
                                # history and replay the PREVIOUS turn's command
                                # ("tô vàng sàn" re-ran the finished "tô đỏ
                                # tường" task).
                                current_query = (
                                    u"Tool `{}` returned: {}. Continue with the "
                                    u"NEXT step of the user's LATEST request "
                                    u"ONLY: \"{}\". Earlier conversation turns "
                                    u"are already completed — never repeat "
                                    u"them. Reply with the next tool call, or "
                                    u"a final summary if this request is "
                                    u"done.".format(
                                        intent,
                                        _result_text,
                                        captured))
                                continue
                            else:
                                # A model that picks "unknown" but still wrote a
                                # message (small/local models do this a lot —
                                # they don't map cleanly to one of the listed
                                # intents but still try to answer) should have
                                # that answer shown, not silently swapped out
                                # for the generic offline fallback in finish().
                                if intent == "unknown" and message.strip():
                                    _parsed["intent"] = "chat"
                                # Narrated instead of acted. Same hole the
                                # native AgentLoop had: a reply that only
                                # promises the work ("Đang liệt kê các mức
                                # trong dự án…") ended the turn with no tool
                                # run and no data — "list levels" answered
                                # nothing. Nudge once, then let it stand.
                                if (self._tool_runs == 0 and not _turn_calls
                                        and _announce_nudges < 1
                                        and _announces_work(
                                            message or _cleaned)):
                                    _announce_nudges += 1
                                    self.Dispatcher.Invoke(Action(
                                        self._remove_stream_bubble))
                                    current_history.append(
                                        {"role": "assistant",
                                         "content": _json_text(_parsed)})
                                    current_history.append(
                                        {"role": "user",
                                         "content": _ANNOUNCE_FIXUP})
                                    current_query = (
                                        u"{}\n\nThe request is: \"{}\"".format(
                                            _ANNOUNCE_FIXUP, captured))
                                    continue
                                result = _parsed
                                break
                        else:
                            result = {"intent": "chat", "message": _cleaned}
                            break

                    # Remember this turn's executed calls — later turns'
                    # old-context guard needs them. Pure-chat turns keep the
                    # registry alive.
                    if _turn_calls:
                        self._remember_turn_calls(_turn_calls)

                    # Falling out of the while loop leaves result=None, because
                    # every tool-executing branch ends in `continue` and only a
                    # text reply breaks with a result. finish() then walked all
                    # the way down to "I didn't understand this request" — after
                    # five tools had SUCCESSFULLY run and modified the model.
                    # Only synthesise when tools actually ran; with none, the
                    # existing llm_call_failed / didn't-understand paths are
                    # still the right answer.
                    if result is None and self._tool_runs:
                        result = {
                            "intent": "chat",
                            "message": (
                                u"Completed {} step(s), then stopped — this "
                                u"request needs more steps than one turn "
                                u"allows. Tell me what to do next, or split it "
                                u"into smaller requests.".format(self._tool_runs)),
                            "params": {},
                            "_max_steps": True,
                        }

                    def finish():
                        try:
                            if self._cancelled():
                                self._finish_cancelled(rid)
                                return
                            if not self._claim_turn(rid):
                                return
                            has_stream = (self._stream_tb is not None)
                            r_intent   = result.get("intent") if result else None
                            _conv      = ("chat", "help", "greet")

                            # Step budget exhausted mid-plan — show what ran and
                            # offer the same one-click continuation the native
                            # agent path already offers.
                            if result and result.get("_max_steps"):
                                if has_stream:
                                    self._remove_stream_bubble()
                                    self._clear_stream_refs()
                                self._hide_typing_indicator()
                                msg = result.get("message", u"")
                                self._append_bot_message(
                                    msg, icon=_ICON_WARNING,
                                    icon_color=_ICON_AMBER)
                                self._add_to_history("assistant", msg)
                                try:
                                    self._append_quick_replies(
                                        [u"Continue"],
                                        [u"continue where you stopped"])
                                except Exception:
                                    pass
                                self._set_busy(False)
                                return

                            # Plain conversational reply that already streamed live
                            # → keep the bubble, just apply markdown + record it.
                            if has_stream and r_intent in _conv:
                                if r_intent == "help":
                                    msg = result.get("params", {}).get(
                                        "answer", result.get("message", ""))
                                else:
                                    msg = result.get("message", "")
                                msg = self._clean_bot_response(msg) if msg else u""
                                if not msg:
                                    try:
                                        msg = self._stream_tb.Text or u""
                                    except Exception:
                                        msg = u""
                                if not msg:
                                    msg = u"Anything else I can help with?"
                                self._finalize_stream_bubble(msg)
                                self._add_to_history("assistant", msg)
                                self._clear_stream_refs()
                                self._set_busy(False)
                                return

                            # Action/tool result → discard any preview bubble and
                            # let the executor render its own message + run it.
                            if has_stream:
                                self._remove_stream_bubble()
                            self._hide_typing_indicator()
                            # A generic "I didn't understand" guess from the offline
                            # NLU is only worth showing when the LLM call actually
                            # completed and ALSO had nothing better — if the LLM
                            # never responded at all, that guess would silently
                            # masquerade as "the assistant tried and failed to
                            # match your request", which is misleading; the
                            # llm_call_failed branch below gives the real reason.
                            nlu_hint_usable = (
                                nlu_hint and nlu_hint.get("intent") not in (None, "unknown")
                                and not (llm_call_failed and nlu_hint.get("_generic_fallback"))
                            )
                            if result and result.get("intent") not in (None, "unknown"):
                                self._execute_result(result)
                            elif nlu_hint_usable:
                                self._execute_result(nlu_hint)
                            else:
                                if has_attach and not use_claude and not use_local:
                                    if rag_context:
                                        self._append_bot_message(
                                            u"Document content:\n\n" + rag_context[:2000],
                                            icon=_ICON_ATTACH, icon_color=_ICON_SLATE
                                        )
                                    else:
                                        self._append_bot_message(
                                            u"Could not extract text from the document. The PDF may be scanned."
                                        )
                                    self._set_busy(False)
                                    return
                                fb = keyword_parse(captured)
                                if fb:
                                    self._execute_result(fb)
                                elif llm_call_failed:
                                    # The AI genuinely never answered (timeout /
                                    # connection error / API rejection) — say so
                                    # plainly, WITH the provider's real reason
                                    # when it was recorded (get_last_error).
                                    label = get_provider_display_label()
                                    detail = u""
                                    try:
                                        _le = (_provider.get_last_error()
                                               if _provider else None)
                                        if _le:
                                            detail = (u"\nDetails: {}".format(_le)
                                                      if _is_viet_text(captured)
                                                      else u"\nDetail: {}".format(_le))
                                    except Exception:
                                        pass
                                    msg = (u"Model AI ({}) không phản hồi (model quá nặng, "
                                           u"mất kết nối hoặc API báo lỗi). Thử lại hoặc chọn "
                                           u"model khác trong Cài đặt.{}".format(label, detail)
                                           if _is_viet_text(captured) else
                                           u"The AI model ({}) didn't respond (too heavy, "
                                           u"disconnected, or the API returned an error). Try again "
                                           u"or pick another model in Settings.{}".format(label, detail))
                                    self._append_bot_message(msg, icon=_ICON_WARNING, icon_color=_ICON_AMBER)
                                    self._set_busy(False)
                                else:
                                    msg = (u"I did not understand this request — could you describe it in more detail?"
                                           if _is_viet_text(captured) else
                                           "I didn't understand this request — could you describe it more specifically?")
                                    self._append_bot_message(msg)
                                    self._set_busy(False)
                        except Exception as finish_ex:
                            self._clear_stream_refs()
                            # _claim_turn was already taken at the top of
                            # finish(); release it so the error can be shown.
                            self._replied = False
                            self._report_error(u"finish", finish_ex, rid)

                    self.Dispatcher.Invoke(Action(finish))

                # Already on the routing worker thread — run the LLM turn
                # inline instead of spawning yet another thread.
                do_nlp()
            else:
                # ── 5. No provider configured at all — keyword fallback ─────────
                fb = keyword_parse(raw)

                def _finish_offline(_fb=fb):
                    if _fb:
                        self._execute_result(_fb)
                    else:
                        self._append_bot_message(
                            get_setup_guidance_message(_is_viet_text(raw)),
                            icon=_ICON_INFO, icon_color=_ICON_SLATE)
                        self._set_busy(False)
                self.Dispatcher.Invoke(Action(_finish_offline))
        except Exception:
            # Bubble to _route()'s handler in _process_input — it hides the
            # typing indicator and releases the busy lock on the UI thread.
            raise

    # ─── Execute intent ────────────────────────────────────────────────────────

    def _execute_result(self, result):
        """Execute the action described by a parsed result dict.

        Responsibilities:
        - Display bot message
        - Add bot reply to conversation history
        - Learn successful patterns
        - Release busy state when done (including after background exports)
        """
        intent  = result.get("intent", "unknown")
        message = result.get("message", "")
        params  = result.get("params", {})
        raw     = self._last_raw
        # Kept so a 👍/👎 on the reply can act on the route that produced it
        # (_vote_decision reads it when the reply row is built).
        self._last_result = dict(result or {})

        def _bot(msg):
            """Show message and record in conversation history."""
            self._append_bot_message(msg)
            self._add_to_history("assistant", msg)

        def _learn(msg=''):
            """Record successful command→intent mapping."""
            learn_pattern(raw, intent, params, msg)

        # ── Conversational-input guard (last line of defence) ─────────────────
        # Pure small talk ("morning", "thanks", "ok"...) must never launch a
        # tool, no matter which layer produced the intent — a mis-learned
        # pattern or an LLM hallucination could map it to open_*/export.
        if intent not in ("help", "chat", "greet", "unknown") and HAS_NLP:
            try:
                from Intelligence.nlu_engine import is_conversational
                _is_smalltalk = is_conversational(raw)
            except Exception:
                _is_smalltalk = False
            if _is_smalltalk:
                conv = parse_command_nlu(raw) or {}
                if conv.get("intent") in ("greet", "chat", "help") and conv.get("message"):
                    reply = conv["message"]
                elif _is_viet_text(raw):
                    reply = (u"Xin chào! Tôi là T3Lab Assistant.\n"
                             u"Hôm nay bạn muốn làm gì?")
                else:
                    reply = (u"Hello! I'm T3Lab Assistant.\n"
                             u"What would you like to do today?")
                _bot(reply)
                self._set_busy(False)
                return

        # ── Conversation (no action needed) ──────────────────────────────────
        if intent in ("help", "chat", "greet"):
            reply = params.get("answer", message) if intent == "help" else message
            reply = self._clean_bot_response(reply) if reply else u""
            _bot(reply or u"Anything else I can help with?")
            self._set_busy(False)
            return

        # Both paths construct Revit-bound windows and must run in API context.
        if intent in ("export_direct", "open_batchout_configured"):
            request_id = getattr(self, '_request_id', 0)
            if getattr(self, '_batchout_request_id', None) == request_id:
                return
            # The LLM finish() claims a synchronous reply before handing its
            # intent here. Transfer terminal ownership to the API callback.
            self._replied = False
            _bot("Export queued. Waiting for Revit..." if intent == "export_direct"
                 else "BatchOut launch queued. Waiting for Revit...")
            expected_doc = self.doc
            self._batchout_request_id = request_id
            self._batchout_running = False

            def _cancel_batchout():
                return (self._cancelled() or request_id != getattr(self, '_request_id', 0))

            def _run_batchout():
                if _cancel_batchout():
                    return False, "BatchOut request stopped before execution. No files were created."
                if (expected_doc is None or not expected_doc.IsValidObject
                        or revit.doc != expected_doc):
                    return False, "The active document changed or closed. Refresh the Assistant context before retrying."
                self._batchout_running = True
                if intent == "export_direct":
                    if not HAS_EXECUTOR:
                        return False, "The BatchOut executor is unavailable. Reload pyRevit before retrying."
                    ok, count, details = direct_export(_load_batchout_mod(), params,
                                                       cancel_check=_cancel_batchout)
                    return ok, details
                notices = []
                ok = launch_batchout_configured(params, notices.append)
                return ok, ("BatchOut was opened with the requested settings."
                            if ok else (notices[-1] if notices else "Could not open configured BatchOut."))

            def _finished_batchout(ok, details):
                def _finish_ui():
                    if getattr(self, '_batchout_request_id', None) == request_id:
                        self._batchout_request_id = None
                        self._batchout_running = False
                    if not self._claim_turn(request_id):
                        return
                    try:
                        _bot(details or "BatchOut did not return a result. Check Revit before retrying.")
                        if ok:
                            _learn(details)
                    finally:
                        self._set_busy(False)
                self._ui_invoke(_finish_ui)

            if not HAS_API_CONTEXT:
                _finished_batchout(False, "Revit API event support is unavailable. Reload pyRevit and reopen the Assistant.")
                return
            try:
                run_in_api_context(_run_batchout, _finished_batchout, require_api_context=True)
            except Exception as ex:
                _finished_batchout(False, _exc_text(ex))
            return

        # ── Spell-check all Text Notes (deterministic DB scan + LLM proofread) ─
        # The scan is done HERE, not at the LLM's discretion — small local
        # models used to just chat back "please send me the text notes".
        if intent == "check_spelling":
            confirm = message or (
                u"Đang quét Text Note trong model để kiểm tra chính tả tiếng Anh..."
                if _is_viet_text(raw) else
                u"Scanning model Text Notes for English spelling errors...")
            _bot(confirm)
            _learn(confirm)
            self._run_spellcheck(raw)
            return

        # ── Simple tool launchers ─────────────────────────────────────────────
        if intent in TOOL_LAUNCHERS:
            confirm = message or u"Opening {}...".format(get_tool_title(intent))
            _bot(confirm)
            _learn(confirm)
            self._launch_tool(intent, TOOL_LAUNCHERS[intent])
            return

        # ── MCP Revit intents ─────────────────────────────────────────────────
        try:
            from Intelligence.t3lab_agent import is_mcp_intent, get_intent_info
            if is_mcp_intent(intent):
                cat, desc = get_intent_info(intent)
                param_txt = u""
                if params:
                    param_txt = u"\n**Parameters:** " + u", ".join(
                        u"{}={}".format(k, v) for k, v in params.items() if v is not None
                    )
                reply = (
                    message + u"\n\n"
                    if message else u""
                ) + (
                    u"**Revit API:** `{}`\n"
                    u"**Action:** {}{}\n\n"
                    u"*Requires the [revit-mcp](https://github.com/mcp-servers-for-revit/revit-mcp) "
                    u"server running alongside Revit.*"
                ).format(intent, desc, param_txt)
                _learn(reply)
                _bot(reply)
                self._set_busy(False)
                return
        except Exception as _mcp_ex:
            logger.debug(u"MCP intent handler error: {}".format(_exc_text(_mcp_ex)))

        # ── Recover hallucinated open_* intents ──────────────────────────────
        # The LLM sometimes invents a near-miss intent name ("open_mcp_control"
        # instead of "open_mcpcontrol"). Before declaring the tool missing,
        # resolve the user's own words against the full tool catalog and
        # launch the tool only if one clearly wins.
        if intent.startswith("open_") and HAS_NLP:
            _match = None
            try:
                from Intelligence.nlu_engine import resolve_tool
                _match, _cands = resolve_tool(raw)
            except Exception:
                _match = None
            if _match and _match['intent'] in TOOL_LAUNCHERS:
                label = _match.get('title', _match['intent'])
                confirm = (u"Đang mở {}...".format(label) if _is_viet_text(raw)
                           else u"Opening {}...".format(label))
                _bot(confirm)
                learn_pattern(raw, _match['intent'], {}, confirm)
                self._launch_tool(_match['intent'],
                                  TOOL_LAUNCHERS[_match['intent']])
                return
            # Nothing resolved — name the tools that DO exist rather than
            # leaving the user with a dead intent name. `_cands` comes from
            # resolve_tool's ambiguity list (top 3 plausible tools).
            if intent not in TOOL_LAUNCHERS:
                _bot(self._unknown_tool_text(intent, raw))
                self._set_busy(False)
                return

        # ── Unknown / fallthrough ─────────────────────────────────────────────
        # Reaching here with a non-empty, non-"unknown" intent means the model
        # returned a tool name that isn't registered anywhere (T3Lab UI tool
        # or MCP tool) — say so plainly instead of the misleading "Đã thực
        # hiện." ("Done."), since nothing was actually executed.
        if intent == "unknown":
            _bot(params.get("message", u"The request is not clear — could you describe it in more detail?"))
        elif message:
            _bot(message)
        else:
            _bot(u"Tool `{}` does not exist — check the name or describe what you need.".format(intent))
        self._set_busy(False)

    def _launch_tool(self, intent, launcher):
        """Open a ribbon tool from chat. UI THREAD.

        The launcher is marshalled into a real Revit API context instead of
        being called here. `_execute_result` runs on the WPF dispatcher, i.e.
        Revit's main thread while Revit is IDLE — and idle is not a "standard
        API execution": a tool that creates an ExternalEvent (BCF Reader,
        ManaLoca, BatchOut) or opens a Transaction threw as soon as its window
        was built, after the assistant had already said it was opening.

        The hop is ASYNCHRONOUS by necessity — Revit fires external events
        from this very thread's message loop, so waiting for the result here
        would deadlock. Busy is therefore released immediately (the tool has
        been handed over) and only a FAILURE gets reported afterwards.
        """
        def _report(ok, err):
            # SUCCESS used to return silently. The turn then ended on the
            # "Opening X..." line — a progress message that never resolved, so
            # the user sat watching an ellipsis with no way to tell whether
            # the window had opened, was still coming, or had died. Report
            # both outcomes: an unresolved in-progress line reads as a hang.
            if ok:
                self._ui_invoke(
                    lambda: self._append_bot_message(
                        self._launch_success_text(intent),
                        icon=_ICON_SUCCESS, icon_color=_ICON_GREEN))
                return
            self._ui_invoke(
                lambda: self._append_bot_message(
                    self._launch_failure_text(intent, err)))

        try:
            run_in_api_context(launcher, _report)
        except Exception as ex:
            _report(False, _exc_text(ex))
        self._set_busy(False)

    def _launch_success_text(self, intent):
        """Terminal confirmation for a launcher that DID open its window."""
        title = get_tool_title(intent)
        if _ui_viet():
            return u"Đã mở **{}**. Cửa sổ tool đang ở trong Revit.".format(title)
        return u"Opened **{}** — the tool window is now in Revit.".format(title)

    def _launch_failure_text(self, intent, err):
        """Honest failure message for a launcher that did not open its tool.

        Replaces the old "Could not open the tool. Check the console." — the
        console is not where a Revit user looks, and the message named neither
        the tool nor the reason.
        """
        title = get_tool_title(intent)
        if err:
            return u"Could not open **{}**: {}".format(title, err)
        return u"Could not open **{}** — the tool reported no reason.".format(title)

    def _unknown_tool_text(self, intent, raw):
        """Message for an intent that resolves to no installed tool.

        Lists real alternatives from the live catalog so the user is not left
        guessing. The old code advertised intents whose pushbuttons had been
        deleted, so this path used to be reached constantly.
        """
        lines = [u"There is no tool called `{}` in this extension.".format(intent)]
        names = []
        try:
            from Intelligence.nlu_engine import resolve_tool
            _m, cands = resolve_tool(raw or u"")
            for c in (cands or [])[:3]:
                t = c.get('title') or c.get('intent')
                if t and t not in names:
                    names.append(t)
        except Exception:
            pass
        if names:
            lines.append(u"Closest matches: {}.".format(
                u", ".join(u"**{}**".format(n) for n in names)))
        else:
            lines.append(u"Type `/` or open the command palette to see what is "
                         u"available.")
        return u"\n\n".join(lines)

    def _run_spellcheck(self, raw):
        """Collect every TextNote, proofread them in batches via the LLM,
        then post one consolidated report (element IDs included).

        Runs on a background thread; scope switches to the active view when
        the request mentions it ("trong view này", "active view", ...).
        """
        viet = _is_viet_text(raw)
        low = (raw or u"").lower()
        view_only = any(k in low for k in (
            u"view này", u"view nay", u"view hiện tại", u"view hien tai",
            u"trong view", u"active view", u"current view", u"in view",
            u"in this view"))

        def work():
            report = None
            ui_findings, ui_total, ui_uniq, ui_failed = [], 0, 0, 0
            try:
                from Services import spell_checker as SC
                # Collect on the Revit MAIN thread via the MCP server's
                # ExternalEvent. The old direct SC.collect_text_notes(self.doc)
                # call ran the collector on THIS worker thread — Revit rejected
                # it, the exception was swallowed and "No Text Notes found"
                # came back on models full of notes. Whole-project scope now
                # also proofreads sheet/view/room/level names + project info,
                # not just TextNotes.
                notes = None
                res = None
                try:
                    from core.server import get_t3labai_server
                    srv = get_t3labai_server()
                    # ONE main-thread collector for the whole model. Replaces the
                    # old piecemeal calls (TextNotes + sheets/views/rooms/levels
                    # + 4 project-info fields): it also gathers title-block
                    # labels, the FULL Project Information, revision
                    # descriptions, view "Title on Sheet", dimension text
                    # overrides, model text and schedule names, so every place
                    # that holds human-authored text gets proofread.
                    res = srv._execute_tool('collect_spellcheck_text',
                                            {'view_only': view_only})
                    if not isinstance(res, dict):
                        res = {}
                    if 'items' in res:
                        notes = []
                        for it in res['items']:
                            _txt = (it.get('text') or u'').strip()
                            if _txt and any(c.isalpha() for c in _txt):
                                notes.append({
                                    'id':   it.get('id'),
                                    'text': _txt,
                                    'view': it.get('view')
                                            or it.get('source') or u'',
                                })
                except Exception as _col_ex:
                    logger.debug(u"spellcheck server collect error: {}".format(_exc_text(_col_ex)))
                    notes = None
                if notes is None and isinstance(res, dict) and res.get('error'):
                    # The scan TOOL failed (e.g. document-context error) —
                    # surface that instead of the misleading "No Text Notes
                    # found": every model has at least view/level names, so
                    # an empty scan almost always means the scan itself
                    # broke, and hiding the error sent debugging the wrong
                    # way (looked like an empty model).
                    report = (u"Không quét được model — MCP server báo lỗi: {}"
                              if viet else
                              u"Could not scan the model — MCP server error: {}"
                              ).format(res.get('error'))
                elif notes is None:
                    # Last resort — only works when Revit tolerates an
                    # off-thread read (idle).
                    notes = SC.collect_text_notes(self.doc, view_only=view_only)
                if report is not None:
                    pass          # scan error already reported above
                elif not notes:
                    if viet:
                        report = u"No Text Notes found{}.".format(
                            u" in the current view" if view_only else u" in the project")
                    else:
                        report = u"No Text Notes found{}.".format(
                            u" in the active view" if view_only else u" in the project")
                else:
                    uniq = SC.dedupe_notes(notes)
                    # PRIMARY detector — deterministic dictionary + edit-distance
                    # engine (no AI provider needed). This replaces "hand every
                    # batch to the connected LLM and trust the answer": small
                    # local models (llama3.1:8b, qwen...) routinely replied
                    # NO_ERRORS on obvious typos, so the scan looked clean while
                    # Claude-via-MCP flagged them all. The dictionary engine
                    # catches them offline and instantly.
                    det = SC.check_deterministic(uniq)
                    if det is not None:
                        if len(uniq) > 300:
                            self._safe_append_bot(
                                u"Đang phân tích {} nội dung ({} mục)…".format(
                                    len(uniq), len(notes)) if viet else
                                u"Analyzing {} unique texts ({} items)…".format(
                                    len(uniq), len(notes)))
                        findings, failed = det, 0
                        report = SC.format_report(findings, len(notes), len(uniq),
                                                  failed, viet, view_only)
                        ui_findings, ui_total, ui_uniq, ui_failed = \
                            findings, len(notes), len(uniq), failed
                    elif not (has_api_key() or has_local_llm()):
                        report = (
                            u"Đã tìm thấy **{}** mục text nhưng không tải được từ "
                            u"điển kiểm tra chính tả và chưa kết nối AI — cài lại "
                            u"extension hoặc kết nối provider trong Settings."
                            if viet else
                            u"Found **{}** text items but the spell-check "
                            u"dictionary could not be loaded and no AI provider is "
                            u"connected — reinstall the extension or connect a "
                            u"provider in Settings.").format(len(notes))
                    else:
                        # Fallback (dictionary asset missing) — legacy LLM batch
                        # path. Cloud providers take much larger batches than the
                        # small-local-model default (25 notes / 3.5k chars).
                        try:
                            _cloud = get_active_provider_name() in (
                                "claude", "openai")
                        except Exception:
                            _cloud = False
                        if _cloud:
                            batches = SC.build_batches(uniq, max_notes=120,
                                                       max_chars=15000)
                            _max_tok = 3000
                        else:
                            batches = SC.build_batches(uniq)
                            _max_tok = 1400
                        if len(batches) > 1:
                            self._safe_append_bot(
                                u"Found {} text items ({} unique texts) — checking "
                                u"in {} batches, please wait...".format(
                                    len(notes), len(uniq), len(batches)))
                        from Intelligence.llm_router import LLMRouter
                        router = LLMRouter()
                        sys_p  = SC.build_system_prompt(viet)
                        findings, failed = [], 0
                        for batch in batches:
                            if self._cancel_requested:
                                break
                            resp = None
                            try:
                                resp = router.chat([], sys_p,
                                                   SC.build_batch_query(batch),
                                                   max_tokens=_max_tok)
                            except Exception as ex:
                                logger.debug(u"spellcheck batch error: {}".format(_exc_text(ex)))
                            if not resp or not resp.strip():
                                failed += 1
                                continue
                            findings.extend(SC.parse_findings(resp, batch))
                        report = SC.format_report(findings, len(notes), len(uniq),
                                                  failed, viet, view_only)
                        ui_findings, ui_total, ui_uniq, ui_failed = \
                            findings, len(notes), len(uniq), failed
            except Exception as ex:
                logger.debug(u"spellcheck error: {}".format(_exc_text(ex)))
                report = (u"Spell-check failed — see the console for details."
                          if viet else u"Spell-check failed — see console for details.")
            if report:
                # Findings render as an interactive card (proposed fix +
                # clickable element-id links that select & zoom in Revit);
                # the plain-text report still goes to history so follow-up
                # "fix ..." turns have the full list, and stays the visible
                # fallback if the card fails to build.
                def _show(_r=report, _f=ui_findings, _t=ui_total,
                          _u=ui_uniq, _fb=ui_failed):
                    shown = False
                    if _f:
                        shown = self._append_spellcheck_findings(
                            _f, viet, _t, _u)
                    if not shown:
                        self._append_bot_message(_r)
                    elif _fb:
                        self._append_bot_message(
                            u"⚠️ {} nhóm không kiểm tra được (AI không phản "
                            u"hồi) — thử lại sau.".format(_fb) if viet else
                            u"⚠️ {} batch(es) could not be checked (no AI "
                            u"response) — try again.".format(_fb))
                    self._add_to_history("assistant", _r)
                self.Dispatcher.Invoke(Action(_show))
            self.Dispatcher.Invoke(Action(lambda: self._set_busy(False)))

        t = Thread(ThreadStart(work))
        t.IsBackground = True
        t.SetApartmentState(ApartmentState.STA)
        t.Start()

    def _run_tool(self, intent, default_msg):
        """Helper for quick-button clicks: guard, show message, run launcher."""
        if self._busy:
            self._append_bot_message(u"Still working on the previous request, please wait...",
                                     icon=_ICON_SYNC, icon_color=_ICON_SLATE)
            return
        self._set_busy(True)
        self._last_raw = default_msg
        self._append_bot_message(default_msg)
        self._add_to_history("assistant", default_msg)
        launcher = TOOL_LAUNCHERS.get(intent)
        if launcher:
            # Same API-context hop as the chat path — a quick button is still
            # a WPF click, not a Revit command. See _launch_tool.
            self._launch_tool(intent, launcher)
            return
        self._append_bot_message(self._unknown_tool_text(intent, default_msg))
        self._set_busy(False)

    # ─── Native agentic loop (function calling) ────────────────────────────────

    def _ranked_skill_ids(self, text, primary, limit=2):
        """Up to `limit` skill ids for this message, best-ranked first.

        `primary` is the dispatcher's own choice and always leads — the LLM
        classification stage may have picked a skill the keyword triggers
        never matched, and that verdict must not be discarded.
        """
        ids = [primary] if primary else []
        try:
            from Intelligence.skills_engine import get_skills_engine
            for sid in get_skills_engine().match(text):
                if sid not in ids:
                    ids.append(sid)
                if len(ids) >= limit:
                    break
        except Exception:
            pass
        return ids[:limit]

    def _build_knowledge_reference(self, query, local=False, history=None):
        """Retrieve a compact project-knowledge block to ground the tool agent.

        BM25-only (no embedder) so it stays fast and deterministic on every
        request — the lexical channel is exactly what carries local models.
        Returns a prompt fragment or '' (nothing relevant / no index).
        Budget is tighter for local models to protect their context window.

        `history` lets the retriever resolve a follow-up question against what
        was just discussed ("còn tầng 2 thì sao?" carries none of the nouns it
        needs on its own).
        """
        if not HAS_KNOWLEDGE:
            return u''
        try:
            from Intelligence.knowledge.knowledge_agent import (
                KnowledgeAgent, build_reference_block)
            agent = KnowledgeAgent(embedder=None)   # BM25-only: fast + always-on
            top_k = 2 if local else 3
            hits = agent.retrieve(query, top_k=top_k, history=history)
            if not hits:
                return u''
            return build_reference_block(
                hits, excerpt_chars=(500 if local else 800), max_items=top_k)
        except Exception as ex:
            logger.debug(u"knowledge reference build error: {}".format(_exc_text(ex)))
            return u''

    # ─── Graph path (multi-goal turns) ────────────────────────────────────────

    def _graph_enabled(self):
        """Kill switch + prerequisites for the graph agent layer."""
        if not (HAS_GRAPH and HAS_AGENTS and HAS_SPECIALISTS):
            return False
        try:
            from config.settings import get_settings
            settings = get_settings()
            return bool(settings.is_graph_mode_enabled()
                        and settings.is_multi_agent_enabled())
        except Exception:
            return True

    def _build_orchestrator(self, provider, history, viet):
        """A GraphOrchestrator whose runner is one `_run_native_agent` turn.

        max_parallel is pinned to 1: the runner streams into the chat
        transcript, and two agent turns writing into one transcript interleave
        into nonsense. The executor's parallel path is real and tested, but it
        belongs to headless callers — see Intelligence/graph/executor.py.
        """
        base_history = list(history)

        def _runner(node, context, state):
            if self._cancelled():
                # Tell the graph, not just this node: cancelling the state
                # stops the sweep between layers instead of failing every
                # remaining node one at a time.
                state.cancel()
                return None
            spec = get_spec(node.specialist) if node.specialist else None
            skill_ids = [node.skill] if node.skill else None
            # `context` carries what earlier nodes produced. It rides in front
            # of this node's own goal exactly like attachment RAG excerpts do.
            self._run_native_agent(
                provider, list(base_history), node.goal,
                spec=spec, skill_ids=skill_ids,
                rag_context=(context or None), hold_busy=True)
            return getattr(self, '_last_agent_text', u'') or None

        def _on_step(index, total, node):
            header = (u"**Bước {}/{}** — {}" if viet
                      else u"**Step {}/{}** — {}").format(
                          index, total, node.goal)

            def _show():
                self._append_bot_message(header, icon=_ICON_SYNC,
                                         icon_color=_ICON_SLATE)
                self._show_typing_indicator()
            self._ui_invoke(_show)

        skills_engine = None
        try:
            from Intelligence.skills_engine import SkillsEngine
            skills_engine = SkillsEngine()
        except Exception:
            pass

        return GraphOrchestrator(
            _runner, dispatcher=AgentDispatcher(), skills_engine=skills_engine,
            provider=provider, max_parallel=1, on_step=_on_step)

    # ─── Parallel task cards ──────────────────────────────────────────────────

    def _parallel_tasks_enabled(self):
        """Kill switch for concurrent task cards (agents.parallel_tasks).

        Default ON, but hard-gated downstream by eligible_for_parallel: it only
        ever fires for a genuinely writer-FREE multi-goal plan (>=2 read goals),
        so two model-writes can never interleave. Cancellable per card. Needs the
        graph layer it plugs into, so a disabled graph disables this too.
        """
        if not self._graph_enabled():
            return False
        try:
            from config.settings import get_settings
            return bool(get_settings().get_agent_option("parallel_tasks", True))
        except Exception:
            return True

    def _run_agent_text(self, provider, base_history, node, cancel_check=None):
        """Run ONE read goal to completion with NO streaming; return its text.

        The parallel counterpart of _build_orchestrator's per-node runner: it
        never touches the shared streaming bubble or self._last_agent_text
        (which concurrent tasks would clobber), returning the answer as a value
        instead. Read-only by construction — the parallel path is gated on a
        writer-free plan — so it needs neither the action-group wrapper nor the
        destructive-confirm card, and it withholds the launcher and memory tools
        (a background task must not open a window or race a fact write).
        Runs on a task worker thread. Never raises.
        """
        try:
            from Intelligence.agent_loop import AgentLoop, build_agent_system_prompt
            from Intelligence import tool_schema
            from core.server import get_t3labai_server
            srv = get_t3labai_server()
        except Exception as ex:
            logger.debug(u"parallel node setup failed: {}".format(_exc_text(ex)))
            return None

        spec = get_spec(node.specialist) if (node.specialist
                                             and HAS_SPECIALISTS) else None
        _is_local = provider.NAME in ("ollama", "lmstudio")

        # Read tools only — no launcher (terminal, opens a window) and no
        # memory pseudo-tool. Both would be wrong for a background sub-goal.
        _spec_tools = spec.tools_for(_is_local) if spec is not None else None
        if _spec_tools:
            tools = tool_schema.get_tools_by_names(provider.NAME, _spec_tools, [])
        else:
            tools = tool_schema.get_tools_for_provider(
                provider.NAME, [], essential_only=_is_local)
        if not tools:
            return None

        _lang = _reply_language()
        if _lang == 'auto':
            _lang = 'vi' if _is_viet_text(node.goal) else 'en'
        _proj_instructions, _mem_block = self._project_prompt_blocks()
        if spec is not None and HAS_SPECIALISTS:
            system_prompt = build_specialist_prompt(
                spec, project_instructions=_proj_instructions,
                skills_block=u"", local=_is_local, lang=_lang)
        else:
            system_prompt = build_agent_system_prompt(local=_is_local, lang=_lang)
            if _proj_instructions:
                system_prompt += u"\n\n## Project instructions\n" + _proj_instructions
        if _mem_block:
            system_prompt += u"\n\n" + _mem_block

        def _exec(name, args):
            # srv._execute_tool marshals anything main-thread-bound onto Revit's
            # thread via ExternalEvent — that queue is the real serializer, so
            # several read tasks contend there and nowhere else.
            return srv._execute_tool(name, dict(args or {}))

        # Bridge BOTH cancel signals into the loop's cooperative guard: the
        # window-wide cancel and this individual task's Cancel button.
        def _guard():
            try:
                if self._cancelled():
                    return True
                return bool(cancel_check and cancel_check())
            except Exception:
                return False

        loop = AgentLoop(provider, _exec, tools,
                         callbacks={"guard_check": _guard}, max_tokens=1500)
        try:
            res = loop.run(list(base_history), system_prompt, node.goal)
        except Exception as ex:
            logger.debug(u"parallel node run failed: {}".format(_exc_text(ex)))
            return None
        return ((res or {}).get("text") or u"").strip() or None

    def _run_parallel_tasks(self, plan, provider, history, viet):
        """Run an all-read multi-goal plan concurrently as task cards.

        Each goal becomes an AgentTask; its full answer is posted to the
        transcript ATOMICALLY when the task finishes (never streamed), which is
        what lets several run at once without their text interleaving. WORKER
        THREAD (marshals every UI touch through _ui_invoke).
        """
        from Intelligence.agents.task_manager import get_task_manager, DONE
        nodes = plan.agent_nodes()
        total = len(nodes)
        mgr = get_task_manager()
        base_history = list(history)

        state = {"cards": {}, "tasks": {}, "done": 0}

        # Build the card panel FIRST (all rows 'running') so a task that finishes
        # fast still finds its card. _ui_invoke is synchronous, so this returns
        # before any task is submitted.
        def _panel():
            self._hide_typing_indicator()
            state["cards"] = self._append_task_cards(nodes, state, mgr, viet)
        self._ui_invoke(_panel)

        def _on_done(task, _node):
            def _ui():
                self._update_task_card(state["cards"].get(_node.id), task, viet)
                if task.status == DONE and task.result:
                    self._append_bot_message(u"**{}**\n\n{}".format(
                        _node.goal, task.result))
                    self._add_to_history("assistant", task.result)
                state["done"] += 1
                if state["done"] >= total:
                    done_ok = sum(1 for t in state["tasks"].values()
                                  if t.status == DONE)
                    summary = (u"✔ Xong {}/{} tác vụ song song.".format(
                        done_ok, total) if viet else
                        u"✔ Finished {}/{} parallel tasks.".format(
                            done_ok, total))
                    self._append_bot_message(summary, icon=_ICON_SYNC,
                                             icon_color=_ICON_SLATE)
                    self._set_busy(False)
            self._ui_invoke(_ui)

        for node in nodes:
            def _fn(task, _n=node):
                return self._run_agent_text(provider, base_history, _n,
                                            cancel_check=task.is_cancelled)

            def _done(task, _n=node):
                _on_done(task, _n)
            t = mgr.submit(node.goal, _fn, writer=False, on_done=_done)
            state["tasks"][node.id] = t

    def _append_task_cards(self, nodes, state, mgr, viet):
        """Build the running/done/failed task-card panel. UI thread only.

        Returns {node_id: handle} for _update_task_card. Each card carries a
        Cancel button wired to mgr.cancel(task.id).
        """
        cards = {}
        try:
            from System.Windows.Controls import (Border, TextBlock, StackPanel,
                                                  Orientation, Button)
            from System.Windows import Thickness, CornerRadius, TextWrapping
            from System.Windows.Media import FontFamily

            outer = Border()
            _bind_bg(outer, 'SelectedBg')
            _bind_border(outer, 'CardBorder')
            outer.BorderThickness = Thickness(1)
            outer.CornerRadius    = CornerRadius(8)
            outer.Padding         = Thickness(12, 8, 12, 8)
            outer.Margin          = Thickness(0, 0, 8, 10)
            col = StackPanel()

            head = TextBlock()
            head.Text       = (u"Chạy {} tác vụ song song".format(len(nodes))
                               if viet else
                               u"Running {} tasks in parallel".format(len(nodes)))
            head.FontSize   = 12
            head.FontWeight = System.Windows.FontWeights.SemiBold
            _bind_fg(head, 'Ink')
            head.Margin     = Thickness(0, 0, 0, 6)
            col.Children.Add(head)

            for node in nodes:
                row = StackPanel()
                row.Orientation = Orientation.Horizontal
                row.Margin      = Thickness(0, 2, 0, 2)

                status = TextBlock()
                status.Text       = u""   # MDL2 Sync — running
                status.FontFamily = FontFamily(u"Segoe MDL2 Assets")
                status.FontSize   = 12
                _bind_fg(status, 'Blue')
                status.Margin     = Thickness(0, 1, 8, 0)

                goal = TextBlock()
                goal.Text         = node.goal
                goal.FontSize     = 12
                _bind_fg(goal, 'Ink')
                goal.TextWrapping = TextWrapping.Wrap
                goal.MaxWidth     = 320

                cancel = Button()
                cancel.Content   = (u"Hủy" if viet else u"Cancel")
                cancel.FontSize  = 10
                cancel.Margin    = Thickness(8, 0, 0, 0)
                cancel.Padding   = Thickness(6, 1, 6, 1)
                cancel.Cursor    = Cursors.Hand

                def _cancel_click(sender, args, _nid=node.id):
                    t = state["tasks"].get(_nid)
                    if t is not None:
                        mgr.cancel(t.id)
                cancel.Click += _cancel_click

                row.Children.Add(status)
                row.Children.Add(goal)
                row.Children.Add(cancel)
                col.Children.Add(row)
                cards[node.id] = {"status": status, "cancel": cancel}

            outer.Child = col
            self.chat_history_panel.Children.Add(outer)
            self._scroll_to_bottom()
        except Exception as ex:
            logger.debug(u"_append_task_cards error: {}".format(_exc_text(ex)))
        return cards

    def _update_task_card(self, handle, task, viet):
        """Flip a task row to its terminal state. UI thread only."""
        if not handle:
            return
        try:
            from Intelligence.agents.task_manager import DONE, FAILED, CANCELLED
            status = handle["status"]
            if task.status == DONE:
                status.Text = u""   # MDL2 CheckMark
                _bind_fg(status, 'Success')
            elif task.status == CANCELLED:
                status.Text = u""   # MDL2 Cancel
                _bind_fg(status, 'Muted')
            else:  # FAILED or anything non-terminal-looking
                status.Text = u""   # MDL2 Cancel
                _bind_fg(status, 'Danger')
            # A finished task can no longer be cancelled.
            try:
                handle["cancel"].IsEnabled = False
                handle["cancel"].Visibility = Visibility.Collapsed
            except Exception:
                pass
        except Exception as ex:
            logger.debug(u"_update_task_card error: {}".format(_exc_text(ex)))

    def _run_graph_plan(self, raw, history, utterance=None):
        """Plan and run a MULTI-GOAL turn as a graph. WORKER THREAD.

        Returns True when the request was handled here. False means the
        message was not multi-goal (or the layer is off / unavailable) and the
        caller must fall through to its normal single-specialist path —
        nothing has been shown to the user in that case.
        """
        if not self._graph_enabled():
            return False
        try:
            from Intelligence.llm_router import LLMRouter
            provider = LLMRouter().get_active_provider()
        except Exception:
            return False
        if provider is None or not getattr(provider, 'SUPPORTS_NATIVE_TOOLS',
                                           False):
            # Every node is an agent turn; without native tools there is no
            # agent loop to run them in.
            return False

        viet = _is_viet_text(raw)
        rid = getattr(self, '_request_id', 0)

        # Decide BEFORE anything is shown: a plan the layer declines must leave
        # the turn exactly as it found it, so the caller can fall through.
        try:
            orchestrator = self._build_orchestrator(provider, history, viet)
            plan = orchestrator.plan_for(raw, utterance=utterance)
        except Exception as ex:
            logger.debug("graph plan error: {}".format(ex))
            return False
        if plan is None or not plan.is_multi:
            return False
        logger.debug("graph plan:\n{}".format(plan.describe()))

        # Parallel task cards: an all-READ multi-goal plan can run its goals at
        # once as cancellable cards instead of one-at-a-time. Gated hard (opt-in
        # switch + writer-free) — see task_manager.eligible_for_parallel. Any
        # writer present, or the switch off, falls straight through to the
        # sequential orchestrator below, exactly as before.
        try:
            from Intelligence.agents.task_manager import eligible_for_parallel
            if eligible_for_parallel(plan.is_multi, len(plan.agent_nodes()),
                                     plan.has_writer(),
                                     self._parallel_tasks_enabled()):
                self._run_parallel_tasks(plan, provider, history, viet)
                return True
        except Exception as ex:
            logger.debug(u"parallel tasks skipped: {}".format(_exc_text(ex)))

        # Past this point the plan owns the turn: its nodes stream into the
        # transcript, so falling through afterwards would answer twice.
        try:
            result = orchestrator.handle(
                raw, utterance=utterance, plan=plan,
                lang=('vi' if viet else 'en'))
        except Exception as ex:
            self._report_error(u"graph plan", ex, rid)
            return True

        if self._cancelled():
            self._finish_cancelled(rid)
            return True

        def _finish():
            self._hide_typing_indicator()
            summary = self._graph_summary(result, viet)
            if summary:
                self._append_bot_message(summary, icon=_ICON_SYNC,
                                         icon_color=_ICON_SLATE)
                self._add_to_history("assistant", summary)
            self._set_busy(False)

        if self._claim_turn(rid):
            self._ui_invoke(_finish)
        return True

    @staticmethod
    def _graph_summary(result, viet):
        """Closing line for a graph turn.

        Every node already streamed its own answer into the transcript, so
        repeating the reducer's full concatenation here would show the user the
        same text twice. What is NOT otherwise visible is which steps did not
        happen — so the summary is a tally, plus the reducer's own report for
        the steps that failed.
        """
        plan, state = result.plan, result.state
        if plan is None or state is None:
            return u''
        nodes = plan.agent_nodes()
        failed = [n for n in nodes if not state.succeeded(n.id)]
        total = len(nodes)
        if not failed:
            return (u"✔ Đã hoàn thành cả {} bước.".format(total) if viet
                    else u"✔ All {} steps completed.".format(total))
        try:
            from Intelligence.graph.reducer import Reducer
            detail = Reducer().reduce(plan.graph, state,
                                      node_ids=[n.id for n in failed],
                                      lang=('vi' if viet else 'en'))
        except Exception:
            detail = u''
        head = (u"Hoàn thành {}/{} bước.".format(total - len(failed), total)
                if viet else
                u"Completed {}/{} steps.".format(total - len(failed), total))
        return u"{}\n\n{}".format(head, detail) if detail else head

    @staticmethod
    def _analysis_hint(text, analysis=None):
        """Per-message language analysis for the volatile context block.

        Returns u'' when the language layer is unavailable or the sentence is
        plain enough to need no help, so an ordinary turn costs no extra
        tokens. Never raises — a hint is an optimisation, not a requirement.
        """
        if not HAS_LANGUAGE or not (text or u'').strip():
            return u''
        try:
            utt = analysis if analysis is not None else _lang_analyzer.analyze(
                text)
            lang = _reply_language()
            return utt.to_prompt_hint(
                lang=(lang if lang in ('vi', 'en') else None))
        except Exception:
            return u''

    def _note_native_skip(self, reason):
        """Record WHY the native tool-calling path was skipped.

        These used to be logger.debug only. pyRevit's debug log is off by
        default, so every skip was invisible: the turn quietly dropped to the
        legacy JSON-intent path, and when that path then failed to parse a
        local model's prose the user just saw "Could not read data from the
        model" with nothing anywhere explaining that the good path never ran.
        """
        msg = u"native path skipped: {}".format(reason)
        logger.debug(msg)
        try:
            self._log_activity(msg[:600])
        except Exception:
            pass

    def _run_native_agent(self, provider, history, captured,
                          spec=None, skill_ids=None, rag_context=None,
                          image_files=None, analysis=None, hold_busy=False):
        """Run the native tool-calling agent loop. WORKER THREAD.

        spec: optional SpecialistSpec — narrows the tool catalog, adds a
        role block to the system prompt and tightens the loop budget
        (multi-agent layer). None = today's exact behavior.
        skill_ids: optional matched skill ids injected into the prompt.
        rag_context: optional compact retrieval excerpts (attachment RAG)
        prepended to the user content only.
        image_files: attached image paths sent as real vision blocks. The
        caller only passes them when the provider reports SUPPORTS_VISION.
        analysis: optional pre-built language.Utterance for `captured`, reused
        for the per-turn analysis hint instead of re-analysing the message.
        hold_busy: keep the busy state ON after this turn. Set by the graph
        path, which runs several of these back to back for ONE user message —
        releasing the composer between steps would let a new request land in
        the middle of a running plan.

        Returns True when the request was fully handled (UI updated, busy
        released unless hold_busy). Returns False so the legacy JSON-intent
        path can run — only when nothing was shown to the user yet.

        The answer text is also left on `self._last_agent_text`, which the
        graph path reads to pass one node's result to the next.
        """
        self._last_agent_text = u""
        if provider is None or not getattr(provider, "SUPPORTS_NATIVE_TOOLS", False):
            self._note_native_skip(
                u"provider {} has no native tools".format(
                    getattr(provider, 'NAME', provider)))
            return False

        from Intelligence.agent_loop import AgentLoop, build_agent_system_prompt
        from Intelligence import tool_schema

        try:
            from core.server import get_t3labai_server
            srv = get_t3labai_server()
        except Exception as _srv_ex:
            self._note_native_skip(u"MCP server unavailable ({})".format(
                _exc_text(_srv_ex)))
            return False

        launcher = tool_schema.make_launcher_tool(list(TOOL_LAUNCHERS.keys()))
        # Local providers get a curated subset: the full ~110 schemas
        # overflow local context windows and small models pick tools far
        # less accurately from a huge catalog (cloud providers are fine).
        _is_local = provider.NAME in ("ollama", "lmstudio")
        _spec_tools = spec.tools_for(_is_local) if spec is not None else None
        _use_launcher = spec.use_launcher if spec is not None else True
        _extras = [launcher] if _use_launcher else []
        # Persistent-memory tool: always available so the model can save a
        # durable preference the moment the user states one. Executed
        # locally in _exec_tool — never reaches the Revit server.
        _extras.append(tool_schema.make_memory_tool())
        # Matched skills may declare extra tools their playbook needs —
        # union them into a restricted subset (validated by the registry).
        if _spec_tools and skill_ids:
            try:
                from Intelligence.skills_engine import get_skills_engine
                _sk_tools = set()
                for _sid in skill_ids:
                    _sk_tools.update(get_skills_engine().tools_for(_sid))
                if _sk_tools:
                    _spec_tools = frozenset(_spec_tools) | _sk_tools
            except Exception:
                pass
        if _spec_tools:
            tools = tool_schema.get_tools_by_names(
                provider.NAME, _spec_tools, _extras)
        else:
            tools = tool_schema.get_tools_for_provider(
                provider.NAME, _extras, essential_only=_is_local)
        if len(tools) <= len(_extras):   # registry unavailable
            self._note_native_skip(
                u"tool registry empty for provider {} (got {} tools, {} of them "
                u"extras)".format(provider.NAME, len(tools), len(_extras)))
            return False

        ctx = u""
        if HAS_SCOUT:
            try:
                ctx = ContextScout.get_context_summary_for_ai()
            except Exception:
                pass
        # Prepend the cached open-model digest (element counts / health) so the
        # agent starts grounded in the real model — a local model then spends
        # its round-trips on the task, not on re-discovering the project.
        _snap = self._model_snapshot_grounding()
        if _snap:
            ctx = u"{}\n\n{}".format(ctx, _snap) if ctx else _snap

        _proj_instructions, _mem_block = self._project_prompt_blocks()
        _skills_block = u""
        try:
            if skill_ids:
                from Intelligence.skills_engine import build_skills_block
                _skills_block = build_skills_block(skill_ids)
        except Exception:
            pass

        # The model must answer in the same language the window renders in,
        # otherwise a Vietnamese turn gets Vietnamese tool cards wrapped
        # around an English answer.
        _lang = _reply_language()
        if _lang == 'auto':
            _lang = 'vi' if _is_viet_text(captured) else 'en'

        # ── STATIC system prompt ──────────────────────────────────────────────
        # Everything below is stable for the session, which is the whole point:
        # it sits behind ONE prompt-cache breakpoint. Anything that changes per
        # turn (live Revit state, per-question knowledge excerpts) goes into
        # _volatile below and rides along with the user turn instead — putting
        # it here made the system block never repeat, so the cache never hit.
        if spec is not None and HAS_SPECIALISTS:
            system_prompt = build_specialist_prompt(
                spec, project_instructions=_proj_instructions,
                skills_block=_skills_block, local=_is_local, lang=_lang)
        else:
            system_prompt = build_agent_system_prompt(local=_is_local,
                                                      lang=_lang)
            if _proj_instructions:
                system_prompt += u"\n\n## Project instructions\n" + _proj_instructions
            if _skills_block:
                system_prompt += u"\n\n" + _skills_block

        # Persistent memory — facts saved in previous chats steer BOTH the
        # specialist and the default prompt path.
        if _mem_block:
            system_prompt += u"\n\n" + _mem_block

        # Rolling summary of turns that scrolled out of the window. Static
        # enough to belong here: it only changes when turns fall out (every
        # few turns), so the prompt cache still hits in between — unlike live
        # Revit state, which changes every turn and rides the user message.
        try:
            from Intelligence.conversation import summary_block
            _sum_block = summary_block(
                getattr(self, '_history_summary', u''), viet=(_lang == 'vi'))
            if _sum_block:
                system_prompt += u"\n\n" + _sum_block
        except Exception:
            pass

        # ── VOLATILE context (travels with the user turn) ─────────────────────
        # Project-knowledge grounding: a compact reference block so the tool
        # agent's ANSWERS and ACTIONS follow documented standards, not just the
        # knowledge specialist. Retrieved from the user's request (BM25); a
        # stray hit on an unrelated command is harmless (the header tells the
        # model to ignore irrelevant excerpts). knowledge/comment specialists
        # have their own retrieval pipeline and never reach this path.
        _kref = u""
        try:
            _spec_name = spec.name if spec is not None else 'general'
            if _spec_name not in ('knowledge', 'comment'):
                _kref = self._build_knowledge_reference(
                    captured, local=_is_local, history=history) or u""
        except Exception:
            _kref = u""

        # Harness action mode: 'confirm' = propose-then-wait before ANY
        # model-modifying tool call (chip next to the project picker).
        try:
            from config.settings import get_settings as _gs_mode
            if _gs_mode().get_action_mode() == 'confirm':
                system_prompt += (
                    u"\n\n## ACTION MODE: CONFIRM FIRST\n"
                    u"Before calling ANY tool that modifies the model "
                    u"(create/set/bulk/move/rotate/rename/delete/join/split/"
                    u"purge/load/place/tag/color...), REPLY first with a "
                    u"short plan: which tools, which elements and how many "
                    u"are affected — then STOP and wait for the user's "
                    u"confirmation in the next message. Read-only tools "
                    u"(get/list/query/analyze/export) may be called "
                    u"immediately without asking.")
        except Exception:
            pass

        viet = _is_viet_text(captured)

        # ── C2: vision ────────────────────────────────────────────────────────
        # Two sources of images, and they compose: files the user attached
        # (paperclip / paste / drag-drop) and, for "look at this view", a
        # snapshot of the active view. Both ship as Claude-format image
        # blocks — claude_provider sends them natively, openai_provider
        # converts them to image_url; providers without SUPPORTS_VISION never
        # get here (the caller keeps them on the text path).
        user_content = captured
        if rag_context:
            user_content = rag_context + u"\n\n" + captured
        _vision_files = list(image_files or [])
        if self._wants_view_snapshot(captured, provider):
            self._safe_update_typing_text(
                u"● ● ●  Đang chụp active view…" if viet
                else u"● ● ●  Capturing the active view…")
            shot = self._capture_active_view(srv)
            if shot:
                _vision_files.append(shot)
        if _vision_files:
            try:
                from Intelligence.rag_processor import build_vision_content_blocks
                # Text goes in last (the builder's own contract), so the
                # RAG/attachment context stays attached to the question.
                user_content = build_vision_content_blocks(
                    user_content, _vision_files)
            except Exception as _vx:
                logger.debug(u"vision block build error: {}".format(_exc_text(_vx)))

        # Live state rides with THIS turn instead of the cached system block.
        # It never reaches _conversation_history (which stores the raw user
        # text), so it stays ephemeral and the transcript does not grow.
        try:
            from Intelligence.agent_loop import (build_context_block,
                                                 apply_context_block)
            _volatile = build_context_block(revit_context=ctx,
                                            knowledge_ref=_kref,
                                            analysis_hint=self._analysis_hint(
                                                captured, analysis))
            user_content = apply_context_block(user_content, _volatile)
        except Exception as _ctx_ex:
            logger.debug(u"context block build error: {}".format(_exc_text(_ctx_ex)))

        # ── B5: tool-execution wrapper ────────────────────────────────────────
        # Destructive tools block on an in-chat Confirm/Cancel card; the first
        # purge_unused of a request is always forced to dry_run.
        #
        # B4 (one request = one TransactionGroup = one Undo entry) USED to live
        # here and is gone. A TransactionGroup cannot outlive the
        # ExternalEvent.Execute that opened it — Revit force-discards every
        # phase an event handler leaves open, logging "An API event handler
        # left some transaction phases open for the active document" once per
        # write. The group therefore never grouped anything, while the stale
        # handle it left behind was Assimilate()d later and took the whole
        # process down on the next request. See the withdrawal note on
        # __begin_action_group in lib/core/server.py before reinstating any of
        # this: each tool now commits its own transaction, so a multi-step
        # request is N undo entries.
        purge = {"first_done": False}
        # Belt and braces: an AppDomain-anchored server from before a pyRevit
        # reload may still be carrying a group handle from the old code.
        try:
            srv._release_stale_action_group()
        except Exception:
            pass

        # ── D1: cancel the loop when the USER switches documents mid-request ──
        # Agent-initiated document changes (switch_active_document /
        # open_document / close_document called by the model for a multi-doc
        # workflow) DISARM the guard for the rest of the request instead of
        # tripping it — the agent is deliberately working across models.
        #
        # Declared HERE, above _exec_tool, because _exec_tool closes over it.
        # It used to be assigned ~50 lines further down and only worked
        # because nothing called _exec_tool in between; any reordering would
        # have turned that into a NameError at the first tool call.
        doc_guard = {"key": _get_doc_key(), "armed": True}

        def _exec_tool(name, args):
            args = dict(args or {})
            if name == tool_schema.MEMORY_TOOL_NAME:
                # Local pseudo-tool — persists a fact, never touches Revit.
                try:
                    from Intelligence import assistant_memory
                    _pid = None
                    try:
                        from config.project_store import ProjectStore
                        _pid = ProjectStore().get_active_project_id()
                    except Exception:
                        pass
                    _action = (args.get('action') or 'save').lower()
                    _fact = args.get('fact')
                    _scope = args.get('scope') or 'project'
                    _replaces = args.get('replaces')
                    if _action == 'forget':
                        # `replaces` names the fact to drop; fall back to `fact`
                        # if the model put the gist there instead.
                        _ok, _removed = assistant_memory.forget_fact(
                            _replaces or _fact, project_id=_pid)
                        if _ok:
                            return {"success": True,
                                    "note": u"Forgot: {}".format(_removed)}
                        return {"error": u"No matching fact to forget."}
                    if _action == 'update':
                        # Supersede the prior fact named by `replaces` with the
                        # new `fact`; update_fact falls back to a save if the old
                        # one isn't found, so the correction still lands.
                        _ok, _note = assistant_memory.update_fact(
                            _replaces or _fact, _fact,
                            scope=_scope, project_id=_pid)
                        if _ok:
                            return {"success": True, "note": _note}
                        return {"error": _note}
                    _ok, _note = assistant_memory.add_fact(
                        _fact, scope=_scope, project_id=_pid)
                    if _ok:
                        return {"success": True, "note": _note}
                    return {"error": _note}
                except Exception as _mem_ex:
                    return {"error": u"{}".format(_mem_ex)}
            if name == 'purge_unused' and not purge["first_done"]:
                purge["first_done"] = True
                if not bool(args.get('dry_run', True)):
                    args['dry_run'] = True   # first pass is ALWAYS a report
            # What counts as destructive is declared with the tools themselves
            # (server._DESTRUCTIVE_TOOLS / _DESTRUCTIVE_OPS) so a new tool can
            # opt in at the point of definition instead of needing an edit here.
            destructive = srv.is_destructive(name, args)
            if destructive and not self._confirm_tool_blocking(name, args, viet):
                return {"cancelled": True,
                        "note": "User declined the '{}' action.".format(name)}
            res = srv._execute_tool(name, args)
            # Multi-doc workflow: the model changed the active document ON
            # PURPOSE — the doc-changed guard must not abort the request.
            # Activation can complete asynchronously, so retargeting the key
            # here could race; disarming for the rest of the request is the
            # safe, predictable behavior.
            if name in ('switch_active_document', 'open_document',
                        'close_document'):
                try:
                    if isinstance(res, dict) and 'error' not in res:
                        doc_guard["armed"] = False
                except Exception:
                    doc_guard["armed"] = False
            return res

        def _exec_reads_batch(batch):
            """Run a run of read-only calls in ONE crossing to Revit.

            Only reachable for plain reads (agent_loop.leading_read_run stops
            at the first write, launcher or memory call), so none of the
            _exec_tool preamble above — the action group, the destructive
            confirm card, the purge dry-run forcing, the doc-guard disarm —
            can apply to anything in here.

            This used to call srv.execute_tools_batch, which the server has
            never defined. AgentLoop wraps the call in `except Exception:
            return {}`, so the AttributeError was swallowed and batching
            silently never engaged — one dead round-trip per turn, invisible.
            Executed here instead: still ONE seam for the loop, and correct.
            `batch` is [(name, args), ...]; the return must be a list of the
            same length or AgentLoop discards it.
            """
            out = []
            for _name, _args in batch:
                out.append(srv._execute_tool(_name, _args))
            return out

        def _guard_check():
            try:
                if not doc_guard["armed"]:
                    return False
                cur = _get_doc_key()
                # "default" = the read failed (API busy / no context) — that is
                # UNKNOWN, not "changed"; only trip on a positive mismatch.
                return cur != "default" and cur != doc_guard["key"]
            except Exception:
                return False

        import time as _time
        stream = {"text": u"", "last": 0.0, "open": False}
        card   = {"cur": None}

        # ── Throttled live-stream rendering ───────────────────────────────────
        # Deltas are batched: at most ~25 UI updates/second, pushed with
        # BeginInvoke so the worker never blocks on the dispatcher. Only
        # state-transition callbacks below use the synchronous Invoke.
        def _push_stream(force):
            now = _time.time()
            if not force and (now - stream["last"]) < 0.04:
                return
            stream["last"] = now
            snap = _hide_reasoning(stream["text"])

            def _ui():
                try:
                    if not stream["open"]:
                        # Nothing visible yet (model still "thinking") —
                        # keep the typing indicator, don't open a bubble.
                        if not snap:
                            return
                        stream["open"] = True
                        self._hide_typing_indicator()
                        self._begin_stream_bubble()
                    if self._stream_tb is not None:
                        self._stream_tb.Text = snap
                        self._scroll_to_bottom()
                except Exception:
                    pass

            try:
                self.Dispatcher.BeginInvoke(Action(_ui))
            except Exception:
                pass

        def on_text_delta(chunk):
            self._mark_first_token()
            stream["text"] += chunk
            _push_stream(False)

        def on_turn_text(text, is_final):
            text = _hide_reasoning(text)
            stream["text"] = u""
            if not text:
                # Reasoning-only turn (everything was <think>) — never render
                # an empty bubble; discard any half-open one and move on.
                def _drop():
                    try:
                        if stream["open"]:
                            stream["open"] = False
                            self._remove_stream_bubble()
                    except Exception:
                        pass
                try:
                    self.Dispatcher.Invoke(Action(_drop))
                except Exception:
                    pass
                return

            def _ui():
                try:
                    # "Show thinking" off hides the INTERIM narration only —
                    # the final answer always renders. The text still goes to
                    # history (the model must see its own turn) and still
                    # drives the typing indicator, so the user keeps a live
                    # signal instead of staring at a frozen window.
                    if not is_final and not self._chat_verbosity()[1]:
                        if stream["open"]:
                            stream["open"] = False
                            self._remove_stream_bubble()
                            self._clear_stream_refs()
                        self._show_typing_indicator()
                        if getattr(self, "_typing_text_block", None) is not None:
                            _line = u" ".join(text.split())[:80]
                            self._typing_text_block.Text = u"● ● ●  {}".format(_line)
                        self._add_to_history("assistant", text)
                        return
                    if stream["open"]:
                        stream["open"] = False
                        self._finalize_stream_bubble(text)
                        self._clear_stream_refs()
                    else:
                        self._hide_typing_indicator()
                        self._append_bot_message(text)
                    self._add_to_history("assistant", text)
                except Exception:
                    pass

            try:
                self.Dispatcher.Invoke(Action(_ui))
            except Exception:
                pass

        def on_tool_start(name, args, iteration):
            def _ui():
                try:
                    self._hide_typing_indicator()
                    card["cur"] = self._append_tool_card(name, args)
                    self._show_typing_indicator()
                    if getattr(self, "_typing_text_block", None) is not None:
                        self._typing_text_block.Text = (
                            u"● ● ●  Running `{}`…".format(name))
                except Exception:
                    pass

            try:
                self.Dispatcher.Invoke(Action(_ui))
            except Exception:
                pass

        def on_tool_done(name, result, ok, seconds):
            def _ui():
                try:
                    self._update_tool_card(card["cur"], ok, seconds, result)
                except Exception:
                    pass

            try:
                self.Dispatcher.Invoke(Action(_ui))
            except Exception:
                pass

        # Skill chip — show which playbook is steering this turn. A /slash-
        # forced skill already drew its chip at send time (_process_input),
        # so drawing it here again would duplicate the row.
        _dec_chip = getattr(self, '_agent_decision', None)
        if skill_ids and not (_dec_chip and _dec_chip.get('skill_forced')):
            def _show_chips():
                try:
                    self._append_skill_chips(skill_ids)
                except Exception:
                    pass
            try:
                self.Dispatcher.Invoke(Action(_show_chips))
            except Exception:
                pass

        loop = AgentLoop(
            provider, _exec_tool, tools,
            callbacks={
                "on_text_delta": on_text_delta,
                "on_turn_text":  on_turn_text,
                "on_tool_start": on_tool_start,
                "on_tool_done":  on_tool_done,
                "guard_check":   _guard_check,
            },
            max_iterations=(spec.max_iterations if spec is not None else 10),
            max_tokens=(spec.max_tokens if spec is not None else 1500),
            turn_timer=getattr(self, '_turn_timer', None),
            execute_tools_batch=_exec_reads_batch,
            # tool_schema.is_model_modifying, NOT srv.is_write_tool — the
            # server has never had that method. Attribute access raises before
            # AgentLoop is even constructed, so EVERY Revit request through the
            # pane died here and fell through to the legacy JSON path, which a
            # local model's prose then failed to parse: "Could not read data
            # from the model." The native path had simply never run.
            is_write_tool=tool_schema.is_model_modifying)

        self._agent_loop = loop
        if self._cancel_requested:
            loop.cancel()
        try:
            result = loop.run(history, system_prompt, user_content)
        finally:
            self._agent_loop = None

        # Provider never answered turn 1 and nothing reached the UI →
        # hand back to the legacy path (it has its own fallbacks).
        if (result.get("status") == "failed"
                and result.get("iterations", 0) <= 1
                and not result.get("text")
                and not result.get("tool_runs")
                and not stream["open"]):
            # Log WHY. This handover is invisible to the user: the legacy path
            # answers without native tools, so a data question can come back as
            # a confident prose stub with no tool call behind it. The provider
            # recorded a real reason (bad key, no model, HTTP/tool-payload
            # error) — losing it here is what made that look like the model
            # simply "chose" not to use tools.
            try:
                logger.debug(u"native path: turn 1 failed ({}) -> legacy "
                             u"fallback".format(provider.get_last_error()
                                                or u"no reason recorded"))
            except Exception:
                pass
            return False

        # Model "answered" with an EMPTY turn — no text, no tool calls
        # (local models do this when a playbook/tool catalog confuses
        # them). Nothing has reached the UI, so ending here left the user
        # with just a skill chip and total silence (observed 2026-07-22:
        # "khối lượng bê tông" → schedule-qto chip → nothing). Fall back
        # to the legacy JSON-intent path instead — it always ends with a
        # visible reply (answer, keyword fallback, or a real error).
        if (result.get("status") == "done"
                and not (result.get("text") or u"").strip()
                and not result.get("tool_runs")
                and not stream["open"]):
            logger.debug("native path: empty model turn -> legacy fallback")
            return False

        def _finish_ui():
            try:
                # A turn interrupted mid-stream leaves an open live bubble.
                if stream["open"]:
                    stream["open"] = False
                    txt = stream["text"]
                    if txt.strip():
                        self._finalize_stream_bubble(txt)
                        self._add_to_history("assistant", txt)
                    else:
                        # Nothing visible ever landed — drop the shell
                        # instead of leaving an empty bubble in the chat.
                        self._remove_stream_bubble()
                    self._clear_stream_refs()
                self._hide_typing_indicator()

                st = result.get("status")
                if st == "cancelled":
                    self._append_bot_message(
                        u"Stopped as requested." if viet else u"Stopped.",
                        icon=_ICON_STOP, icon_color=_ICON_SLATE)
                elif st == "doc_changed":
                    self._append_bot_message(
                        (u"You switched to another document — the request was "
                         u"cancelled to avoid editing the wrong model.") if viet else
                        (u"The active document changed — request cancelled "
                         u"to avoid editing the wrong model."),
                        icon=_ICON_WARNING, icon_color=_ICON_AMBER)
                elif st == "failed":
                    label = get_provider_display_label()
                    detail = u""
                    try:
                        _le = provider.get_last_error()
                        if _le:
                            detail = (u"\nDetails: {}".format(_le) if viet
                                      else u"\nDetail: {}".format(_le))
                    except Exception:
                        pass
                    self._append_bot_message(
                        (u"Model AI ({}) bị ngắt giữa chừng — kết quả có thể "
                         u"chưa đầy đủ. Vui lòng thử lại.{}".format(label, detail))
                        if viet else
                        (u"The AI model ({}) dropped mid-request — the result "
                         u"may be incomplete. Please retry.{}".format(label, detail)),
                        icon=_ICON_WARNING, icon_color=_ICON_AMBER)
                    # One-click resend of the exact same request.
                    if self._last_raw and not self._last_raw.startswith(u"["):
                        self._append_quick_replies(
                            [u"Thử lại" if viet else u"Retry"],
                            [self._last_raw])
                elif st in ("max_iterations", "timeout"):
                    self._append_bot_message(
                        (u"The request is too long — stopped after {} steps. Break "
                         u"it into smaller requests to continue.").format(result.get("iterations"))
                        if viet else
                        (u"Request too long — stopped after {} steps. Split it "
                         u"up to continue.").format(result.get("iterations")),
                        icon=_ICON_WARNING, icon_color=_ICON_AMBER)
                    self._append_quick_replies(
                        [u"Tiếp tục" if viet else u"Continue"],
                        [u"tiếp tục phần còn lại" if viet
                         else u"continue where you stopped"])
                elif (st == "done" and not result.get("text")
                        and result.get("tool_runs")):
                    self._append_bot_message(
                        u"Completed {} tool steps.".format(
                            result.get("tool_runs")) if viet else
                        u"Completed {} tool step(s).".format(
                            result.get("tool_runs")),
                        icon=_ICON_SUCCESS, icon_color=_ICON_GREEN)

                # Confirm-question flow: when the reply ends by asking the
                # user to approve an action (action-mode plans, destructive
                # confirmations, rule-5 clarifications phrased as yes/no),
                # offer one-click answers instead of making the user type
                # "ok". The short chip reply then rides the carryover
                # continuation, so the same specialist/skill resumes.
                if st == "done":
                    _tail = (result.get("text") or u"")[-220:].lower()
                    if u"?" in _tail and any(k in _tail for k in (
                            u"confirm", u"proceed", u"shall i", u"should i",
                            u"go ahead", u"do you want", u"want me to",
                            u"xác nhận", u"đồng ý", u"tiến hành",
                            u"thực hiện", u"ok?", u"okay?")):
                        self._append_quick_replies(
                            [u"Đồng ý, làm đi", u"Không, hủy"] if viet else
                            [u"Yes, go ahead", u"No, cancel"])

                li = result.get("launch_intent")
                if li:
                    # Terminal launcher: _execute_result opens the window on
                    # the UI thread and releases the busy state itself.
                    self._execute_result({"intent": li, "message": u"",
                                          "params": {}})
                elif not hold_busy:
                    self._set_busy(False)
            except Exception as ex:
                self._report_error(u"native agent finish", ex)

        self.Dispatcher.Invoke(Action(_finish_ui))
        # What this turn actually said, for a caller chaining several turns
        # together (the graph path). The streamed text is the fallback: a turn
        # that streamed its whole answer can finish with result["text"] empty.
        self._last_agent_text = ((result.get("text") or u"").strip()
                                 or (stream.get("text") or u"").strip())
        return True

    # ─── Tool-call cards ───────────────────────────────────────────────────────

    # ── Compact tool-call group ──────────────────────────────────────────────
    # With "Show tool calls" off, a turn's cards do not vanish — they move
    # into ONE collapsible row ("✓ 5 tools · 3.2s") that expands on click.
    # Vanishing was the tempting option and the wrong one: a failing call
    # would disappear with it, and the next question would be "why did it do
    # nothing?" — the same blind wait this whole setting exists to remove.

    def _chat_verbosity(self):
        """(show_tool_calls, show_thinking) — never raises."""
        try:
            from config.settings import get_settings
            s = get_settings()
            return (s.is_show_tool_calls_enabled(),
                    s.is_show_thinking_enabled())
        except Exception:
            return (True, True)

    def _reset_tool_group(self):
        """Drop the current turn's compact group so the next turn starts a
        fresh one. Called when a new user message is sent."""
        self._tool_group = None

    def _ensure_tool_group(self):
        """The collapsible container for this turn, created on first tool."""
        group = getattr(self, '_tool_group', None)
        if group is not None:
            return group
        from System.Windows.Controls import Border, TextBlock, StackPanel, Orientation
        from System.Windows import Thickness, CornerRadius
        from System.Windows.Media import FontFamily
        from System.Windows.Input import Cursors

        shell = Border()
        _bind_bg(shell, 'SelectedBg')
        _bind_border(shell, 'CardBorder')
        shell.BorderThickness = Thickness(1)
        shell.CornerRadius    = CornerRadius(8)
        shell.Padding         = Thickness(12, 8, 12, 8)
        shell.Margin          = Thickness(0, 0, 8, 10)

        outer = StackPanel()

        head = StackPanel()
        head.Orientation = Orientation.Horizontal
        head.Cursor      = Cursors.Hand

        chevron = TextBlock()
        chevron.Text       = u""          # MDL2 ChevronRight — collapsed
        chevron.FontFamily = FontFamily(u"Segoe MDL2 Assets")
        chevron.FontSize   = 10
        _bind_fg(chevron, 'Faint')
        chevron.Margin     = Thickness(0, 2, 8, 0)

        label = TextBlock()
        label.FontSize = 11.5
        _bind_fg(label, 'Muted')

        head.Children.Add(chevron)
        head.Children.Add(label)
        outer.Children.Add(head)

        body = StackPanel()
        body.Margin     = Thickness(0, 8, 0, 0)
        body.Visibility = Visibility.Collapsed
        outer.Children.Add(body)

        group = {'shell': shell, 'label': label, 'chevron': chevron,
                 'body': body, 'total': 0, 'failed': 0, 'seconds': 0.0,
                 'running': u''}

        def _toggle(sender, e, _g=group):
            expanded = _g['body'].Visibility == Visibility.Visible
            _g['body'].Visibility = (Visibility.Collapsed if expanded
                                     else Visibility.Visible)
            _g['chevron'].Text = u"" if expanded else u""   # right / down
            self._scroll_to_bottom()

        head.MouseLeftButtonUp += _toggle

        shell.Child = outer
        self.chat_history_panel.Children.Add(shell)
        self._tool_group = group
        self._refresh_tool_group_label(group)
        return group

    def _refresh_tool_group_label(self, group):
        running = group.get('running')
        if running:
            group['label'].Text = (
                u"Đang chạy `{}`… ({} tool)".format(running, group['total'])
                if _ui_viet() else
                u"Running `{}`… ({} tools)".format(running, group['total']))
            return
        done = group['total'] - group['failed']
        parts = [u"{} tool".format(done) if _ui_viet()
                 else u"{} tool{}".format(done, u"" if done == 1 else u"s")]
        if group['failed']:
            parts.append(u"{} lỗi".format(group['failed']) if _ui_viet()
                         else u"{} failed".format(group['failed']))
        parts.append(u"{0:.1f}s".format(group['seconds']))
        group['label'].Text = u"{}  {}".format(
            u"" if group['failed'] else u"", u" · ".join(parts))

    def _append_tool_card(self, name, args):
        """Add a tool-call status card to the chat. UI thread only.

        Returns a handle dict for _update_tool_card, or None on failure.
        """
        try:
            from System.Windows.Controls import Border, TextBlock, StackPanel, Orientation
            from System.Windows import Thickness, CornerRadius, TextWrapping
            from System.Windows.Media import SolidColorBrush, Color, FontFamily

            card = Border()
            _bind_bg(card, 'SelectedBg')
            _bind_border(card, 'CardBorder')
            card.BorderThickness = Thickness(1)
            card.CornerRadius    = CornerRadius(8)
            card.Padding         = Thickness(12, 8, 12, 8)
            # Left margin lines the card up with bot bubbles (avatar 36 + 10).
            card.Margin          = Thickness(0, 0, 8, 10)

            panel = StackPanel()

            head = StackPanel()
            head.Orientation = Orientation.Horizontal

            status = TextBlock()
            status.Text       = u""   # MDL2 Sync — running
            status.FontFamily = FontFamily(u"Segoe MDL2 Assets")
            status.FontSize   = 12
            _bind_fg(status, 'Blue')
            status.Margin     = Thickness(0, 1, 8, 0)

            title = TextBlock()
            title.Text       = name
            title.FontFamily = FontFamily(u"Consolas")
            title.FontSize   = 12
            title.FontWeight = System.Windows.FontWeights.SemiBold
            _bind_fg(title, 'Ink')

            dur = TextBlock()
            dur.Text       = u"running…"
            dur.FontSize   = 11
            _bind_fg(dur, 'Faint')
            dur.Margin     = Thickness(8, 1, 0, 0)

            head.Children.Add(status)
            head.Children.Add(title)
            head.Children.Add(dur)
            panel.Children.Add(head)

            try:
                args_s = json.dumps(args, ensure_ascii=False)
            except Exception:
                args_s = u"{}".format(args)
            if len(args_s) > 160:
                args_s = args_s[:160] + u"…"
            args_tb = TextBlock()
            args_tb.Text         = args_s
            args_tb.FontSize     = 11
            _bind_fg(args_tb, 'Muted')
            args_tb.TextWrapping = TextWrapping.Wrap
            args_tb.Margin       = Thickness(20, 2, 0, 0)
            panel.Children.Add(args_tb)

            result_tb = TextBlock()
            result_tb.FontSize     = 11
            _bind_fg(result_tb, 'BotText')
            result_tb.TextWrapping = TextWrapping.Wrap
            result_tb.Margin       = Thickness(20, 3, 0, 0)
            result_tb.Visibility   = Visibility.Collapsed
            panel.Children.Add(result_tb)

            card.Child = panel
            group = None
            if not self._chat_verbosity()[0]:
                try:
                    group = self._ensure_tool_group()
                except Exception as ex:
                    logger.debug(u"tool group error: {}".format(_exc_text(ex)))
                    group = None
            if group is not None:
                # Inside the group the outer chrome would double up.
                card.BorderThickness = Thickness(0)
                card.Background      = None
                card.Padding         = Thickness(0, 0, 0, 0)
                card.Margin          = Thickness(0, 0, 0, 8)
                group['body'].Children.Add(card)
                group['total']  += 1
                group['running'] = name
                self._refresh_tool_group_label(group)
            else:
                self.chat_history_panel.Children.Add(card)
            self._scroll_to_bottom()
            return {"card": card, "status": status, "dur": dur,
                    "result": result_tb, "group": group}
        except Exception as ex:
            logger.debug(u"_append_tool_card error: {}".format(_exc_text(ex)))
            return None

    def _update_tool_card(self, handle, ok, seconds, result):
        """Mark a tool card done/failed and show the result summary. UI thread."""
        if not handle:
            return
        try:
            from System.Windows.Media import SolidColorBrush, Color

            status = handle["status"]
            if ok:
                status.Text       = u""   # MDL2 CheckMark
                _bind_fg(status, 'Success')
            else:
                status.Text       = u""   # MDL2 Cancel
                _bind_fg(status, 'Danger')

            handle["dur"].Text = u"{0:.1f}s".format(seconds)

            group = handle.get("group")
            if group is not None:
                group['seconds'] += float(seconds or 0.0)
                group['running']  = u''
                if not ok:
                    group['failed'] += 1
                    # A failure must never stay folded away — the summary row
                    # is a convenience for the happy path only.
                    group['body'].Visibility = Visibility.Visible
                    group['chevron'].Text    = u""
                self._refresh_tool_group_label(group)

            # _json_text, not json.dumps: a Revit result carrying Windows
            # code-page bytes raises in the encoder, and the u"{}".format
            # fallback used to fail on exactly the same bytes.
            res_s = _json_text(result)
            rt = handle["result"]
            # Friendly one-line summary of common result shapes; full JSON
            # stays available on hover so nothing is hidden.
            summary = self._summarize_tool_result(result, res_s)
            rt.Text       = summary[:240] + (u"…" if len(summary) > 240 else u"")
            rt.ToolTip    = res_s[:4000]
            rt.Visibility = Visibility.Visible

            # C1: clickable element-id links → select & zoom in Revit.
            try:
                ids = self._extract_element_ids(result)
                if ok and ids:
                    from System.Windows.Controls import StackPanel, TextBlock, Orientation
                    from System.Windows import Thickness, TextDecorations
                    from System.Windows.Input import Cursors

                    links = StackPanel()
                    links.Orientation = Orientation.Horizontal
                    links.Margin = Thickness(20, 4, 0, 0)

                    def _mk_link(label, id_list):
                        tb = TextBlock()
                        tb.Text           = label
                        tb.FontSize       = 11
                        _bind_fg(tb, 'Blue')
                        tb.TextDecorations = TextDecorations.Underline
                        tb.Cursor         = Cursors.Hand
                        tb.Margin         = Thickness(0, 0, 10, 0)
                        tb.ToolTip        = u"Select & zoom in Revit"

                        def _click(s, e, _ids=list(id_list)):
                            self._select_in_revit_async(_ids)

                        tb.MouseLeftButtonUp += _click
                        return tb

                    for eid in ids[:6]:
                        links.Children.Add(_mk_link(u"#{}".format(eid), [eid]))
                    if len(ids) > 1:
                        links.Children.Add(
                            _mk_link(u"select all {}".format(len(ids)), ids))
                    handle["card"].Child.Children.Add(links)
            except Exception:
                pass

            self._scroll_to_bottom()
        except Exception as ex:
            logger.debug(u"_update_tool_card error: {}".format(_exc_text(ex)))

    @staticmethod
    def _summarize_tool_result(result, fallback):
        """Turn a tool-result dict into a short human line for the card body.

        Reads the tool-computed count/summary fields the agent itself relies
        on (total_count, element_counts, count, message...) so the card reads
        like a status line instead of a raw JSON dump. Full JSON stays in the
        tooltip. Falls back to compact JSON for unrecognized shapes.
        """
        if not isinstance(result, dict):
            return fallback
        if result.get("error"):
            return u"Error: {}".format(result.get("error"))
        parts = []
        # Explicit human message from the tool, if any.
        msg = result.get("message") or result.get("summary")
        if msg:
            parts.append(u"{}".format(msg))
        # The count fields the agent trusts (see agent_loop rule 10).
        for key, label in ((u"total_count", u"matched"),
                            (u"count", u"count"),
                            (u"row_count", u"rows"),
                            (u"modified_count", u"modified"),
                            (u"created_count", u"created"),
                            (u"deleted_count", u"deleted")):
            if isinstance(result.get(key), int):
                parts.append(u"{} {}".format(result[key], label))
        ec = result.get("element_counts")
        if isinstance(ec, dict) and ec:
            top = sorted(ec.items(), key=lambda kv: kv[1], reverse=True)[:4]
            parts.append(u", ".join(u"{}: {}".format(k, v) for k, v in top))
        # A bare success flag with nothing else still deserves a word.
        if not parts and result.get("success") is True:
            parts.append(u"Done")
        if not parts:
            return fallback
        return u"  ·  ".join(parts)

    @staticmethod
    def _extract_element_ids(result, _limit=60):
        """Collect Revit element ids out of a tool-result dict (C1).

        Recognizes the common id-bearing shapes across the ~75 MCP tools:
        {'id': n}, {'element_id': n}, {'element_ids'|'created_ids'|'ids': [...]},
        and nested lists of {'id': n} dicts (get_current_view_elements, ...).
        Order-preserving, deduped, capped so a huge element dump stays cheap.
        """
        out  = []
        seen = set()

        def _add(v):
            try:
                n = int(v)
            except Exception:
                return
            if n > 0 and n not in seen:
                seen.add(n)
                out.append(n)

        def _walk(node, depth):
            if depth > 4 or len(out) >= _limit:
                return
            if isinstance(node, dict):
                for k, v in node.items():
                    lk = u"{}".format(k).lower()
                    if lk in ("id", "element_id", "new_element_id", "new_id",
                              "tag_id", "wall_id", "grid_id", "level_id"):
                        _add(v)
                    elif lk in ("element_ids", "created_ids", "ids", "new_ids",
                                "tag_ids", "wall_ids", "created_element_ids"):
                        if isinstance(v, (list, tuple)):
                            for item in v:
                                _add(item)
                    elif isinstance(v, (dict, list, tuple)):
                        _walk(v, depth + 1)
            elif isinstance(node, (list, tuple)):
                for item in node:
                    _walk(item, depth + 1)

        _walk(result if isinstance(result, dict) else {}, 0)
        return out

    def _select_in_revit_async(self, element_ids):
        """Select + zoom elements from an element-link click (C1).

        Spawns a WORKER thread: _execute_tool blocks on the ExternalEvent,
        and waiting for that on the UI thread would deadlock (the handler
        itself needs the UI thread to run).
        """
        ids = [i for i in (element_ids or [])]
        if not ids:
            return

        def _work():
            res = None
            try:
                from core.server import get_t3labai_server
                srv = get_t3labai_server()
                res = srv._execute_tool('select_elements',
                                        {'element_ids': ids, 'show': True,
                                         'limit': len(ids)})
            except Exception:
                res = None
            # Tell the user WHY a link didn't move the view. Revit's own
            # "No good view could be found." dialog is now avoided upstream,
            # so silence here would otherwise look like a dead link.
            try:
                if not isinstance(res, dict):
                    return
                why = res.get('show_skipped') or res.get('show_error')
                if not why:
                    return   # success — the view visibly changed, stay quiet

                def _ui():
                    try:
                        self._append_bot_message(
                            u"Couldn't jump to element {}: {}".format(
                                u", ".join(str(i) for i in ids[:3]), why),
                            icon=_ICON_WARNING, icon_color=_ICON_AMBER)
                    except Exception:
                        pass
                self.Dispatcher.BeginInvoke(Action(_ui))
            except Exception:
                pass

        t = Thread(ThreadStart(_work))
        t.IsBackground = True
        t.Start()

    def _append_spellcheck_findings(self, findings, viet, total, uniq):
        """Interactive spell-check result TABLE — one row per distinct
        correction (# · Wrong · Correct · Qty · Element IDs · View), matching
        the way Claude-via-MCP presents a scan. Every element-id is a clickable
        link that opens the view containing that element and zooms to it
        (uidoc.ShowElements). UI THREAD.

        Returns True when the card rendered; the caller falls back to the
        plain-text markdown-table report on False so findings are never lost.
        """
        try:
            from System.Windows.Controls import (Border, StackPanel, TextBlock,
                                                  WrapPanel, Grid,
                                                  ColumnDefinition, RowDefinition,
                                                  Orientation)
            from System.Windows import (Thickness, CornerRadius, TextWrapping,
                                        TextDecorations, GridLength, GridUnitType,
                                        HorizontalAlignment, VerticalAlignment)
            from System.Windows.Media import SolidColorBrush, Color
            from System.Windows.Input import Cursors

            try:
                from Services import spell_checker as SC
                rows = SC.aggregate_findings(findings)
            except Exception:
                rows = []
            if not rows:
                return False

            _font = System.Windows.Media.FontFamily("Hanken Grotesk, Inter")

            # Every colour here is a THEME TOKEN, never a hex. This card used
            # to hardcode the Lumina palette (white card, #0F172A ink), so in
            # Revit's dark theme it rendered as a white slab of near-black
            # text inside a dark pane. See docs/assistant-revit-ui.md.
            card = Border()
            _bind_bg(card, 'CardBg')
            _bind_border(card, 'CardBorder')
            card.BorderThickness = Thickness(1)
            card.CornerRadius    = CornerRadius(8)
            card.Padding         = Thickness(12, 9, 12, 10)
            card.Margin          = Thickness(0, 0, 8, 12)

            panel = StackPanel()

            instances = sum(len(r["ids"]) for r in rows)
            # Monochrome MDL2 glyph instead of the 🔍 emoji, so the header
            # matches the icon language of the tool cards and follows the theme.
            head_row = StackPanel()
            head_row.Orientation = Orientation.Horizontal
            head_row.Margin      = Thickness(0, 0, 0, 8)

            hicon = TextBlock()
            hicon.Text       = _ICON_SEARCH
            hicon.FontFamily = System.Windows.Media.FontFamily(u"Segoe MDL2 Assets")
            hicon.FontSize   = 12
            hicon.Margin     = Thickness(0, 1, 7, 0)
            hicon.VerticalAlignment = VerticalAlignment.Center
            _bind_fg(hicon, 'Blue')
            head_row.Children.Add(hicon)

            hdr = TextBlock()
            hdr.Text = (u"Kiểm tra chính tả: {} lỗi ({} vị trí) trong {} nội dung"
                        .format(len(rows), instances, uniq) if viet else
                        u"Spell-check: {} issue(s) across {} instances in {} unique texts"
                        .format(len(rows), instances, uniq))
            hdr.FontSize     = 12.5
            hdr.FontFamily   = _font
            hdr.FontWeight   = System.Windows.FontWeights.SemiBold
            _bind_fg(hdr, 'Ink')
            hdr.TextWrapping = TextWrapping.Wrap
            hdr.VerticalAlignment = VerticalAlignment.Center
            head_row.Children.Add(hdr)
            panel.Children.Add(head_row)

            grid = Grid()
            for w in (GridLength.Auto, GridLength.Auto, GridLength.Auto,
                      GridLength.Auto, GridLength(1.4, GridUnitType.Star),
                      GridLength(1.0, GridUnitType.Star)):
                cd = ColumnDefinition()
                cd.Width = w
                grid.ColumnDefinitions.Add(cd)

            _MAX_ROWS = 60
            shown_rows = rows[:_MAX_ROWS]
            for _ in range(len(shown_rows) + 1):
                grid.RowDefinitions.Add(RowDefinition())

            def _cell(text, col, row, token, size=12, bold=False, wrap=True,
                      align_r=False):
                tb = TextBlock()
                tb.Text         = text
                tb.FontSize     = size
                tb.FontFamily   = _font
                _bind_fg(tb, token)
                tb.TextWrapping = TextWrapping.Wrap if wrap else TextWrapping.NoWrap
                tb.Margin       = Thickness(0, 5, 12, 5)
                tb.VerticalAlignment = VerticalAlignment.Center
                if bold:
                    tb.FontWeight = System.Windows.FontWeights.SemiBold
                if align_r:
                    tb.HorizontalAlignment = HorizontalAlignment.Right
                Grid.SetColumn(tb, col)
                Grid.SetRow(tb, row)
                grid.Children.Add(tb)
                return tb

            # header row
            heads = ([u"#", u"Sai", u"Đúng", u"SL", u"Element IDs", u"View"] if viet
                     else [u"#", u"Wrong", u"Correct", u"Qty", u"Element IDs", u"View"])
            for c, h in enumerate(heads):
                _cell(h, c, 0, 'Faint', size=10.5, bold=True,
                      align_r=(c == 3)).FontWeight = System.Windows.FontWeights.Bold
            hline = Border()
            _bind_border(hline, 'CardBorder')
            hline.BorderThickness = Thickness(0, 0, 0, 1)
            Grid.SetRow(hline, 0)
            Grid.SetColumnSpan(hline, 6)
            grid.Children.Add(hline)

            for i, r in enumerate(shown_rows):
                rr = i + 1
                bg = Border()
                if i % 2 == 1:
                    _bind_bg(bg, 'CodeBg')          # subtle zebra on both themes
                _bind_border(bg, 'Divider')
                bg.BorderThickness = Thickness(0, 0, 0, 1)
                Grid.SetRow(bg, rr)
                Grid.SetColumnSpan(bg, 6)
                grid.Children.Add(bg)

                _cell(u"{}".format(rr), 0, rr, 'Muted', size=11)

                # Wrong / Correct: plain text, no chip. The wrong word used to
                # sit in a bordered red pill while the correction was bare bold
                # text — two visual languages for one before→after pair. The
                # theme's own note on Danger says it is "error TEXT … never a
                # fill", so the pill was against the palette as well as
                # lopsided. Same size, same weight, colour carries the meaning.
                _cell(r["wrong"], 1, rr, 'Danger', size=12, bold=True)

                right = r["right"]
                if r.get("reason") and not right:
                    right = r["reason"]
                elif r.get("reason"):
                    right = u"{} ({})".format(right, r["reason"])
                _cell(right, 2, rr, 'Success', size=12, bold=True)

                _cell(u"{}".format(len(r["ids"])), 3, rr, 'Muted', size=11,
                      align_r=True)

                # element-id links (WrapPanel) — click opens the element's view
                ids = [x for x in r["ids"] if x is not None]
                wrap = WrapPanel()
                wrap.Orientation = Orientation.Horizontal
                wrap.Margin      = Thickness(0, 4, 12, 4)
                Grid.SetColumn(wrap, 4)
                Grid.SetRow(wrap, rr)

                def _mk_link(label, id_list, tip):
                    tb = TextBlock()
                    tb.Text            = label
                    tb.FontSize        = 11
                    tb.FontFamily      = _font
                    _bind_fg(tb, 'Blue')
                    tb.TextDecorations = TextDecorations.Underline
                    tb.Cursor          = Cursors.Hand
                    tb.Margin          = Thickness(0, 1, 10, 1)
                    tb.ToolTip         = tip

                    def _click(s, e, _ids=list(id_list)):
                        self._select_in_revit_async(_ids)

                    tb.MouseLeftButtonUp += _click
                    return tb

                _go_tip = (u"Mở view chứa element & zoom" if viet
                           else u"Open the element's view & zoom")
                for eid in ids[:8]:
                    wrap.Children.Add(_mk_link(u"#{}".format(eid), [eid], _go_tip))
                if len(ids) > 8:
                    wrap.Children.Add(_mk_link(
                        u"+{}".format(len(ids) - 8), ids,
                        (u"Chọn tất cả {}".format(len(ids)) if viet
                         else u"Select all {}".format(len(ids)))))
                elif len(ids) > 1:
                    wrap.Children.Add(_mk_link(
                        u"chọn cả {}".format(len(ids)) if viet
                        else u"all {}".format(len(ids)), ids,
                        (u"Chọn & zoom tất cả" if viet else u"Select & zoom all")))
                grid.Children.Add(wrap)

                views = [v for v in r["views"] if v]
                _cell(u" / ".join(views[:3]) + (u" …" if len(views) > 3 else u"")
                      if views else u"—", 5, rr, 'Muted', size=11)

            panel.Children.Add(grid)

            if len(rows) > _MAX_ROWS:
                more = TextBlock()
                more.Text = (u"… và {} lỗi khác — xem bảng đầy đủ trong lịch sử chat."
                             .format(len(rows) - _MAX_ROWS) if viet else
                             u"… and {} more — full table kept in the chat history."
                             .format(len(rows) - _MAX_ROWS))
                more.FontSize = 11
                more.FontFamily = _font
                _bind_fg(more, 'Muted')
                more.Margin = Thickness(0, 8, 0, 0)
                more.TextWrapping = TextWrapping.Wrap
                panel.Children.Add(more)

            hint = TextBlock()
            hint.Text = (u'Bấm ID để mở view chứa element. Nhắn "sửa 1,2..." '
                         u'hoặc "sửa tất cả" để áp dụng đề xuất.' if viet else
                         u'Click an ID to open the element\'s view. Reply '
                         u'"fix 1,2..." or "fix all" to apply the suggestions.')
            hint.FontSize     = 11
            hint.FontFamily   = _font
            _bind_fg(hint, 'Faint')
            hint.TextWrapping = TextWrapping.Wrap
            hint.Margin       = Thickness(0, 10, 0, 0)
            panel.Children.Add(hint)

            card.Child = panel
            self.chat_history_panel.Children.Add(card)
            self._scroll_to_bottom()
            return True
        except Exception as ex:
            logger.debug(u"_append_spellcheck_findings error: {}".format(_exc_text(ex)))
            return False

    # ─── Destructive-tool confirmation (B5) ────────────────────────────────────

    def _confirm_tool_blocking(self, name, args, viet, timeout_sec=120):
        """WORKER thread: render a Confirm/Cancel card and block until the
        user decides. Returns True only on an explicit Confirm click —
        timeout, Stop, or any error all count as declined.
        """
        import threading
        state = {"decision": None, "seal": None}
        evt = threading.Event()

        def _ui():
            try:
                self._hide_typing_indicator()
                self._append_confirm_card(name, args, state, evt, viet)
            except Exception:
                state["decision"] = False
                evt.set()

        try:
            self.Dispatcher.Invoke(Action(_ui))
        except Exception:
            return False

        waited = 0.0
        while waited < timeout_sec and not evt.is_set():
            evt.wait(0.25)
            waited += 0.25
            loop = self._agent_loop
            if loop is not None and loop.is_cancelled():
                break

        if state["decision"] is None:
            # Timeout / Stop — seal the card so stale buttons can't approve
            # a request that is already over.
            def _expire():
                try:
                    if state.get("seal"):
                        state["seal"](u"⏱ Expired — skipped" if viet
                                      else u"⏱ Expired — skipped")
                except Exception:
                    pass
            try:
                self.Dispatcher.BeginInvoke(Action(_expire))
            except Exception:
                pass
        return state["decision"] is True

    def _append_confirm_card(self, name, args, state, evt, viet):
        """Confirm/Cancel card for a destructive tool call. UI thread only."""
        from System.Windows.Controls import Border, TextBlock, StackPanel, Orientation, Button
        from System.Windows.Documents import Run
        from System.Windows import Thickness, CornerRadius, TextWrapping
        from System.Windows.Media import SolidColorBrush, Color
        from System.Windows.Input import Cursors

        card = Border()
        card.Background      = SolidColorBrush(Color.FromRgb(254, 242, 242))  # #FEF2F2
        card.BorderBrush     = SolidColorBrush(Color.FromRgb(252, 165, 165))  # #FCA5A5
        card.BorderThickness = Thickness(1)
        card.CornerRadius    = CornerRadius(8)
        card.Padding         = Thickness(12, 10, 12, 10)
        card.Margin          = Thickness(0, 0, 8, 10)

        panel = StackPanel()

        head = TextBlock()
        head.FontSize   = 12
        head.FontWeight = System.Windows.FontWeights.SemiBold
        head.Foreground = SolidColorBrush(Color.FromRgb(185, 28, 28))          # #B91C1C
        # Minimal MDL2 warning glyph — needs its own FontFamily run; the plain
        # "⚠" character rendered in the body font would show as a colored
        # emoji glyph (or tofu) instead of a flat monochrome icon.
        self._add_icon_run(head, _ICON_WARNING, (185, 28, 28), size=12)
        head.Inlines.Add(Run(u"Confirm destructive action" if viet
                             else u"Confirm destructive action"))
        panel.Children.Add(head)

        try:
            args_s = json.dumps(args, ensure_ascii=False)
        except Exception:
            args_s = u"{}".format(args)
        if len(args_s) > 200:
            args_s = args_s[:200] + u"…"
        body = TextBlock()
        body.Text         = u"`{}` — {}".format(name, args_s)
        body.FontSize     = 11.5
        body.TextWrapping = TextWrapping.Wrap
        # Fixed ink, not a theme token. This card stays light red in BOTH
        # themes on purpose (a destructive prompt has to shout), so token text
        # inverts with the host and disappears on it: BotText is #E4E4E4 in
        # Revit's dark theme — near-white on #FEF2F2.
        body.Foreground   = SolidColorBrush(Color.FromRgb(63, 63, 63))   # #3F3F3F
        body.Margin       = Thickness(0, 4, 0, 8)
        panel.Children.Add(body)

        btn_row = StackPanel()
        btn_row.Orientation = Orientation.Horizontal

        status_tb = TextBlock()
        status_tb.FontSize   = 11.5
        # Same reason as `body` above — fixed card, fixed ink.
        status_tb.Foreground = SolidColorBrush(Color.FromRgb(140, 106, 106))  # #8C6A6A
        status_tb.Margin     = Thickness(10, 5, 0, 0)
        status_tb.Visibility = Visibility.Collapsed

        def _mk_btn(label, bg, fg):
            b = Button()
            b.Content         = label
            b.FontSize        = 12
            b.FontWeight      = System.Windows.FontWeights.SemiBold
            b.Padding         = Thickness(14, 5, 14, 5)
            b.Margin          = Thickness(0, 0, 8, 0)
            b.Cursor          = Cursors.Hand
            b.Background      = SolidColorBrush(bg)
            b.Foreground      = SolidColorBrush(fg)
            b.BorderThickness = Thickness(0)
            return b

        # Fixed colours, like the card itself — the two buttons live on a
        # surface that does not follow the host, so theme tokens read wrong on
        # it in dark mode (CardBg is a mid grey there: grey label on a red
        # button, 2.1:1; Ink is white: white label on a light grey button).
        ok_btn = _mk_btn(u"Confirm" if viet else u"Confirm",
                         Color.FromRgb(196, 43, 28),      # #C42B1C
                         Color.FromRgb(255, 255, 255))
        no_btn = _mk_btn(u"Cancel" if viet else u"Cancel",
                         Color.FromRgb(230, 230, 230),    # #E6E6E6
                         Color.FromRgb(63, 63, 63))       # #3F3F3F

        def _seal(msg):
            try:
                ok_btn.IsEnabled     = False
                no_btn.IsEnabled     = False
                status_tb.Text       = msg
                status_tb.Visibility = Visibility.Visible
            except Exception:
                pass
        state["seal"] = _seal

        def _on_ok(s, e):
            state["decision"] = True
            _seal(u"✓ Confirmed" if viet else u"✓ Confirmed")
            evt.set()

        def _on_cancel(s, e):
            state["decision"] = False
            _seal(u"✗ Cancelled" if viet else u"✗ Cancelled")
            evt.set()

        ok_btn.Click += _on_ok
        no_btn.Click += _on_cancel

        btn_row.Children.Add(ok_btn)
        btn_row.Children.Add(no_btn)
        btn_row.Children.Add(status_tb)
        panel.Children.Add(btn_row)

        card.Child = panel
        self.chat_history_panel.Children.Add(card)
        self._scroll_to_bottom()

    # ─── Vision view capture (C2) ──────────────────────────────────────────────

    def _wants_view_snapshot(self, text, provider):
        """True when the user asks the assistant to LOOK at the current view
        and the active provider can take Claude-format image blocks (the
        agent path currently ships vision only for Claude)."""
        if not text:
            return False
        if provider is None or getattr(provider, "NAME", "") != "claude":
            return False
        try:
            if not provider.supports_vision():
                return False
        except Exception:
            return False
        import re as _re
        pat = (u"(nhìn|xem|quan sát|soi|chụp|đánh giá|kiểm tra"
               u"|look|inspect|review|check|analy)"
               u"[^\n]{0,24}"
               u"(view|màn hình|bố cục|layout|screen)")
        return _re.search(pat, text, _re.IGNORECASE | _re.UNICODE) is not None

    def _capture_active_view(self, srv):
        """WORKER thread: export the active view as a PNG (~1280px) through
        the ExternalEvent write path. Returns the PNG path or None."""
        try:
            import tempfile
            folder = os.path.join(tempfile.gettempdir(), 'T3Lab_ViewShots')
            res = srv._execute_tool('export_image',
                                    {'width': 1280, 'output_folder': folder})
            files = (res or {}).get('files') or []
            for p in files:
                if p.lower().endswith('.png') and os.path.isfile(p):
                    return p
            return files[0] if files else None
        except Exception:
            return None

    # ─── Chat UI helpers ──────────────────────────────────────────────────────

    _avatar_bitmap = None   # lazy-loaded, cached BitmapImage of icon.png (per window)
    _avatar_bitmap_failed = False

    def _get_avatar_bitmap(self):
        """Return the cached BitmapImage for the tool's own icon.png, or None.

        icon.png is the SAME icon Revit shows on the T3Lab Assistant ribbon
        button (T3LabAssistant.pushbutton/icon.png) — using it as the chat
        avatar means the bubble literally shows the tool's real icon instead
        of a generic colored "T3" initials badge.
        """
        if self._avatar_bitmap is not None:
            return self._avatar_bitmap
        if self._avatar_bitmap_failed:
            return None
        try:
            from System import Uri, UriKind
            from System.Windows.Media.Imaging import BitmapCacheOption
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')
            bmp = BitmapImage()
            bmp.BeginInit()
            bmp.UriSource   = Uri(icon_path, UriKind.Absolute)
            bmp.CacheOption = BitmapCacheOption.OnLoad
            bmp.EndInit()
            bmp.Freeze()
            self._avatar_bitmap = bmp
            return bmp
        except Exception as ex:
            logger.debug(u"_get_avatar_bitmap error: {}".format(_exc_text(ex)))
            self._avatar_bitmap_failed = True
            return None

    def _stop_avatar_spins(self):
        """Freeze every live avatar spin (UI thread only). Rotation is the
        'thinking' indicator: bubbles born during a busy turn spin, and the
        moment the turn ends they all stop and rest upright."""
        spins = getattr(self, '_live_avatar_spins', None) or []
        if not spins:
            return
        try:
            from System.Windows.Media import RotateTransform
            for spin in spins:
                try:
                    spin.BeginAnimation(RotateTransform.AngleProperty, None)
                except Exception:
                    pass
        except Exception:
            pass
        self._live_avatar_spins = []

    def _make_avatar(self, letter, size=36):
        """Create a circular avatar showing the tool's real icon.png.

        Falls back to a plain initials badge (BatchOut blue #3498DB) only if
        the icon file can't be loaded — the app must never crash a chat row
        over a missing/locked icon asset.
        """
        from System.Windows.Controls import Border, TextBlock
        from System.Windows import Thickness, CornerRadius
        from System.Windows.Media import SolidColorBrush, Color, ImageBrush, Stretch
        from System.Windows import HorizontalAlignment, VerticalAlignment

        av = Border()
        av.Width = size
        av.Height = size
        av.CornerRadius = CornerRadius(size / 2.0)
        av.Margin = Thickness(0, 2, 10, 0)
        av.VerticalAlignment = VerticalAlignment.Top

        # Spin the avatar ONLY while the assistant is thinking (user request
        # 2026-07-22, replacing the earlier permanent rotation). Since replies
        # went bubble-less the only avatar left is the one in the typing row,
        # which exists exactly for the duration of a busy turn — so a moving
        # mark still means "working right now", and _set_busy(False) freezing
        # live spins remains the belt-and-braces stop. 30fps cap keeps the
        # animation cheap.
        if getattr(self, '_busy', False):
            try:
                from System.Windows.Media import RotateTransform
                from System.Windows.Media.Animation import (DoubleAnimation,
                                                            RepeatBehavior,
                                                            Timeline)
                from System.Windows import Point, Duration
                from System import TimeSpan, Nullable, Int32
                av.RenderTransformOrigin = Point(0.5, 0.5)
                spin = RotateTransform()
                av.RenderTransform = spin
                anim = DoubleAnimation(
                    0.0, 360.0, Duration(TimeSpan.FromSeconds(1.6)))
                anim.RepeatBehavior = RepeatBehavior.Forever
                Timeline.SetDesiredFrameRate(anim, Nullable[Int32](30))
                spin.BeginAnimation(RotateTransform.AngleProperty, anim)
                if not hasattr(self, '_live_avatar_spins'):
                    self._live_avatar_spins = []
                self._live_avatar_spins.append(spin)
            except Exception:
                pass

        bmp = self._get_avatar_bitmap()
        if bmp is not None:
            brush = ImageBrush(bmp)
            brush.Stretch = Stretch.UniformToFill
            av.Background = brush
            return av

        av.Background = SolidColorBrush(Color.FromRgb(52, 152, 219))   # #3498DB fallback
        lbl = TextBlock()
        lbl.Text = letter
        lbl.FontSize = 12
        lbl.FontWeight = System.Windows.FontWeights.Bold
        lbl.Foreground = SolidColorBrush(Color.FromRgb(255, 255, 255))
        lbl.HorizontalAlignment = HorizontalAlignment.Center
        lbl.VerticalAlignment = VerticalAlignment.Center
        av.Child = lbl
        return av

    @staticmethod
    def _add_icon_run(text_block, glyph, rgb=None, size=13.5):
        """Prepend a Segoe MDL2 Assets icon Run to a TextBlock, followed by a
        thin space — the ONLY way to render a real icon glyph inline: the rest
        of the bubble uses Hanken Grotesk/Inter, which has no glyph at these
        private-use codepoints, so the icon needs its own FontFamily run
        rather than living in the same string as the body text.
        """
        from System.Windows.Documents import Run
        from System.Windows.Media import FontFamily as _WpfFontFamily, SolidColorBrush, Color

        icon_run = Run(glyph)
        icon_run.FontFamily = _WpfFontFamily(u"Segoe MDL2 Assets")
        icon_run.FontSize   = size
        if rgb:
            icon_run.Foreground = SolidColorBrush(Color.FromRgb(*rgb))
        text_block.Inlines.Add(icon_run)
        text_block.Inlines.Add(Run(u"  "))

    # ─── Claude-style message actions ────────────────────────────────────────
    # Line-art glyphs for the icon row that sits under every assistant reply.
    # Same geometry language as the composer icons (1.5px round-capped strokes
    # on a 24x24 viewbox), so the whole surface reads as one icon set.

    _ACT_COPY  = (u"M8 8h11a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2Z "
                  u"M4 16a2 2 0 0 1-2-2V3a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2")
    _ACT_CHECK = u"M20 6 9 17l-5-5"
    _ACT_RETRY = u"M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8 M3 3v5h5"
    _ACT_UP    = (u"M7 10v12 M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4"
                  u"a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z")
    _ACT_DOWN  = (u"M17 14V2 M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20"
                  u"a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z")
    _ACT_PREV  = u"M15 18l-6-6 6-6"
    _ACT_NEXT  = u"M9 18l6-6-6-6"

    def _make_action_icon(self, data, tooltip, handler, token='IconFg'):
        """One muted icon button for the message action row."""
        from System.Windows.Controls import Button
        from System.Windows.Shapes import Path
        from System.Windows.Media import Geometry, Stretch, PenLineCap, PenLineJoin

        p = Path()
        p.Data             = Geometry.Parse(data)
        _bind_stroke(p, token)
        p.StrokeThickness  = 1.5
        p.StrokeStartLineCap = PenLineCap.Round
        p.StrokeEndLineCap   = PenLineCap.Round
        p.StrokeLineJoin     = PenLineJoin.Round
        p.Width  = 13.5
        p.Height = 13.5
        p.Stretch = Stretch.Uniform

        btn = Button()
        try:
            btn.Style = self.Resources["MsgActionBtn"]
        except Exception:
            pass
        btn.Content = p
        btn.ToolTip = tooltip
        if handler is not None:
            btn.Click += handler
        return btn

    def _make_message_actions(self, row):
        """The action/feedback bar under assistant replies — disabled per user request."""
        return None

    @staticmethod
    def _owning_reply_row(sender):
        """Walk up from a clicked action icon to the reply row that owns it."""
        from System.Windows.Media import VisualTreeHelper
        node = sender
        for _ in range(12):
            try:
                node = VisualTreeHelper.GetParent(node)
            except Exception:
                return None
            if node is None:
                return None
            tag = getattr(node, 'Tag', None)
            if isinstance(tag, dict) and 'versions' in tag:
                return node
        return None

    def _refresh_reply_row(self, row):
        """Re-render a reply row's body + action bar from its Tag state."""
        from System.Windows.Controls import TextBlock
        from System.Windows import TextWrapping

        state = row.Tag or {}
        versions = state.get('versions') or [u""]
        idx = max(0, min(state.get('index', 0), len(versions) - 1))
        text = versions[idx]

        host = state.get('host')
        if host is None:
            return
        host.Children.Clear()
        try:
            body = self._render_md_blocks(text, icon=state.get('icon'),
                                          icon_color=state.get('icon_color'))
        except Exception:
            body = TextBlock()
            body.FontSize     = 13.5
            body.FontFamily   = System.Windows.Media.FontFamily("Hanken Grotesk, Inter")
            _bind_fg(body, 'BotText')
            body.TextWrapping = TextWrapping.Wrap
            body.LineHeight   = 21
            body.Text         = text
        host.Children.Add(body)
        acts = self._make_message_actions(row)
        if acts is not None:
            host.Children.Add(acts)

    # ─── Action handlers ─────────────────────────────────────────────────────

    def _msg_copy_clicked(self, sender, e):
        """Copy the reply to the clipboard, with the icon flashing a check."""
        row = self._owning_reply_row(sender)
        if row is None:
            return
        state = row.Tag or {}
        versions = state.get('versions') or [u""]
        text = versions[max(0, min(state.get('index', 0), len(versions) - 1))]
        try:
            from System.Windows import Clipboard
            try:
                Clipboard.SetText(text)
            except Exception:
                # Another process can hold the clipboard open; SetDataObject
                # with copy=True retries through the shell instead of throwing.
                Clipboard.SetDataObject(text, True)
        except Exception as ex:
            logger.debug(u"copy to clipboard failed: {}".format(_exc_text(ex)))
            return

        try:
            from System.Windows.Media import Geometry
            from System.Windows.Threading import DispatcherTimer
            from System import TimeSpan
            glyph = sender.Content
            glyph.Data = Geometry.Parse(self._ACT_CHECK)
            _bind_stroke(glyph, 'Success')
            sender.ToolTip = u"Copied"

            timer = DispatcherTimer()
            timer.Interval = TimeSpan.FromMilliseconds(1200)

            def _restore(s, ev):
                timer.Stop()
                try:
                    glyph.Data = Geometry.Parse(self._ACT_COPY)
                    _bind_stroke(glyph, 'IconFg')
                    sender.ToolTip = u"Copy"
                except Exception:
                    pass
            timer.Tick += _restore
            timer.Start()
        except Exception:
            pass

    def _msg_retry_clicked(self, sender, e):
        """Regenerate this reply from the prompt that produced it.

        The reply is NOT replaced: the new answer is filed as another version
        of the same row, so the arrows let the user compare instead of losing
        the first answer. The user's own message is not echoed a second time.
        """
        if self._busy:
            return
        row = self._owning_reply_row(sender)
        if row is None:
            return
        prompt = (row.Tag or {}).get('prompt') or u""
        if not prompt.strip():
            self._append_bot_message(
                u"Nothing to try again — the original request for this reply "
                u"is no longer in this session.",
                icon=_ICON_INFO, icon_color=_ICON_SLATE)
            return
        self._retry_target_row  = row
        self._suppress_user_echo = True
        try:
            self.chat_input.Text = prompt
            self._process_input()
        except Exception as ex:
            self._retry_target_row   = None
            self._suppress_user_echo = False
            logger.debug(u"retry failed: {}".format(_exc_text(ex)))

    def _msg_vote_up_clicked(self, sender, e):
        self._record_vote(sender, 'up')

    def _msg_vote_down_clicked(self, sender, e):
        self._record_vote(sender, 'down')

    def _record_vote(self, sender, vote):
        """Latch a thumbs up/down on a reply, log it, and LEARN from it.

        Clicking the latched side again clears the vote, so a mis-click is
        undoable rather than permanent.

        The vote used to end at the activity log. It now also feeds the route
        that produced the reply (captured in row.Tag['decision'] when the row
        was built): 👍 reinforces the phrasing→intent mapping through the same
        learn_pattern() the assistant already uses, 👎 suppresses that exact
        pair so it can never win the turn again. Both are deliberately scoped
        to the phrasing that was voted on — one bad answer is evidence about
        one route, and a feedback loop that over-generalizes is worse than
        none.
        """
        row = self._owning_reply_row(sender)
        if row is None:
            return
        state = row.Tag or {}
        state['vote'] = None if state.get('vote') == vote else vote
        row.Tag = state
        self._refresh_reply_row(row)
        if not state['vote']:
            return          # vote cleared — nothing to log or learn from

        versions = state.get('versions') or [u""]
        text = versions[max(0, min(state.get('index', 0), len(versions) - 1))]
        preview = u" ".join((text or u"").split())[:200]
        self._log_activity(u"Feedback: {} — “{}”".format(
            u"good response" if vote == 'up' else u"bad response", preview))
        self._apply_vote(vote, state.get('decision') or {})

    def _vote_decision(self):
        """Snapshot of how the current turn was routed, for the vote loop.

        Taken when the reply row is built so a vote cast much later still
        refers to the turn it belongs to, not to whatever is running now.
        """
        dec = getattr(self, '_agent_decision', None) or {}
        last = getattr(self, '_last_result', None) or {}
        return {
            'raw':        getattr(self, '_last_raw', u"") or u"",
            'intent':     last.get('intent'),
            'params':     last.get('params') or {},
            'message':    last.get('message') or u"",
            'skill':      dec.get('skill'),
            'specialist': dec.get('specialist'),
        }

    def _apply_vote(self, vote, decision):
        """Persist one vote against the route that produced the reply.

        Never raises: feedback is a nicety, and a write failure must not take
        down the chat.
        """
        raw = (decision.get('raw') or u'').strip()
        if not raw:
            return
        try:
            from Intelligence import feedback
            feedback.record_vote(vote, raw,
                                 intent=decision.get('intent'),
                                 skill=decision.get('skill'),
                                 specialist=decision.get('specialist'))
        except Exception as ex:
            logger.debug(u"feedback store error: {}".format(_exc_text(ex)))

        if vote != 'up':
            return
        # An up-voted turn is a confirmed phrasing→intent mapping. learn_pattern
        # already refuses small talk and non-tool intents, so this cannot
        # record "thanks" as a command.
        intent = decision.get('intent')
        if not intent:
            return
        try:
            learn_pattern(raw, intent, decision.get('params') or {},
                          decision.get('message') or u'')
        except Exception as ex:
            logger.debug(u"learn_pattern from vote failed: {}".format(_exc_text(ex)))

    def _msg_prev_clicked(self, sender, e):
        self._step_version(sender, -1)

    def _msg_next_clicked(self, sender, e):
        self._step_version(sender, +1)

    def _step_version(self, sender, delta):
        row = self._owning_reply_row(sender)
        if row is None:
            return
        state = row.Tag or {}
        versions = state.get('versions') or []
        idx = state.get('index', 0) + delta
        if idx < 0 or idx >= len(versions):
            return
        state['index'] = idx
        row.Tag = state
        self._refresh_reply_row(row)

    def _adopt_retry_version(self, text, icon=None, icon_color=None):
        """File `text` as another version of the row Try again was pressed on.

        Returns True when the text was adopted (so the caller must NOT also
        append a new row). Any failure returns False and the reply lands as a
        normal new message — a broken regenerate must never swallow an answer.
        """
        row = getattr(self, '_retry_target_row', None)
        if row is None:
            return False
        self._retry_target_row = None
        try:
            state = row.Tag or {}
            versions = state.get('versions')
            if not isinstance(versions, list):
                return False
            versions.append(text)
            state['versions']   = versions
            state['index']      = len(versions) - 1
            state['icon']       = icon
            state['icon_color'] = icon_color
            state['vote']       = None
            row.Tag = state
            self._refresh_reply_row(row)
            self._scroll_to_bottom()
            return True
        except Exception as ex:
            logger.debug(u"_adopt_retry_version failed: {}".format(_exc_text(ex)))
            return False

    def _append_user_message(self, text, attachment_note=None):
        """Add the user's message as a right-aligned bubble.

        No fill, no border, no shadow: the user's turn is right-aligned text on
        the same surface as everything else. (It was Revit's selected-row blue
        tint, then briefly a bordered + shadowed card — both read as a second
        surface inside a pane this narrow.) Alignment alone marks the speaker.

        attachment_note, if given, renders as its own line with a minimal
        Attach glyph — replaces the old baked-in "📎 filename" text so the
        indicator is a real icon, not a colored emoji character.
        """
        try:
            from System.Windows.Controls import Border, TextBlock, Grid, ColumnDefinition
            from System.Windows.Documents import Run, LineBreak
            from System.Windows import Thickness, CornerRadius, TextWrapping, GridLength, HorizontalAlignment

            row = Grid()
            row.Margin = Thickness(40, 4, 0, 14)
            col0 = ColumnDefinition()
            col0.Width = GridLength(1, System.Windows.GridUnitType.Star)
            row.ColumnDefinitions.Add(col0)

            bubble = Border()
            _bind_bg(bubble, 'ChatBg')
            bubble.Padding      = Thickness(2, 0, 2, 0)
            bubble.HorizontalAlignment = HorizontalAlignment.Right

            msg_text = TextBlock()
            msg_text.FontSize    = 13.5
            msg_text.FontFamily  = System.Windows.Media.FontFamily("Hanken Grotesk, Inter")
            _bind_fg(msg_text, 'UserBubbleText')
            msg_text.LineHeight  = 21
            msg_text.TextWrapping = TextWrapping.Wrap
            if text:
                msg_text.Inlines.Add(Run(text))
            if attachment_note:
                if text:
                    msg_text.Inlines.Add(LineBreak())
                self._add_icon_run(msg_text, _ICON_ATTACH, size=12)
                msg_text.Inlines.Add(Run(attachment_note))
            bubble.Child = msg_text

            Grid.SetColumn(bubble, 0)
            row.Children.Add(bubble)
            self.chat_history_panel.Children.Add(row)
            self._scroll_to_bottom()
        except Exception as ex:
            logger.debug(u"Error adding user message: {}".format(_exc_text(ex)))

    @staticmethod
    def _clean_bot_response(text):
        """Strip chain-of-thought, meta-commentary, and excessive whitespace."""
        import re as _re
        # Remove <think>...</think> blocks (reasoning models)
        text = _re.sub(r'<think>[\s\S]*?</think>', '', text, flags=_re.IGNORECASE)
        # Remove lines that read like internal planning/meta-commentary
        _skip_prefixes = (
            u"* user", u"* role", u"* tone", u"* language", u"* option",
            u"* the user", u"* since i am", u"* as an ai",
            u"- user", u"- role", u"- tone", u"- option",
            u"i am an ai", u"as an ai assistant",
            u"let me analyze", u"let me think", u"i need to consider",
            u"i'll analyze", u"i'll consider",
        )
        lines_out = []
        for line in text.splitlines():
            low = line.strip().lower()
            if any(low.startswith(p) for p in _skip_prefixes):
                continue
            lines_out.append(line)
        # Collapse 3+ consecutive blank lines to 1
        text = _re.sub(r'\n{3,}', u'\n\n', u'\n'.join(lines_out))
        return text.strip()

    # Legacy colored emoji markers (still reachable through chat history saved
    # before the fast-context path was removed) mapped to a minimal MDL2 glyph
    # + Lumina
    # color — converted at render time so no caller needs to change its
    # markdown text, only this one renderer.
    _MD_ICON_MARKERS = [
        (u"⚡ ",            "_ICON_ANALYZE", "_ICON_BLUE"),   # instant DB answer
        (u"\U0001f4cb ",   "_ICON_LIST",    "_ICON_SLATE"),  # stats/info section
        (u"\U0001f5bc️ ", "_ICON_SEARCH", "_ICON_SLATE"),  # view section
        (u"\U0001f3af ",   "_ICON_LIST",    "_ICON_SLATE"),  # selection section
        (u"\U0001f4ca ",   "_ICON_LIST",    "_ICON_BLUE"),   # LLM chart/stats emoji
        (u"\U0001f4c8 ",   "_ICON_LIST",    "_ICON_BLUE"),   # LLM trend emoji
        # nlu_engine's capability answers and the assistant's own status lines
        # were still emitting full-colour emoji, against the house rule stated
        # at _ICON_INFO above. Converting them HERE rather than editing ~30
        # string literals keeps one renderer authoritative, and any emoji a
        # model invents in its reply gets the same treatment for free.
        # A variation-selector form (U+FE0F) must precede its bare twin —
        # startswith would match the bare marker and strand the selector.
        (u"✅ ",       "_ICON_SUCCESS", "_ICON_GREEN"),  # ✅ yes / found
        (u"✔ ",       "_ICON_SUCCESS", "_ICON_GREEN"),  # ✔ completed
        (u"❌ ",       "_ICON_STOP",    "_ICON_RED"),    # ❌ none / no tool
        (u"⚠️ ", "_ICON_WARNING", "_ICON_AMBER"),  # ⚠️ warning
        (u"⚠ ",       "_ICON_WARNING", "_ICON_AMBER"),  # ⚠ warning
        (u"\U0001f50d ",   "_ICON_SEARCH",  "_ICON_BLUE"),   # 🔍 QA / lookup
        (u"\U0001f4c2 ",   "_ICON_LIST",    "_ICON_SLATE"),  # 📂 ribbon tools
        (u"\U0001f9e9 ",   "_ICON_LIST",    "_ICON_SLATE"),  # 🧩 other tools
        (u"\U0001f4d6 ",   "_ICON_LIST",    "_ICON_SLATE"),  # 📖 read / query
        (u"\U0001f4c1 ",   "_ICON_LIST",    "_ICON_SLATE"),  # 📁 view / sheet
        (u"\U0001f4e4 ",   "_ICON_LIST",    "_ICON_SLATE"),  # 📤 export
        (u"✏️ ", "_ICON_LIST",    "_ICON_SLATE"),  # ✏️ create / modify
        (u"✏ ",       "_ICON_LIST",    "_ICON_SLATE"),  # ✏ create / modify
    ]

    @staticmethod
    def _add_inline_md(text_block, line):
        """Render ONE line's inline markdown into text_block:
        **bold** and `code` spans (code = Consolas, ink color)."""
        import re as _re
        from System.Windows.Documents import Run
        from System.Windows import FontWeights
        from System.Windows.Media import FontFamily as _WpfFontFamily, SolidColorBrush, Color

        for b_idx, b_seg in enumerate(_re.split(r'\*\*', line)):
            if not b_seg:
                continue
            bold = (b_idx % 2 == 1)
            for c_idx, c_seg in enumerate(b_seg.split(u'`')):
                if not c_seg:
                    continue
                r = Run()
                r.Text = c_seg
                if bold:
                    r.FontWeight = FontWeights.SemiBold
                if c_idx % 2 == 1:              # inside `code`
                    r.FontFamily = _WpfFontFamily(u"Consolas")
                    _bind_fg(r, 'CodeFg')
                text_block.Inlines.Add(r)

    @staticmethod
    def _build_md_inlines(text_block, text):
        """
        Populate text_block.Inlines with paragraph-level markdown:
        leading emoji→MDL2 icon markers, # headings, bullet lines (* / -),
        **bold** / `code` inline spans, line breaks. Tables are handled one
        level up by _render_md_blocks (a TextBlock cannot host a grid).
        """
        from System.Windows.Documents import Run, LineBreak
        from System.Windows import FontWeights

        module_globals = globals()
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if i > 0:
                text_block.Inlines.Add(LineBreak())

            # Legacy emoji section markers → minimal MDL2 icon run
            stripped = line.strip()
            for marker, glyph_name, color_name in T3LabAssistantWindow._MD_ICON_MARKERS:
                if stripped.startswith(marker):
                    T3LabAssistantWindow._add_icon_run(
                        text_block, module_globals[glyph_name], module_globals[color_name])
                    line = stripped[len(marker):]
                    break

            # Headings: "# " / "## " / "### " → semibold, slightly larger
            stripped = line.strip()
            if stripped.startswith(u'#'):
                h = len(stripped) - len(stripped.lstrip(u'#'))
                if 1 <= h <= 4 and stripped[h:h + 1] == u' ':
                    r = Run()
                    r.Text       = stripped[h + 1:].strip()
                    r.FontWeight = FontWeights.SemiBold
                    r.FontSize   = 14.5 if h <= 2 else 13.5
                    text_block.Inlines.Add(r)
                    continue

            # Blockquote: "> text" → muted bar + text
            if stripped.startswith(u'> '):
                from System.Windows.Media import SolidColorBrush, Color
                bar = Run()
                bar.Text       = u'▏ '   # ▏ left one-eighth block
                _bind_fg(bar, 'Faint')
                text_block.Inlines.Add(bar)
                quote = Run()
                quote.Text       = stripped[2:]
                _bind_fg(quote, 'Muted')
                quote.FontStyle  = System.Windows.FontStyles.Italic
                text_block.Inlines.Add(quote)
                continue

            # Ordered list: "1. text", "2) text" → keep the number, indent-align
            import re as _re_md
            _om = _re_md.match(r'^(\d{1,3})[.)]\s+(.*)$', stripped)
            if _om:
                num_run = Run()
                num_run.Text       = u'{}. '.format(_om.group(1))
                num_run.FontWeight = FontWeights.SemiBold
                text_block.Inlines.Add(num_run)
                T3LabAssistantWindow._add_inline_md(text_block, _om.group(2))
                continue

            # Bullet lines (also nested "  - " / "  * ")
            if stripped.startswith(u'* ') or stripped.startswith(u'- '):
                indent = len(line) - len(line.lstrip())
                prefix_run = Run()
                prefix_run.Text = (u'    ' if indent >= 2 else u'') + u'• '
                text_block.Inlines.Add(prefix_run)
                line = stripped[2:]             # remaining text after bullet marker

            T3LabAssistantWindow._add_inline_md(text_block, line)

    def _make_md_table(self, raw_rows):
        """Render markdown pipe-rows ("| a | b |") as a bordered WPF Grid.

        Returns a Border-wrapped Grid, or None when the rows don't form a
        usable table (caller then falls back to showing the raw text).
        Header row (followed by |---|---|) gets the Lumina table header
        treatment; star-sized columns so the table always fits the bubble.
        """
        try:
            import re as _re
            from System.Windows.Controls import Grid, ColumnDefinition, RowDefinition, Border, TextBlock
            from System.Windows import Thickness, CornerRadius, TextWrapping, GridLength, GridUnitType
            from System.Windows.Media import SolidColorBrush, Color

            parsed = []
            for r in raw_rows:
                inner = r.strip()
                if inner.startswith(u"|"):
                    inner = inner[1:]
                if inner.endswith(u"|"):
                    inner = inner[:-1]
                parsed.append([c.strip() for c in inner.split(u"|")])

            def _is_sep(cells):
                return bool(cells) and all(
                    _re.match(r'^:?-{2,}:?$', c or u'') for c in cells)

            header = None
            if len(parsed) >= 2 and _is_sep(parsed[1]):
                header = parsed[0]
                body = [r for r in parsed[2:] if not _is_sep(r)]
            else:
                body = [r for r in parsed if not _is_sep(r)]
            rows = ([header] if header is not None else []) + body
            if not rows:
                return None
            ncols = max(len(r) for r in rows)
            if ncols < 2:
                return None

            _line  = _theme_color('Divider')
            _inkhd = _theme_color('Ink')
            _ink   = _theme_color('BotText')

            g = Grid()
            for _c in range(ncols):
                cd = ColumnDefinition()
                cd.Width = GridLength(1, GridUnitType.Star)
                g.ColumnDefinitions.Add(cd)

            for ri, row in enumerate(rows):
                g.RowDefinitions.Add(RowDefinition())
                is_head = (header is not None and ri == 0)
                for ci in range(ncols):
                    cell = Border()
                    cell.BorderBrush = SolidColorBrush(_line)
                    cell.BorderThickness = Thickness(
                        0, 0,
                        1 if ci < ncols - 1 else 0,
                        1 if ri < len(rows) - 1 else 0)
                    cell.Padding = Thickness(8, 4, 8, 4)
                    if is_head:
                        _bind_bg(cell, 'SelectedBg')

                    tb = TextBlock()
                    tb.FontSize     = 12 if is_head else 12.5
                    tb.FontFamily   = System.Windows.Media.FontFamily("Hanken Grotesk, Inter")
                    tb.TextWrapping = TextWrapping.Wrap
                    tb.Foreground   = SolidColorBrush(_inkhd if is_head else _ink)
                    if is_head:
                        tb.FontWeight = System.Windows.FontWeights.SemiBold
                    self._add_inline_md(tb, row[ci] if ci < len(row) else u"")
                    cell.Child = tb
                    Grid.SetRow(cell, ri)
                    Grid.SetColumn(cell, ci)
                    g.Children.Add(cell)

            outer = Border()
            outer.BorderBrush     = SolidColorBrush(_line)
            outer.BorderThickness = Thickness(1)
            outer.CornerRadius    = CornerRadius(4)
            outer.Margin          = Thickness(0, 6, 0, 6)
            outer.Child = g
            return outer
        except Exception as ex:
            logger.debug(u"_make_md_table error: {}".format(_exc_text(ex)))
            return None

    def _make_code_block(self, code_lines):
        """Render a fenced ``` code block as a monospace card with a subtle
        surface bg and horizontal scroll so code formatting is preserved."""
        from System.Windows.Controls import Border, TextBlock, ScrollViewer, ScrollBarVisibility
        from System.Windows import Thickness, CornerRadius
        from System.Windows.Media import SolidColorBrush, Color, FontFamily

        tb = TextBlock()
        tb.Text       = u"\n".join(code_lines).rstrip(u"\n")
        tb.FontFamily = FontFamily(u"Consolas")
        tb.FontSize   = 12
        _bind_fg(tb, 'Ink')

        sv = ScrollViewer()
        sv.HorizontalScrollBarVisibility = ScrollBarVisibility.Auto
        sv.VerticalScrollBarVisibility   = ScrollBarVisibility.Disabled
        sv.Content = tb

        card = Border()
        _bind_bg(card, 'CodeBg')
        _bind_border(card, 'CardBorder')
        card.BorderThickness = Thickness(1)
        card.CornerRadius    = CornerRadius(4)
        card.Padding         = Thickness(10, 8, 10, 8)
        card.Margin          = Thickness(0, 4, 0, 4)
        card.Child = sv
        return card

    def _make_hr(self):
        """A thin horizontal divider for markdown '---' / '***' rules."""
        from System.Windows.Controls import Border
        from System.Windows import Thickness
        from System.Windows.Media import SolidColorBrush, Color
        hr = Border()
        hr.Height     = 1
        _bind_bg(hr, 'Divider')
        hr.Margin      = Thickness(0, 8, 0, 8)
        return hr

    def _render_md_blocks(self, text, icon=None, icon_color=None):
        """Build the CONTENT of a bot bubble: a StackPanel of paragraph
        TextBlocks, real table Grids, fenced code cards, and rules.

        The old single-TextBlock renderer showed markdown tables as raw
        "| a | b |" pipe text; consecutive pipe-lines now become a bordered
        grid with a header row, so LLM answers containing tables read
        cleanly. icon/icon_color prefix the first paragraph.
        """
        from System.Windows.Controls import StackPanel, TextBlock
        from System.Windows import TextWrapping, Thickness
        from System.Windows.Media import SolidColorBrush, Color

        panel = StackPanel()
        state = {"icon": icon}

        def _new_tb():
            tb = TextBlock()
            tb.FontSize     = 13
            tb.FontFamily   = System.Windows.Media.FontFamily("Hanken Grotesk, Inter")
            _bind_fg(tb, 'BotText')
            tb.TextWrapping = TextWrapping.Wrap
            tb.LineHeight   = 20
            return tb

        para = []

        def _flush_para():
            if not para:
                return
            chunk = u"\n".join(para).strip(u"\n")
            del para[:]
            if not chunk.strip() and not state["icon"]:
                return
            tb = _new_tb()
            if state["icon"]:
                self._add_icon_run(tb, state["icon"], icon_color)
                state["icon"] = None
            self._build_md_inlines(tb, chunk)
            tb.Margin = Thickness(0, 0, 0, 2)
            panel.Children.Add(tb)

        lines = (text or u"").splitlines() or [u""]
        i = 0
        n = len(lines)
        while i < n:
            s = lines[i].strip()

            # Fenced code block: ```lang ... ``` → monospace card
            if s.startswith(u"```"):
                i += 1
                code_lines = []
                while i < n and not lines[i].strip().startswith(u"```"):
                    code_lines.append(lines[i])
                    i += 1
                if i < n:               # consume the closing fence
                    i += 1
                _flush_para()
                try:
                    panel.Children.Add(self._make_code_block(code_lines))
                except Exception:
                    para.extend(code_lines)
                continue

            # Horizontal rule: standalone ---, ***, ___
            if s in (u"---", u"***", u"___"):
                _flush_para()
                try:
                    panel.Children.Add(self._make_hr())
                except Exception:
                    pass
                i += 1
                continue

            if s.startswith(u"|") and s.count(u"|") >= 2:
                tbl = []
                while i < n:
                    s2 = lines[i].strip()
                    if s2.startswith(u"|") and s2.count(u"|") >= 2:
                        tbl.append(s2)
                        i += 1
                    else:
                        break
                _flush_para()
                table = self._make_md_table(tbl)
                if table is not None:
                    panel.Children.Add(table)
                else:
                    para.extend(tbl)     # unparseable → show as raw text
                continue
            para.append(lines[i])
            i += 1
        _flush_para()

        if panel.Children.Count == 0:
            panel.Children.Add(_new_tb())
        return panel

    def _append_bot_message(self, text, icon=None, icon_color=None, actions=True):
        """Add an assistant reply — plain text on the pane, no bubble.

        Only the user's turn carries a filled bubble; the assistant's answer
        renders straight onto the chat background, full width, with the
        Claude-style action row (copy · try again · 👍 · 👎) underneath. In a
        docked pane barely 320px wide, dropping the bubble border and the
        avatar gutter is not only the reference look — it is the difference
        between a readable answer and a column of two-word lines.

        icon/icon_color: optional Segoe MDL2 glyph + RGB tuple rendered before
        the text, in its own FontFamily run — the minimal-icon replacement for
        the old baked-in colored emoji prefixes.
        """
        # Never render an empty reply — a blank assistant row looks like a
        # broken answer (observed with agent turns whose text was consumed
        # elsewhere).
        if not (text or u"").strip() and icon is None:
            return

        # A pending "Try again" folds this answer into the row it was fired
        # from instead of stacking a second copy underneath it.
        if getattr(self, '_retry_target_row', None) is not None:
            if self._adopt_retry_version(text, icon=icon, icon_color=icon_color):
                return

        try:
            from System.Windows.Controls import TextBlock, Grid, StackPanel, ColumnDefinition
            from System.Windows import Thickness, TextWrapping, GridLength

            row = Grid()
            row.Margin = Thickness(0, 0, 8, 16)
            col_msg = ColumnDefinition()
            col_msg.Width = GridLength(1, System.Windows.GridUnitType.Star)
            row.ColumnDefinitions.Add(col_msg)

            host = StackPanel()
            Grid.SetColumn(host, 0)
            row.Children.Add(host)

            # State for the action row: the text itself, any regenerated
            # versions, and the prompt that produced it (for Try again).
            row.Tag = {
                'versions':   [text],
                'index':      0,
                'icon':       icon,
                'icon_color': icon_color,
                'vote':       None,
                'host':       host,
                'prompt':     getattr(self, '_last_raw', u"") or u"",
                # What produced this reply — so a 👍/👎 can act on the route
                # and not just be filed in the activity log.
                'decision':   self._vote_decision(),
            }

            # Block renderer: paragraphs + real table grids. Falls back to a
            # plain TextBlock if anything in the renderer throws.
            try:
                content = self._render_md_blocks(text, icon=icon, icon_color=icon_color)
            except Exception:
                content = TextBlock()
                content.FontSize     = 13.5
                content.FontFamily   = System.Windows.Media.FontFamily("Hanken Grotesk, Inter")
                _bind_fg(content, 'BotText')
                content.TextWrapping = TextWrapping.Wrap
                content.LineHeight   = 21
                content.Text         = text
            host.Children.Add(content)

            if actions:
                try:
                    acts = self._make_message_actions(row)
                    if acts is not None:
                        host.Children.Add(acts)
                except Exception as ex:
                    logger.debug(u"message actions skipped: {}".format(_exc_text(ex)))

            self.chat_history_panel.Children.Add(row)
            self._scroll_to_bottom()
        except Exception as ex:
            logger.debug(u"Error adding bot message: {}".format(_exc_text(ex)))

    # ─── Live streaming bubble ────────────────────────────────────────────────

    def _begin_stream_bubble(self):
        """Create an empty assistant row that will be filled token-by-token.

        Same bubble-less shape as a finished reply (see _append_bot_message),
        so text does not visibly re-flow when streaming ends and the markdown
        renderer takes over. Stores references in self._stream_row /
        self._stream_tb / self._stream_host. UI thread only.
        """
        try:
            from System.Windows.Controls import TextBlock, Grid, StackPanel, ColumnDefinition
            from System.Windows import Thickness, TextWrapping, GridLength

            row = Grid()
            row.Margin = Thickness(0, 0, 8, 16)
            col_msg = ColumnDefinition()
            col_msg.Width = GridLength(1, System.Windows.GridUnitType.Star)
            row.ColumnDefinitions.Add(col_msg)

            host = StackPanel()
            Grid.SetColumn(host, 0)
            row.Children.Add(host)

            tb = TextBlock()
            tb.FontSize     = 13.5
            tb.FontFamily   = System.Windows.Media.FontFamily("Hanken Grotesk, Inter")
            _bind_fg(tb, 'BotText')
            tb.TextWrapping = TextWrapping.Wrap
            tb.LineHeight   = 21
            host.Children.Add(tb)

            self.chat_history_panel.Children.Add(row)
            self._stream_row  = row
            self._stream_tb   = tb
            self._stream_host = host
            self._scroll_to_bottom()
        except Exception as ex:
            logger.debug(u"_begin_stream_bubble error: {}".format(_exc_text(ex)))
            self._stream_row  = None
            self._stream_tb   = None
            self._stream_host = None

    def _finalize_stream_bubble(self, text):
        """Re-render the live bubble with full markdown once streaming is done.

        The streaming TextBlock showed plain text; swap the bubble's content
        for the block renderer so tables/headings in the finished reply get
        their real layout. Falls back to inline-only rendering on error.
        """
        try:
            tb = self._stream_tb
            if tb is None:
                return
            row  = self._stream_row
            host = getattr(self, '_stream_host', None)

            # A pending "Try again" folds the streamed answer into the row it
            # was fired from — the throwaway stream row goes away entirely.
            if getattr(self, '_retry_target_row', None) is not None:
                if self._adopt_retry_version(text):
                    self._remove_stream_bubble()
                    return

            if host is not None and row is not None:
                # Promote the streaming row to a full reply row: markdown body
                # + the action icons, identical to _append_bot_message output.
                row.Tag = {
                    'versions':   [text],
                    'index':      0,
                    'icon':       None,
                    'icon_color': None,
                    'vote':       None,
                    'host':       host,
                    'prompt':     getattr(self, '_last_raw', u"") or u"",
                    'decision':   self._vote_decision(),
                }
                self._refresh_reply_row(row)
            else:
                tb.Inlines.Clear()
                try:
                    self._build_md_inlines(tb, text)
                except Exception:
                    tb.Text = text
            self._scroll_to_bottom()
        except Exception:
            # Last resort: leave the plain streamed text in place rather than
            # losing an answer the user already watched arrive.
            try:
                if self._stream_tb is not None:
                    self._stream_tb.Text = text
            except Exception:
                pass

    def _remove_stream_bubble(self):
        """Discard the live row (used when an action renders its own reply)."""
        try:
            if self._stream_row is not None:
                self.chat_history_panel.Children.Remove(self._stream_row)
        except Exception:
            pass
        self._stream_row  = None
        self._stream_tb   = None
        self._stream_host = None

    def _clear_stream_refs(self):
        """Detach references so the row persists but is no longer 'live'."""
        self._stream_row  = None
        self._stream_tb   = None
        self._stream_host = None

    def _stream_llm_turn(self, provider, router, history, system_prompt,
                         query, max_tokens=1200, **kwargs):
        """Run one streaming LLM turn, filling a live bubble. Worker thread.

        The bubble is created lazily on the first token so the animated typing
        indicator stays visible until the model actually starts replying.
        Returns the full raw response text (str) or None.
        """
        extractor = StreamingJSONExtractor()
        state = {"raw": [], "started": False}

        def _on_delta(chunk):
            if not chunk:
                return
            state["raw"].append(chunk)
            disp = _hide_reasoning(extractor.display(u"".join(state["raw"])))

            def _ui():
                if not state["started"]:
                    if not disp:
                        return
                    state["started"] = True
                    self._hide_typing_indicator()
                    self._begin_stream_bubble()
                try:
                    if self._stream_tb is not None:
                        # Live text is plain (fast); markdown applied on finalize.
                        self._stream_tb.Text = disp
                        self._scroll_to_bottom()
                except Exception:
                    pass

            try:
                self.Dispatcher.Invoke(Action(_ui))
            except Exception:
                pass

        try:
            # **kwargs MUST be forwarded. The tool loop's first (streamed) turn
            # passes response_format={"type":"json_object"}; dropping it meant
            # iteration 1 ran without JSON mode, so a prose reply failed the
            # JSON regex and a correct answer the user had just watched stream
            # in was overwritten with "Could not read data from the model".
            # Both chat_stream implementations already accept **kwargs.
            # is_configured(), NOT check_health(): the health probe is a live
            # GET /models on the cloud providers, so gating the turn on it put
            # a network round-trip (8s timeout, no retry) in front of every
            # message and silently rerouted through the router whenever a proxy
            # dropped it. Attempt the real call and let its own error surface —
            # same rationale as has_api_key() in Intelligence/t3lab_assistant.py.
            if provider is not None and _provider_usable(provider):
                return provider.chat_stream(
                    history[-16:], system_prompt, query, _on_delta,
                    max_tokens, **kwargs)
            return router.chat_stream(
                history[-16:], system_prompt, query, _on_delta,
                max_tokens, **kwargs)
        except Exception as ex:
            logger.debug(u"_stream_llm_turn error: {}".format(_exc_text(ex)))
            return None

    def _scroll_to_bottom(self):
        try:
            self.chat_scroll.ScrollToBottom()
        except Exception:
            pass


# MAIN SCRIPT
# ==================================================



def show_assistant_dialog(is_docked=False):
    """Show T3Lab Assistant — via Dockable Pane if possible, otherwise as a companion window."""
    if not revit.doc:
        forms.alert("Please open a Revit document first.", exitscript=True)
        return None

    try:
        from Autodesk.Revit.UI import DockablePaneId, DockablePane
        from GUI.AssistantPaneControl import ASSISTANT_PANE_GUID, AssistantPaneProvider

        pane_id = DockablePaneId(ASSISTANT_PANE_GUID)
        uiapp = _get_uiapp()
        if uiapp is None:
            raise AttributeError("no UIApplication available")

        # Attempt dynamic registration if not yet registered in Revit
        if not DockablePane.PaneExists(pane_id):
            uictrld = _get_uicontrolled_app()
            if uictrld is not None and hasattr(uictrld, 'RegisterDockablePane'):
                try:
                    uictrld.RegisterDockablePane(pane_id, 'T3Lab Assistant', AssistantPaneProvider())
                    logger.info("Registered DockablePane dynamically via UIControlledApplication.")
                except Exception as ex_dyn:
                    logger.debug("Dynamic UIControlledApplication.RegisterDockablePane: {}".format(ex_dyn))

        if not DockablePane.PaneExists(pane_id):
            if hasattr(uiapp, 'RegisterDockablePane'):
                try:
                    uiapp.RegisterDockablePane(pane_id, 'T3Lab Assistant', AssistantPaneProvider())
                    logger.info("Registered DockablePane dynamically via UIApplication.")
                except Exception as ex_dyn2:
                    logger.debug("Dynamic UIApplication.RegisterDockablePane: {}".format(ex_dyn2))

        if DockablePane.PaneExists(pane_id):
            pane = uiapp.GetDockablePane(pane_id)
            if pane:
                if pane.IsShown():
                    pane.Hide()
                else:
                    pane.Show()
                return pane
        else:
            raise Exception("DockablePane 'T3Lab Assistant' requires Revit restart to dock.")
    except Exception as ex:
        logger.warning(u"Dockable pane unavailable: {}. Opening companion tool window.".format(_exc_text(ex)))
        window = T3LabAssistantWindow(is_docked=False)
        window.ShowDialog()
        return window
