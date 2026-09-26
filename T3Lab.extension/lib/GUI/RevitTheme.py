# -*- coding: utf-8 -*-
"""
Revit theme bridge for the T3Lab Assistant surface.

Two jobs:

1. **Follow Revit.** Revit 2024+ exposes ``UIThemeManager.CurrentTheme``
   (Light / Dark). The assistant lives inside a Revit DockablePane, so a
   hardcoded light surface reads as a foreign window glued into the host —
   a white rectangle glaring out of a dark Revit. ``current_theme()`` reads
   the host theme (Light on older hosts, which have no dark mode) and every
   palette lookup follows it.

2. **One palette, one place.** The assistant is a *chat* surface, not a tool
   dialog, so it does not use the Lumina tool palette in
   ``.claude/rules/ui-design-standard.md``; it uses **Revit's own UI greys**
   plus the chat split (filled user bubble · bubble-less assistant text). That
   deviation is deliberate and scoped to this one surface — the copyright amber
   ``#F59E0B`` and the shared button styles are untouched, so the Lumina audit
   still passes. Everything else routes through the tokens below rather than
   being re-typed as a hex literal in 70 different places.

Usage from XAML (re-themes live, no reload):

    Background="{DynamicResource T3ThemeChatBg}"

Usage from code (bubbles, chips, cards built in script.py):

    from GUI.RevitTheme import brush, rgb
    border.Background = brush('CardBg')

``apply(window)`` writes every token into the element's ``Resources`` as a
``SolidColorBrush`` named ``T3Theme<Token>``, so a theme flip is a single
call — WPF re-evaluates every ``DynamicResource`` that points at it.

Pure Python + guarded .NET imports, so it stays importable under CPython 3 for
the headless tests.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals


# ─── Palettes ─────────────────────────────────────────────────────────────────
# Both palettes are Revit's own UI greys, not a chat product's palette. The
# assistant sits in a dockable pane between the Project Browser and Properties,
# so anything with a hue of its own — the warm "paper" cream this used to be,
# or a saturated brand accent — reads as a foreign app pasted over the host.
#
# Anchors, per theme, taken from Revit's own chrome:
#
#   Light   palette/ribbon chrome #F0F0F0 · list & field surfaces #FFFFFF
#           separators #D9D9D9 · control borders #C4C4C4 · label text black
#   Dark    (Revit 2024+) chrome #2E2E2E · palette content #383838
#           fields/menus #414141 · separators #4A4A4A · label text #E4E4E4
#
# The one colour in the window is Autodesk blue — the same blue Revit uses for
# its own highlights, so the send button, the active chip and the selection
# count stay identifiable without importing a second brand. It is tuned per
# theme (see 'Accent' in each palette), not one fixed hex.

_LIGHT = {
    # ══════════════════════════════════════════════════════════════════════════
    # NOT A DESIGN. These are Revit's own values, decoded out of
    # UIFramework.dll -> themes/themecolorlight.baml with Baml2006Reader
    # (dev/dump_revit_theme.ps1 reproduces it). The comment after each entry is
    # the Revit key it came from; entries without one are ours because Revit has
    # no counterpart.
    #
    # The earlier hand-matched values were wrong in most places — buttons were
    # near-white with a blue hover wash where Revit uses flat greys, the accent
    # was #0A6FB3 where Revit uses #0696D7, and the dark surfaces were neutral
    # greys where Revit uses a blue-grey family. Do not "tidy" these back toward
    # round numbers; re-run the dump instead.
    # ══════════════════════════════════════════════════════════════════════════

    # Surfaces
    'AppBg':                   '#F5F5F5',   # RibbonPanelBackgroundColor
    'DialogBg':                '#F5F5F5',   # RibbonPanelBackgroundColor. ApplicationClientAreaBackgroundColor (#EEEEEE) is tuned to sit beside the dark canvas and reads heavy in a dialog
    'ChromeBg':             '#FFFFFF',   # dialog caption is WHITE, lighter than the body. ChromeBackgroundColor (#D9D9D9) is the MAIN WINDOW chrome and is wrong here
    'ChatBg':                  '#FFFFFF',   # AGridInteriorColor — content/list surface
    'CardBg':                  '#FFFFFF',   # ComboItemBgColor — popup / drop-down surface
    'CardBorder':              '#DEDEDE',   # HigOptionBarBtnBorderDisabled (was ComboBorderColor #CCCCCC)
    'Divider':                 '#ECECEC',   # AGridBorderColor
    'GroupBorder':             '#E5E5E5',   # ComboDisabledBorderColor (was AGridButtonBorderColor #BBBBBB, far too heavy for a section frame)
    'PaneEdge':                '#DEDEDE',   # HigOptionBarBtnBorderDisabled (was LightChromeBorderColor #C1C1C1)

    # Type
    'Ink':                     '#3C3C3C',   # ButtonTextColor — Revit's control text is #3C3C3C, not black
    'GroupHeaderFg':           '#3C3C3C',   # ButtonTextColor
    'Muted':                   '#5A5A5A',   # ForegroundDisabledColor
    'Faint':                   '#ABABAB',   # ComboDisabledTextColor — placeholders

    # Accent
    'Accent':                  '#0696D7',   # PushButtonBorderFocusColor — Autodesk blue: #0696D7 light / #38ABDF dark
    'AccentHover':             '#1893CA',   # OKButtonHoverColor
    'AccentSoft':              '#D6E9F7',   # tint fill; Revit has no key for it
    'Blue':                    '#006EAF',   # HigHyperlinkText — links
    'OnAccent':                '#FFFFFF',   # glyph on an accent fill

    # Push buttons
    'ControlBg':               '#FFFFFF',   # ComboItemBgColor. PushButtonFaceColor (#EEEEEE) equals the old body, so buttons registered only as grey outlines
    'ControlBorder':           '#CCCCCC',   # ComboBorderColor (was PushButtonBorderColor #B7B7B7)
    'ControlHoverBg':          '#F0F0F0',   # DisabledBackgroundColor (was PushButtonHoverColor #DEDEDE)
    'ControlHoverBorder':      '#B7B7B7',   # PushButtonBorderColor
    'ControlPressedBg':        '#E6E6E6',   # AGridHeaderColor (was PushButtonPressedColor #D2D2D2)
    'ControlPressedBorder':    '#AFAFAF',   # PushButtonBorderHoverColor
    'ControlDisabledBg':       '#F5F5F5',   # AGridButtonDisabledColor
    'ControlDisabledBorder':   '#E5E5E5',   # ComboDisabledBorderColor
    'ControlDisabledFg':       '#A7A7A7',   # PushButtonDisabledTextColor

    # Fields
    'FieldBg':                 '#FFFFFF',   # ComboItemBgColor. Fields go white so they lift off the body instead of blending into it
    'FieldBorder':             '#DEDEDE',   # HigOptionBarBtnBorderDisabled. A white field needs a lighter edge than a grey one did
    'FieldFocusBorder':        '#0696D7',   # ComboPressedBorderColor
    'IconFg':                  '#919191',   # ComboArrowColor
    'IconFgHover':             '#3C3C3C',   # ComboArrowPressedColor
    'IconHoverBg':             '#EAEAEA',   # HigIconBkgHover
    'InputText':               '#3C3C3C',   # ComboTextColor
    'InputCaret':              '#3C3C3C',   # ComboTextColor

    # Lists / schedules
    'HeaderBg':                '#F0F0F0',   # DisabledBackgroundColor. ScheduleHeaderColor (#E1E1E1) is a dark band on top of a white grid
    'HeaderFg':                '#3C3C3C',   # ButtonTextColor
    'RowAltBg':                '#FAFAFA',   # NOT a Revit key — Revit does not zebra-stripe, and #FFFFFF here would be identical to the row. A near-white alternate keeps long lists readable without adding a grey band
    'RowHoverBg':              '#F3F3F3',   # ComboItemHoverColor
    'RowSelectedBg':           '#0696D7',   # accent. ScheduleHeaderSelectedColor (#D4D4D4) is the schedule HEADER, not a selected row
    'SelectedBg':              '#E3E3E3',   # TreeViewUnFocusBackgroundColor
    'GridLine':                '#ECECEC',   # AGridBorderColor (was ScheduleHeaderBorderColor #CECECE)

    # Progress + status strip
    'ProgressTrack':           '#CECECE',   # HigProgressCtrlBkg
    'ProgressBorder':       '#B7B7B7',   # no Revit key; PushButtonBorderColor is the same hairline
    'ProgressFill':            '#38ABDF',   # HigProgressCtrlFront — #38ABDF on BOTH themes — Revit's dialog progress is blue
    'StatusBarBg':             '#F5F5F5',   # HigStatusBarBkg
    'TrackBg':                 '#CECECE',   # HigProgressCtrlBkg — slider groove
    'ThumbBg':                 '#FFFFFF',   # ComboItemBgColor

    # Tabs
    'TabStripBg':              '#E4E4E4',
    'TabActiveBg':             '#FFFFFF',   # AGridInteriorColor
    'TabHoverBg':              '#F3F3F3',   # ComboItemHoverColor

    # Status colours — Revit has no semantic set; these stay ours
    'Success':                 '#0B7A4A',
    'Warning':                 '#9A6700',
    'Danger':                  '#C42B1C',
    'SuccessSoft':             '#E6F2EA',
    'WarningSoft':             '#FDF3E2',
    'DangerSoft':              '#FBE9E7',
    'Amber':                   '#F59E0B',   # copyright (T3Lab standard, do not retint)
    'CheckFill':               '#0696D7',   # PushButtonBorderFocusColor — checked box; the real state is an image, this is the fallback

    # Chat surface (T3LabAssistant) — unchanged, that window is tuned
    'ComposerBg':              '#FFFFFF',   # AGridInteriorColor
    'UserBubbleBg':            '#DBE8F6',
    'UserBubbleText':          '#000000',
    'BotText':                 '#000000',   # AGridTextColor
    'CodeBg':                  '#F5F5F5',
    'CodeFg':                  '#1C1C1C',
    'ScrollThumb':             '#C1C1C1',
    'Shadow':                  '#000000',
}

_DARK = {
    # ══════════════════════════════════════════════════════════════════════════
    # NOT A DESIGN. These are Revit's own values, decoded out of
    # UIFramework.dll -> themes/themecolordark.baml with Baml2006Reader
    # (dev/dump_revit_theme.ps1 reproduces it). The comment after each entry is
    # the Revit key it came from; entries without one are ours because Revit has
    # no counterpart.
    #
    # The earlier hand-matched values were wrong in most places — buttons were
    # near-white with a blue hover wash where Revit uses flat greys, the accent
    # was #0A6FB3 where Revit uses #0696D7, and the dark surfaces were neutral
    # greys where Revit uses a blue-grey family. Do not "tidy" these back toward
    # round numbers; re-run the dump instead.
    # ══════════════════════════════════════════════════════════════════════════

    # Surfaces
    'AppBg':                   '#2E3440',   # DarkApplicationClientAreaBackgroundColor
    'DialogBg':                '#2E3440',   # DarkApplicationClientAreaBackgroundColor (applicationthemebase)
    'ChromeBg':             '#3B4453',   # one step above the body, keeping the caption-lighter order
    'ChatBg':                  '#454F61',   # ComboItemBgColor. AGridInteriorColor (#3B4453) is what Revit uses, but it equals HigStatusBarBkg on dark, so the list and the footer strip would collapse into one band
    'CardBg':                  '#454F61',   # ComboItemBgColor — popup / drop-down surface
    'CardBorder':              '#687689',   # ComboBorderColor
    'Divider':                 '#485263',   # AGridBorderColor
    'GroupBorder':             '#7C8BA1',   # AGridButtonBorderColor — group-box frame
    'PaneEdge':                '#515C6B',   # DarkChromeBorderColor

    # Type
    'Ink':                     '#F5F5F6',   # ButtonTextColor — Revit's control text is #3C3C3C, not black
    'GroupHeaderFg':           '#F5F5F6',   # ButtonTextColor
    'Muted':                   '#989CA4',   # AGridDisabledTextColor; ForegroundDisabledColor (#DDDDDD) sits too near Ink on dark
    'Faint':                   '#858B94',   # ComboDisabledTextColor — placeholders

    # Accent
    'Accent':                  '#38ABDF',   # PushButtonBorderFocusColor — Autodesk blue: #0696D7 light / #38ABDF dark
    'AccentHover':             '#21A0DB',   # OKButtonHoverColor
    'AccentSoft':              '#1E3A4A',   # tint fill; Revit has no key for it
    'Blue':                    '#6DD2FF',   # HigHyperlinkText — links
    'OnAccent':                '#FFFFFF',   # glyph on an accent fill

    # Push buttons
    'ControlBg':               '#2E3440',   # PushButtonFaceColor — #EEEEEE — Revit buttons are GREY, not near-white
    'ControlBorder':           '#758379',   # PushButtonBorderColor
    'ControlHoverBg':          '#434C5A',   # PushButtonHoverColor — grey, NOT a blue wash
    'ControlHoverBorder':      '#808FA4',   # PushButtonBorderHoverColor
    'ControlPressedBg':        '#515C6B',   # PushButtonPressedColor
    'ControlPressedBorder':    '#8797AD',   # PushButtonBorderPressedColor
    'ControlDisabledBg':       '#2E3440',   # PushButtonDisabledColor
    'ControlDisabledBorder':   '#4A5463',   # PushButtonBorderDisabledColor
    'ControlDisabledFg':       '#7E8188',   # PushButtonDisabledTextColor

    # Fields
    'FieldBg':                 '#3B4453',   # ComboBackgroundColor — #F5F5F5 — off-white, not white
    'FieldBorder':             '#687689',   # ComboBorderColor
    'FieldFocusBorder':        '#38ABDF',   # ComboPressedBorderColor
    'IconFg':                  '#7C8BA1',   # ComboArrowColor
    'IconFgHover':             '#D0D2D7',   # ComboArrowPressedColor
    'IconHoverBg':             '#333C49',   # HigIconBkgHover
    'InputText':               '#F5F5F5',   # ComboTextColor
    'InputCaret':              '#F5F5F5',   # ComboTextColor

    # Lists / schedules
    'HeaderBg':                '#1A1F25',   # ScheduleHeaderColor
    'HeaderFg':                '#F5F5F6',   # ButtonTextColor
    'RowAltBg':                '#3B4453',   # AGridInteriorColor — Revit schedules do not zebra-stripe
    'RowHoverBg':              '#3A4353',   # ComboItemHoverColor
    'RowSelectedBg':           '#38ABDF',   # accent
    'SelectedBg':              '#2A313B',   # TreeViewUnFocusBackgroundColor
    'GridLine':                '#303742',   # ScheduleHeaderBorderColor

    # Progress + status strip
    'ProgressTrack':           '#383E47',   # HigProgressCtrlBkg
    'ProgressBorder':       '#758379',   # PushButtonBorderColor
    'ProgressFill':            '#38ABDF',   # HigProgressCtrlFront — #38ABDF on BOTH themes — Revit's dialog progress is blue
    'StatusBarBg':             '#3B4453',   # HigStatusBarBkg
    'TrackBg':                 '#383E47',   # HigProgressCtrlBkg — slider groove
    'ThumbBg':                 '#2E3440',   # PushButtonFaceColor — slider thumb

    # Tabs
    'TabStripBg':              '#2A313B',
    'TabActiveBg':             '#3B4453',   # AGridInteriorColor
    'TabHoverBg':              '#3A4353',   # ComboItemHoverColor

    # Status colours — Revit has no semantic set; these stay ours
    'Success':                 '#3FBF87',
    'Warning':                 '#E0A94F',
    'Danger':                  '#F08880',
    'SuccessSoft':             '#23372E',
    'WarningSoft':             '#3A3123',
    'DangerSoft':              '#3A2725',
    'Amber':                   '#F59E0B',   # copyright (T3Lab standard, do not retint)
    'CheckFill':               '#38ABDF',   # PushButtonBorderFocusColor — checked box; the real state is an image, this is the fallback

    # Chat surface (T3LabAssistant) — unchanged, that window is tuned
    'ComposerBg':              '#3B4453',   # AGridInteriorColor
    'UserBubbleBg':            '#33475A',
    'UserBubbleText':          '#F5F5F5',
    'BotText':                 '#F5F5F5',   # AGridTextColor
    'CodeBg':                  '#262626',
    'CodeFg':                  '#E4E4E4',
    'ScrollThumb':             '#5A5A5A',
    'Shadow':                  '#000000',
}

_PALETTES = {'light': _LIGHT, 'dark': _DARK}

#: Resource-key prefix used for the brushes written by :func:`apply`.
RESOURCE_PREFIX = 'T3Theme'

_brush_cache = {}       # (theme, token) -> SolidColorBrush
_forced_theme = None    # set by force_theme() for previews/tests

_use_host = True        # prefer Revit's own brushes over the curated copies
_host_dicts = {}        # theme -> ResourceDictionary | False (tried, unavailable)
_host_hex_cache = {}    # (theme, token) -> '#RRGGBB' | None
_host_error = None      # why the bridge found nothing, for the diagnostic
_host_usable_cache = {}  # theme -> (usable, resolved, total)

#: Fraction of the mapped tokens the host must answer for before its values
#: are used at all. Below this the curated palette wins outright — it is the
#: same product's colours anyway, and a consistent copy beats a mixture.
HOST_MIN_COVERAGE = 0.75


# ─── Host theme detection ─────────────────────────────────────────────────────

def current_theme():
    """``'dark'`` or ``'light'`` — whichever Revit is currently using.

    ``UIThemeManager`` only exists on Revit 2024+. Older hosts have no dark
    mode at all, so 'light' is the correct answer there, not a fallback we
    are settling for.
    """
    if _forced_theme is not None:
        return _forced_theme
    try:
        import clr
        clr.AddReference('RevitAPIUI')
        import Autodesk.Revit.UI as _RUI
        mgr = getattr(_RUI, 'UIThemeManager', None)
        if mgr is None:
            return 'light'
        if 'dark' in str(mgr.CurrentTheme).lower():
            return 'dark'
    except Exception:
        pass
    return 'light'


def force_theme(theme):
    """Pin the theme ('light' / 'dark'), or pass None to follow Revit again."""
    global _forced_theme
    _forced_theme = theme if theme in _PALETTES else None


# ─── Host palette bridge ──────────────────────────────────────────────────────
# The two palettes above are a *curated copy* of Revit's greys — values read off
# the product and typed in here. Close, but still a copy: it drifts the moment
# Autodesk retunes a grey, and by construction it can never be more than close.
#
# It does not have to be a copy. Revit paints its own dialogs from a WPF
# ResourceDictionary that ships inside UIFramework.dll and is reachable from
# in-process:
#
#     UIFramework.ApplicationTheme.CurrentTheme     the dictionary Revit is
#                                                   painting with right now
#     UIFramework.ApplicationTheme.FullLightTheme   the light one, explicitly
#     UIFramework.ApplicationTheme.FullDarkTheme    the dark one, explicitly
#
# Pulling a colour out of there is not "matching Revit"; it is the same brush
# value the host uses for its own combo boxes. A T3Lab field and a Revit field
# then agree to the pixel, and keep agreeing across product updates.
#
# Key naming is version-dependent. Verified against the installed products:
#
#   Revit 2025 / 2026   keys carry a theme suffix — ComboBoxBorderBrush_LightTheme,
#                       ComboBoxBorderBrush_DarkTheme — and both Full*Theme
#                       dictionaries exist. Full coverage.
#   Revit 2023          one unsuffixed set (ComboBoxBorderBrush) and no light/dark
#                       split, because the product has no dark mode. Partial
#                       coverage. Note the shipped spelling CheckBoxBackGroundBrush
#                       — capital G — which is why the candidate lists below carry
#                       both spellings.
#   Revit 2024          could not be verified here. The lookup simply finds
#                       nothing and the curated value stands.
#
# Every token the host does not expose keeps its curated value, so an unknown
# host degrades to exactly today's behaviour — never to an unpainted control.
# ``use_host_palette(False)`` turns the whole bridge off if a mapping ever reads
# wrong on a version we could not test.

#: token -> Revit resource keys to try, best first. Only tokens with a genuine
#: counterpart in Revit's own chrome appear here; the rest are ours to choose.
_HOST_KEYS = {
    # Verified against the decoded dictionaries (dev/dump_revit_theme.ps1).
    # Revit exposes TWO naming schemes and both are tried: the HIG/ThemeColor
    # names below, and the suffixed ApplicationThemeFull* names after them.
    'DialogBg':             ('MainWindowBackgroundColor', 'ChromeBackgroundBrush'),
    'AppBg':                ('MainWindowBackgroundColor', 'ChromeBackgroundBrush'),
    'ChatBg':               ('AGridInteriorColor', 'TreeCtrlBackgroundColor'),
    'CardBg':               ('ComboItemBgColor', 'DropDownListBoxBackgroundBrush'),
    'CardBorder':           ('ComboBorderColor', 'DropDownListBoxBorderBrush'),
    'Divider':              ('AGridBorderColor', 'ComboSeperatorColor',
                             'ListBoxSeparatorBrush'),
    'GroupBorder':          ('AGridButtonBorderColor', 'HigOptionBarBtnBorder'),
    'PaneEdge':             ('HigStatusBarBarrage',),

    'Ink':                  ('ButtonTextColor', 'ChromeForegroundBrush'),
    'GroupHeaderFg':        ('ButtonTextColor',),
    'Muted':                ('AGridDisabledTextColor',),
    'Faint':                ('ComboDisabledTextColor', 'TextPromptForegroundBrush'),

    'Accent':               ('PushButtonBorderFocusColor', 'ComboPressedBorderColor'),
    'AccentHover':          ('OKButtonHoverColor',),
    'Blue':                 ('HigHyperlinkText',),
    'CheckFill':            ('PushButtonBorderFocusColor',),

    'ControlBg':            ('PushButtonFaceColor',),
    'ControlBorder':        ('PushButtonBorderColor',),
    'ControlHoverBg':       ('PushButtonHoverColor',),
    'ControlHoverBorder':   ('PushButtonBorderHoverColor',),
    'ControlPressedBg':     ('PushButtonPressedColor',),
    'ControlPressedBorder': ('PushButtonBorderPressedColor',),
    'ControlDisabledBg':    ('PushButtonDisabledColor', 'TextBoxDisabledBackgroundBrush'),
    'ControlDisabledBorder': ('PushButtonBorderDisabledColor',),
    'ControlDisabledFg':    ('PushButtonDisabledTextColor', 'DisabledForegroundBrush'),

    'FieldBg':              ('ComboBackgroundColor', 'ComboBoxBackgroundBrush'),
    'FieldBorder':          ('ComboBorderColor', 'ComboBoxBorderBrush'),
    'FieldFocusBorder':     ('ComboPressedBorderColor', 'HigOptionBarEditBorderFocus'),
    'IconFg':               ('ComboArrowColor', 'ComboBoxArrowBrush'),
    'IconFgHover':          ('ComboArrowPressedColor',),
    'IconHoverBg':          ('HigIconBkgHover',),
    'InputText':            ('ComboTextColor',),
    'InputCaret':           ('ComboTextColor',),

    'HeaderBg':             ('ScheduleHeaderColor', 'AGridHeaderColor',
                             'ListHeaderBackgroundBrush'),
    'HeaderFg':             ('ButtonTextColor',),
    'RowAltBg':             ('AGridInteriorColor',),
    'RowHoverBg':           ('ComboItemHoverColor', 'ListBoxRollOverBackgroundBrush'),
    'RowSelectedBg':        ('ScheduleHeaderSelectedColor', 'ListBoxSelectBackgroundBrush'),
    'SelectedBg':           ('TreeViewUnFocusBackgroundColor',),
    'GridLine':             ('ScheduleHeaderBorderColor', 'AGridBorderColor'),

    'ProgressTrack':        ('HigProgressCtrlBkg',),
    'ProgressFill':         ('HigProgressCtrlFront',),
    'ProgressBorder':       ('PushButtonBorderColor',),
    'StatusBarBg':          ('HigStatusBarBkg',),
    'TrackBg':              ('HigProgressCtrlBkg',),
    'ThumbBg':              ('PushButtonFaceColor',),

    'TabActiveBg':          ('AGridInteriorColor',),
    'TabHoverBg':           ('ComboItemHoverColor',),
}


def use_host_palette(enabled=True):
    """Turn the live Revit lookup on or off. On by default."""
    global _use_host
    _use_host = bool(enabled)
    _brush_cache.clear()
    _host_hex_cache.clear()
    _host_usable_cache.clear()


def _host_dictionary(theme):
    """Revit's own ResourceDictionary for `theme`, or None.

    Two ways in, tried in order:

    1. ``UIFramework.ApplicationTheme`` — the live dictionary Revit is painting
       with. Preferred, because it is whatever the running product decided.
    2. The pack URI for ``themes/themecolor{light,dark}.xaml``. Slower, but it
       does not depend on the ApplicationTheme members being reachable from
       IronPython, which is the failure this fallback exists for.

    ``CurrentTheme`` is only accepted when it *is* the theme being asked for —
    previewing light while Revit runs dark would otherwise silently return the
    dark dictionary and paint a light window in dark greys.

    On failure the reason is kept in ``_host_error`` rather than thrown away, so
    a window can report "0 of N from Revit — <reason>" instead of just 0.
    """
    global _host_error
    cached = _host_dicts.get(theme)
    if cached is not None:
        return cached or None

    found = None
    reasons = []

    try:
        import clr
        try:
            clr.AddReference('UIFramework')
        except Exception as ex:
            reasons.append('AddReference: %s' % ex)
        import UIFramework
        app_theme = getattr(UIFramework, 'ApplicationTheme', None)
        if app_theme is None:
            reasons.append('no UIFramework.ApplicationTheme')
        else:
            names = ['FullDarkTheme'] if theme == 'dark' else ['FullLightTheme']
            if theme == current_theme():
                names.append('CurrentTheme')
            for name in names:
                try:
                    candidate = getattr(app_theme, name, None)
                except Exception as ex:
                    reasons.append('%s: %s' % (name, ex))
                    continue
                if candidate is None:
                    reasons.append('%s is None' % name)
                elif not hasattr(candidate, 'Keys'):
                    reasons.append('%s is %s' % (name, type(candidate).__name__))
                else:
                    found = candidate
                    break
    except Exception as ex:
        reasons.append('import UIFramework: %s' % ex)

    if found is None:
        # Fallback: load the theme colour dictionary straight off the assembly.
        # These are the 90 keys the curated palette was decoded from, so a hit
        # here is exactly as good as a hit above.
        leaf = 'themecolordark' if theme == 'dark' else 'themecolorlight'
        uri = 'pack://application:,,,/UIFramework;component/themes/%s.xaml' % leaf
        try:
            from System import Uri, UriKind
            from System.Windows import ResourceDictionary
            rd = ResourceDictionary()
            rd.Source = Uri(uri, UriKind.Absolute)
            if hasattr(rd, 'Keys') and rd.Count:
                found = rd
        except Exception as ex:
            reasons.append('pack uri: %s' % ex)

    if found is None:
        _host_error = '; '.join(reasons) or 'no reason captured'
    else:
        _host_error = None

    _host_dicts[theme] = found if found is not None else False
    return found


def _solid_hex(resource):
    """'#RRGGBB' for a SolidColorBrush or a raw Color; None for anything else.

    Gradient brushes have no ``.Color`` and drop out here on their own, which
    is what we want — a gradient cannot stand in for a flat token.
    """
    if resource is None:
        return None
    col = getattr(resource, 'Color', None)
    if col is None and hasattr(resource, 'R') and hasattr(resource, 'B'):
        col = resource                 # the *Color_* keys are raw Colors
    if col is None:
        return None
    try:
        if int(getattr(col, 'A', 255)) < 8:
            return None                # transparent: useless as a surface value
        return '#%02X%02X%02X' % (int(col.R), int(col.G), int(col.B))
    except Exception:
        return None


def _host_hex_raw(token, theme):
    """The colour Revit itself uses for `token`, or None. Ungated."""
    key = (theme, token)
    if key in _host_hex_cache:
        return _host_hex_cache[key]

    value = None
    candidates = _HOST_KEYS.get(token)
    if candidates:
        rd = _host_dictionary(theme)
        if rd is not None:
            suffix = '_DarkTheme' if theme == 'dark' else '_LightTheme'
            for base in candidates:
                for name in (base + suffix, base):
                    try:
                        value = _solid_hex(rd[name])
                    except Exception:
                        value = None
                    if value:
                        break
                if value:
                    break

    _host_hex_cache[key] = value
    return value


def host_coverage(theme=None):
    """``(usable, resolved, total)`` for the host lookup on `theme`.

    Probes every mapped token once and caches the verdict. ``usable`` is False
    when the host answered for fewer than :data:`HOST_MIN_COVERAGE` of them, in
    which case nothing from the host is used.
    """
    theme = theme or current_theme()
    cached = _host_usable_cache.get(theme)
    if cached is not None:
        return cached
    total = len(_HOST_KEYS)
    resolved = 0
    for token in _HOST_KEYS:
        if _host_hex_raw(token, theme):
            resolved += 1
    usable = total > 0 and (float(resolved) / total) >= HOST_MIN_COVERAGE
    verdict = (usable, resolved, total)
    _host_usable_cache[theme] = verdict
    return verdict


def _host_hex(token, theme):
    """The host's colour for `token`, but only if the host is trustworthy here."""
    if not host_coverage(theme)[0]:
        return None
    return _host_hex_raw(token, theme)


def host_resource(base, theme=None):
    """A raw resource object from Revit's dictionary — not a colour.

    Some of Revit's control states are not brushes at all. A checked check box
    is drawn from ``CheckBoxCheckedImage_LightTheme`` / ``_DarkTheme``: an
    ImageSource, not a fill. ``hex_of`` cannot carry that, so this returns the
    object itself and the caller decides what to do with it.
    """
    theme = theme or current_theme()
    rd = _host_dictionary(theme)
    if rd is None:
        return None
    suffix = '_DarkTheme' if theme == 'dark' else '_LightTheme'
    for name in (base + suffix, base):
        try:
            found = rd[name]
        except Exception:
            found = None
        if found is not None:
            return found
    return None


def _fallback_check_glyph(theme):
    """A white tick on the CheckFill square, built when the host has no image.

    Same shape as the host's, so the two are interchangeable in the template
    and the XAML never has to know which one it got.
    """
    try:
        from System.Windows import Rect, Point
        from System.Windows.Media import (DrawingGroup, GeometryDrawing,
                                          RectangleGeometry, PathGeometry,
                                          PathFigure, LineSegment, Pen,
                                          DrawingImage, PenLineCap)
        group = DrawingGroup()

        # Authored at 13px, the size the template asks for, so the tick is
        # pixel-crisp instead of being scaled down from a 16px drawing.
        box = GeometryDrawing()
        box.Brush = brush('CheckFill', theme)
        box.Geometry = RectangleGeometry(Rect(0, 0, 13, 13), 2, 2)
        group.Children.Add(box)

        fig = PathFigure()
        fig.StartPoint = Point(3.0, 6.9)
        fig.Segments.Add(LineSegment(Point(5.5, 9.4), True))
        fig.Segments.Add(LineSegment(Point(10.0, 4.2), True))
        geom = PathGeometry()
        geom.Figures.Add(fig)

        pen = Pen(brush('OnAccent', theme), 1.6)
        pen.StartLineCap = PenLineCap.Round
        pen.EndLineCap = PenLineCap.Round
        tick = GeometryDrawing()
        tick.Geometry = geom
        tick.Pen = pen
        group.Children.Add(tick)

        image = DrawingImage(group)
        try:
            image.Freeze()
        except Exception:
            pass
        return image
    except Exception:
        return None


# The progress bar needs no builder any more. Revit's dialog progress is a flat
# HigProgressCtrlFront (#38ABDF, the same on both themes) over HigProgressCtrlBkg,
# so it is two ordinary tokens. The Aero green gradient this used to build is the
# Win32 status-bar control from the *main window*, not what a dialog shows.


def host_font():
    """``(family, size)`` of the OS message font — what Revit's dialogs use.

    Hardcoding "Segoe UI" 12 is right on a default English or Vietnamese
    Windows and wrong the moment the user scales their UI font or runs a
    locale with a different shell face. Returns ``(None, None)`` when the
    lookup fails, and the caller keeps whatever the XAML declared.
    """
    try:
        from System.Windows import SystemFonts
        return (SystemFonts.MessageFontFamily, float(SystemFonts.MessageFontSize))
    except Exception:
        return (None, None)


# ─── Token lookup ─────────────────────────────────────────────────────────────

def palette(theme=None):
    """The whole ``{token: '#RRGGBB'}`` table for a theme."""
    return _PALETTES.get(theme or current_theme(), _LIGHT)


def hex_of(token, theme=None):
    """Hex string for a token.

    Revit's own value wins when the host exposes one (see the host bridge
    above); otherwise the curated palette answers. Unknown tokens fall back to
    the ink colour so a typo shows up as visibly wrong text instead of an
    invisible crash.

    Everything else in this module — ``rgb``, ``brush``, ``color``, ``apply``
    — routes through here, so the host bridge reaches XAML, code-built
    elements and the resource dictionary through one path.
    """
    theme = theme or current_theme()
    if _use_host:
        live = _host_hex(token, theme)
        if live:
            return live
    pal = _PALETTES.get(theme, _LIGHT)
    return pal.get(token, pal['Ink'])


def rgb(token, theme=None):
    """``(r, g, b)`` for a token — for code that builds ``Color.FromRgb``."""
    h = hex_of(token, theme).lstrip('#')
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def brush(token, theme=None):
    """A frozen ``SolidColorBrush`` for a token, cached per theme.

    Frozen so the same instance can be shared across every bubble without WPF
    having to track it per-element; cached so a 500-message conversation does
    not allocate 500 identical brushes.
    """
    theme = theme or current_theme()
    key = (theme, token)
    cached = _brush_cache.get(key)
    if cached is not None:
        return cached
    from System.Windows.Media import SolidColorBrush, Color
    r, g, b = rgb(token, theme)
    br = SolidColorBrush(Color.FromRgb(r, g, b))
    try:
        br.Freeze()
    except Exception:
        pass
    _brush_cache[key] = br
    return br


def color(token, theme=None):
    """A ``System.Windows.Media.Color`` for a token (shadows, gradients)."""
    from System.Windows.Media import Color
    r, g, b = rgb(token, theme)
    return Color.FromRgb(r, g, b)


# ─── Resource injection ───────────────────────────────────────────────────────

def apply(element, theme=None):
    """Write every token into ``element.Resources``.

    Two keys per token:

    ``T3Theme<Token>``
        a ``SolidColorBrush`` — what almost everything binds to.
    ``T3Theme<Token>Color``
        the raw ``Color``. Some properties are typed ``Color``, not ``Brush``
        — ``DropShadowEffect.Color`` above all — and a brush bound there
        silently fails to apply. Without this the popup and composer shadows
        had to carry a hardcoded hex, which meant they kept their light-theme
        value on Revit's dark chrome and stopped separating the popup from
        what was behind it.

    Call once after the XAML loads, and again whenever the host theme changes:
    WPF re-evaluates every ``DynamicResource`` bound to these keys, so the
    whole window re-skins without being rebuilt. Returns the theme applied, or
    None if the element has no usable resource dictionary.
    """
    theme = theme or current_theme()
    try:
        res = element.Resources
    except Exception:
        return None
    for token in palette(theme):
        try:
            res[RESOURCE_PREFIX + token] = brush(token, theme)
        except Exception:
            pass
        try:
            res[RESOURCE_PREFIX + token + 'Color'] = color(token, theme)
        except Exception:
            pass

    # Non-colour resources. The checked check box is an ImageSource in Revit,
    # so the XAML binds one key and gets whichever source is available: the
    # host's own image, or a drawn stand-in with the same geometry.
    try:
        glyph = host_resource('CheckBoxCheckedImage', theme) if _use_host else None
        if glyph is None:
            glyph = _fallback_check_glyph(theme)
        if glyph is not None:
            res[RESOURCE_PREFIX + 'CheckedGlyph'] = glyph
    except Exception:
        pass
    return theme
