# -*- coding: utf-8 -*-
"""BGThemeService — Headless service for Revit canvas background and theme management."""

import os
import sys
import json
try:
    import Autodesk.Revit.DB as DB
except Exception:
    DB = None

LIB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT_DIR = os.path.dirname(LIB_DIR)
CONFIG_PATH = os.path.join(
    EXT_DIR, 'T3Lab.tab', 'Support.panel', 'UI.stack', 'BG Theme.pushbutton', 'dqt_bg_config.json'
)
if not os.path.exists(os.path.dirname(CONFIG_PATH)):
    CONFIG_PATH = os.path.join(LIB_DIR, 'dqt_bg_config.json')

PRESETS = [
    ("Black",      (0,   0,   0)),
    ("Charcoal",   (45,  45,  45)),
    ("Graphite",   (70,  74,  82)),
    ("Gray",       (190, 190, 190)),
    ("Silver",     (226, 228, 231)),
    ("White",      (255, 255, 255)),
    ("Dark Blue",  (28,  40,  64)),
    ("Studio",     (54,  61,  74)),
    ("Warm Paper", (243, 238, 227)),
]

DEFAULT_GRADIENT = {
    "sky": [68, 118, 189],
    "horizon": [205, 224, 240],
    "ground": [142, 134, 114],
    "all_views": False,
}


def clamp(v):
    try:
        v = int(round(float(v)))
    except Exception:
        v = 0
    return max(0, min(v, 255))


def _clamp_rgb(seq, fallback):
    try:
        return [clamp(seq[0]), clamp(seq[1]), clamp(seq[2])]
    except Exception:
        return list(fallback)


def load_config():
    """Return the full config dict with defaults / legacy keys filled in."""
    data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}

    if "r" not in data:
        try:
            from pyrevit import revit
            app = getattr(revit, 'app', None)
            if app is None:
                c = __revit__.Application.BackgroundColor
            else:
                c = app.BackgroundColor
            data["r"], data["g"], data["b"] = c.Red, c.Green, c.Blue
        except Exception:
            data["r"] = data["g"] = data["b"] = 0

    data["r"] = clamp(data.get("r", 0))
    data["g"] = clamp(data.get("g", 0))
    data["b"] = clamp(data.get("b", 0))
    data["live_apply"] = bool(data.get("live_apply", False))

    presets = []
    for p in data.get("custom_presets", []) or []:
        try:
            presets.append({
                "name": str(p.get("name", "?")),
                "rgb": _clamp_rgb(p.get("rgb", []), (0, 0, 0))
            })
        except Exception:
            pass
    data["custom_presets"] = presets

    recents = []
    for c in data.get("recents", []) or []:
        try:
            recents.append(_clamp_rgb(c, (0, 0, 0)))
        except Exception:
            pass
    data["recents"] = recents

    grad = data.get("gradient", {}) or {}
    data["gradient"] = {
        "sky": _clamp_rgb(grad.get("sky", []), DEFAULT_GRADIENT["sky"]),
        "horizon": _clamp_rgb(grad.get("horizon", []), DEFAULT_GRADIENT["horizon"]),
        "ground": _clamp_rgb(grad.get("ground", []), DEFAULT_GRADIENT["ground"]),
        "all_views": bool(grad.get("all_views", False)),
    }
    return data


def save_config(cfg):
    """Persist configuration to disk."""
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


def apply_background(r, g, b):
    """Set Revit application canvas background color."""
    try:
        from pyrevit import revit
        app = getattr(revit, 'app', None)
        if app is None:
            __revit__.Application.BackgroundColor = DB.Color(clamp(r), clamp(g), clamp(b))
        else:
            app.BackgroundColor = DB.Color(clamp(r), clamp(g), clamp(b))
    except Exception:
        try:
            __revit__.Application.BackgroundColor = DB.Color(clamp(r), clamp(g), clamp(b))
        except Exception:
            pass


def quick_cycle():
    """Cycle background color between Black, Gray, and White."""
    try:
        from pyrevit import revit
        app = getattr(revit, 'app', None)
        if app is not None:
            c = app.BackgroundColor
        else:
            c = __revit__.Application.BackgroundColor
        cr, cg, cb = c.Red, c.Green, c.Blue
    except Exception:
        cr = cg = cb = 0

    if cr >= 250 and cg >= 250 and cb >= 250:
        nr, ng, nb = 0, 0, 0
    elif cr <= 5 and cg <= 5 and cb <= 5:
        nr, ng, nb = 190, 190, 190
    else:
        nr, ng, nb = 255, 255, 255

    apply_background(nr, ng, nb)
    cfg = load_config()
    cfg["r"], cfg["g"], cfg["b"] = nr, ng, nb
    save_config(cfg)


def _doc():
    try:
        from pyrevit import revit
        if revit.doc:
            return revit.doc
    except Exception:
        pass
    try:
        uidoc = __revit__.ActiveUIDocument
        return uidoc.Document if uidoc else None
    except Exception:
        return None


def _all_3d_views(doc):
    views = []
    if not doc:
        return views
    try:
        for v in DB.FilteredElementCollector(doc).OfClass(DB.View3D):
            if not v.IsTemplate:
                views.append(v)
    except Exception:
        pass
    return views


def get_3d_context():
    ctx = {"view_name": None, "count_3d": 0}
    doc = _doc()
    if doc is None:
        return ctx
    ctx["count_3d"] = len(_all_3d_views(doc))
    try:
        from pyrevit import revit
        av = revit.active_view if hasattr(revit, 'active_view') else None
        if av is None:
            av = __revit__.ActiveUIDocument.ActiveView
        if isinstance(av, DB.View3D) and not av.IsTemplate:
            ctx["view_name"] = av.Name
    except Exception:
        pass
    return ctx


def _target_3d_views(all_views):
    doc = _doc()
    if doc is None:
        return None, []
    if all_views:
        return doc, _all_3d_views(doc)
    try:
        from pyrevit import revit
        av = revit.active_view if hasattr(revit, 'active_view') else None
        if av is None:
            av = __revit__.ActiveUIDocument.ActiveView
        if isinstance(av, DB.View3D) and not av.IsTemplate:
            return doc, [av]
    except Exception:
        pass
    return doc, []


def _set_views_background(bg, all_views, action_label):
    doc, views = _target_3d_views(all_views)
    if doc is None:
        return False, "No active document."
    if doc.IsReadOnly:
        return False, "The document is read-only."
    if not views:
        return False, "No 3D view target — open a 3D view or tick 'all 3D views'."
    t = DB.Transaction(doc, "BG Theme - 3D Background")
    t.Start()
    done, failed = 0, 0
    try:
        for v in views:
            try:
                v.SetBackground(bg)
                done += 1
            except Exception:
                failed += 1
        t.Commit()
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        return False, "Failed: %s" % ex
    if done == 0:
        return False, "%s failed on every targeted view." % action_label
    msg = "%s on %d 3D view(s)." % (action_label, done)
    if failed:
        msg += " (%d view(s) skipped)" % failed
    return True, msg


def apply_view_gradient(sky, horizon, ground, all_views):
    try:
        bg = DB.ViewDisplayBackground.CreateGradient(
            DB.Color(clamp(sky[0]), clamp(sky[1]), clamp(sky[2])),
            DB.Color(clamp(horizon[0]), clamp(horizon[1]), clamp(horizon[2])),
            DB.Color(clamp(ground[0]), clamp(ground[1]), clamp(ground[2]))
        )
    except Exception as ex:
        return False, "Gradient backgrounds are not supported here: %s" % ex
    return _set_views_background(bg, all_views, "Gradient applied")


def clear_view_background(all_views):
    factory = getattr(DB.ViewDisplayBackground, "CreateNone", None)
    if factory is None:
        return False, "Clearing the background is not supported by this Revit version — apply a plain gradient instead."
    try:
        bg = factory()
    except Exception as ex:
        return False, "Failed to create empty background: %s" % ex
    return _set_views_background(bg, all_views, "Background cleared")


def get_theme_info():
    info = {
        "supported": False,
        "current": None,
        "options": [],
        "canvas_supported": False,
        "canvas_current": None,
        "canvas_options": [],
    }
    try:
        import Autodesk.Revit.UI as RUI
        from System import Enum
        mgr = getattr(RUI, "UIThemeManager", None)
        theme_enum = getattr(RUI, "UITheme", None)
        if mgr is not None and theme_enum is not None:
            info["current"] = str(mgr.CurrentTheme)
            info["options"] = list(Enum.GetNames(theme_enum))
            info["supported"] = True
        canvas_enum = getattr(RUI, "CanvasTheme", None)
        if (mgr is not None and canvas_enum is not None
                and hasattr(mgr, "CurrentCanvasTheme")):
            info["canvas_current"] = str(mgr.CurrentCanvasTheme)
            info["canvas_options"] = list(Enum.GetNames(canvas_enum))
            info["canvas_supported"] = True
    except Exception:
        pass
    return info


def set_ui_theme(name):
    try:
        import Autodesk.Revit.UI as RUI
        from System import Enum
        RUI.UIThemeManager.CurrentTheme = Enum.Parse(RUI.UITheme, name)
        return True, "UI theme set to %s." % name
    except Exception as ex:
        return False, "Could not set UI theme: %s" % ex


def set_canvas_theme(name):
    try:
        import Autodesk.Revit.UI as RUI
        from System import Enum
        RUI.UIThemeManager.CurrentCanvasTheme = Enum.Parse(RUI.CanvasTheme, name)
        return True, "Canvas theme set to %s." % name
    except Exception as ex:
        return False, "Could not set canvas theme: %s" % ex


def get_theme_callbacks():
    """Return dictionary of callbacks for BGThemeDialog."""
    return {
        "apply_background": apply_background,
        "save_config": save_config,
        "get_3d_context": get_3d_context,
        "apply_view_gradient": apply_view_gradient,
        "clear_view_background": clear_view_background,
        "get_theme_info": get_theme_info,
        "set_ui_theme": set_ui_theme,
        "set_canvas_theme": set_canvas_theme,
    }
