# -*- coding: utf-8 -*-
"""
CPython 3 test harness for the T3Lab Assistant's chat-surface UI.

Run:  python3 dev/test_assistant_ui.py
Exit code 0 = all pass. No external test framework — plain asserts, mirroring
dev/test_assistant_routing.py / dev/test_tool_registry.py conventions.

T3LabAssistant.xaml is UI-LOCKED (see .claude/CLAUDE.md): it deliberately
deviates from the Lumina standard, so dev/audit_ui.py skips it entirely and
dev/sync_wpf_styles.py must never touch it. That left the one window with the
most bespoke styling in the codebase with nothing checking it at all — which is
how a vertical-only scrollbar style ended up being used for horizontal
scrollbars, and how two fully-hardcoded styles sat unreferenced for months.

These tests hold the *rules the lock exists to protect*, not the visual design:
colours come from theme tokens, both palettes stay in step, the scrollbar
handles both orientations, and the auto-synced region stays untouched.
"""
from __future__ import unicode_literals

import ast
import io
import os
import re
import sys
import xml.etree.ElementTree as ET

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from tabdir import TAB  # noqa: E402  (the tab folder name changes)
EXT = os.path.join(REPO, 'T3Lab.extension')
XAML = os.path.join(EXT, 'lib', 'GUI', 'Tools', 'T3LabAssistant.xaml')
THEME = os.path.join(EXT, 'lib', 'GUI', 'RevitTheme.py')
_dialog_path = os.path.join(EXT, 'lib', 'GUI', 'T3LabAssistantDialog.py')
SCRIPT = _dialog_path if os.path.exists(_dialog_path) else os.path.join(
    TAB, 'Support.panel', 'T3LabAssistant.pushbutton', 'script.py')

FAILURES = []


def check(label, ok, detail=None):
    line = '  {}  {}'.format('ok  ' if ok else 'FAIL', label)
    if not ok and detail is not None:
        line += '   {!r}'.format(detail)
    print(line)
    if not ok:
        FAILURES.append(label)


def _read(path):
    with io.open(path, 'r', encoding='utf-8') as f:
        return f.read()


XAML_SRC = _read(XAML)
THEME_SRC = _read(THEME)
SCRIPT_SRC = _read(SCRIPT)


def _palettes():
    out = {}
    for node in ast.walk(ast.parse(THEME_SRC)):
        if (isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ('_LIGHT', '_DARK')):
            out[node.targets[0].id] = ast.literal_eval(node.value)
    return out


# ─── theme tokens ─────────────────────────────────────────────────────────────

def test_palettes_stay_in_step():
    """A token in one palette and not the other is a hole that only shows up
    in the theme nobody was looking at."""
    print('[theme: palettes]')
    pals = _palettes()
    check('both palettes found', set(pals) == {'_LIGHT', '_DARK'})
    light, dark = set(pals['_LIGHT']), set(pals['_DARK'])
    check('light has no token dark lacks', not (light - dark),
          sorted(light - dark))
    check('dark has no token light lacks', not (dark - light),
          sorted(dark - light))
    check('every value is a hex colour',
          all(re.match(r'^#[0-9A-Fa-f]{6}$', v)
              for p in pals.values() for v in p.values()))


def test_light_palette_is_copied_faithfully():
    """The light palette exists in RevitTheme._LIGHT (live) and script.py's
    _LIGHT_FALLBACK RGB table (used when RevitTheme cannot be imported at all).

    The copies must agree token for token.
    """
    print('[theme: palette copies]')
    light = _palettes()['_LIGHT']

    fallback = {}
    for node in ast.walk(ast.parse(SCRIPT_SRC)):
        if (isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == '_LIGHT_FALLBACK'):
            fallback = ast.literal_eval(node.value)
    check('the no-theme fallback table was found', bool(fallback))
    # A subset is fine — it only needs the tokens Python actually reads through
    # _trgb/_tb — but every entry it does carry must be the same colour.
    check('the fallback invents no token', not (set(fallback) - set(light)),
          sorted(set(fallback) - set(light)))
    drift = {k: (light[k], '#%02X%02X%02X' % v) for k, v in fallback.items()
             if k in light and light[k].upper() != '#%02X%02X%02X' % v}
    check('the fallback RGB matches the light palette', not drift, drift)


def test_every_token_reference_resolves():
    """A {DynamicResource T3ThemeTypo} silently paints nothing."""
    print('[theme: references]')
    tokens = set(_palettes()['_LIGHT'])

    # T3Theme<Token> is a brush; T3Theme<Token>Color is the raw Color that
    # DropShadowEffect.Color needs (a brush bound there silently fails).
    xaml_refs = set(re.findall(r'DynamicResource\s+T3Theme(\w+?)(?:Color)?\}',
                               XAML_SRC))
    check('every XAML token exists',
          not (xaml_refs - tokens), sorted(xaml_refs - tokens))

    py_refs = set(re.findall(
        r"_bind_(?:fg|bg|border|stroke)\([^,]+,\s*'(\w+)'\)", SCRIPT_SRC))
    py_refs |= set(re.findall(r"_tb\('(\w+)'\)", SCRIPT_SRC))
    py_refs |= set(re.findall(r"_trgb\('(\w+)'\)", SCRIPT_SRC))
    py_refs |= set(re.findall(r"_theme_color\('(\w+)'\)", SCRIPT_SRC))
    check('every Python token exists',
          not (py_refs - tokens), sorted(py_refs - tokens))


def test_apply_publishes_brush_and_colour():
    """DropShadowEffect.Color is typed Color, not Brush. Publishing only
    brushes is why the popup shadows had to carry a hardcoded hex."""
    print('[theme: apply]')
    check('apply writes the brush key',
          "res[RESOURCE_PREFIX + token] = brush(token, theme)" in THEME_SRC)
    check('apply writes the colour key',
          "res[RESOURCE_PREFIX + token + 'Color'] = color(token, theme)"
          in THEME_SRC)


def test_rendered_messages_follow_the_theme():
    """_tb() returns a frozen brush, so anything painted with it keeps its
    render-time colours forever. Code-built chat content must bind instead."""
    print('[theme: live binding]')
    check('the binding helper exists', 'def _bind_theme(' in SCRIPT_SRC)
    check('it uses SetResourceReference',
          'SetResourceReference' in SCRIPT_SRC)
    check('it falls back to a plain assignment',
          re.search(r'setattr\(element, prop_name, _tb\(token\)\)',
                    SCRIPT_SRC) is not None)
    for name in ('_bind_fg', '_bind_bg', '_bind_border', '_bind_stroke'):
        # once in the themed branch, once in the no-RevitTheme fallback
        check('{} defined in both import branches'.format(name),
              SCRIPT_SRC.count('def {}('.format(name)) == 2,
              SCRIPT_SRC.count('def {}('.format(name)))

    # No frozen brush may be assigned straight onto a visual property again.
    leaked = re.findall(
        r'^\s*[\w\.]+\.(?:Foreground|Background|BorderBrush|Stroke)'
        r'\s*=\s*_tb\(', SCRIPT_SRC, re.MULTILINE)
    check('no frozen brush assigned to a visual property', not leaked, leaked)


# ─── scrollbar ────────────────────────────────────────────────────────────────

def test_thin_scrollbar_handles_both_orientations():
    """The T3 scrollbar style handles both orientations — 5px width for vertical
    and 5px height for horizontal, with direction reversed only on vertical."""
    print('[scrollbar: orientation]')
    check('a ScrollBar style exists',
          '<Style TargetType="{x:Type ScrollBar}">' in XAML_SRC)
    check('the style switches on Orientation',
          re.search(r'<Trigger Property="Orientation" Value="Horizontal">',
                    XAML_SRC) is not None)
    check('the vertical track is reversed',
          re.search(r'<Trigger Property="Orientation" Value="Vertical">.*?IsDirectionReversed="true"',
                    XAML_SRC, re.S) is not None)
    check('the horizontal track is NOT reversed',
          re.search(r'<Trigger Property="Orientation" Value="Horizontal">.*?IsDirectionReversed="false"',
                    XAML_SRC, re.S) is not None)
    check('the horizontal thumb is sized by height',
          re.search(r'<Trigger Property="Orientation" Value="Horizontal">.*?Height="5"',
                    XAML_SRC, re.S) is not None)


def test_thin_scrollbar_covers_the_whole_window():
    """The shared T3 block ships an IMPLICIT ScrollBar style.
    Anything not explicitly overridden inherits it automatically."""
    print('[scrollbar: coverage]')
    check('an implicit ScrollBar style is declared',
          '<Style TargetType="{x:Type ScrollBar}">' in XAML_SRC)
    check('the composer still scrolls its own draft',
          'chat_input.VerticalScrollBarVisibility' in SCRIPT_SRC)


# ─── the T3 standard rules ───────────────────────────────────────────────────

def test_shared_style_block_is_untouched():
    print('[lock: shared styles]')
    check('the sync markers are both present',
          '<!-- ═══ T3 STYLES — SINH TỰ ĐỘNG' in XAML_SRC
          and '<!-- ═══ HẾT T3 STYLES ═══ -->' in XAML_SRC)


def test_no_stray_hardcoded_colours():
    """Every colour goes through a token or T3 resource."""
    print('[lock: hardcoded colours]')
    body = XAML_SRC.split('<!-- ═══ HẾT T3 STYLES ═══ -->', 1)[-1]
    allowed = {'#F59E0B', '#CC18181B', '#7F18181B'}
    # The token fallback table itself is where hexes are supposed to live.
    body = re.sub(r'<SolidColorBrush x:Key="T3Theme\w+"[^/]*/>', '', body)
    body = re.sub(r'<Color x:Key="T3Theme\w+">[^<]*</Color>', '', body)
    stray = sorted(set(re.findall(r'"(#[0-9A-Fa-f]{6,8})"', body)) - allowed)
    check('no hardcoded colour outside the token table', not stray, stray)


def test_no_dead_styles():
    """A style with no reference is either a leftover or a wiring bug. Some are
    referenced only from Python via FindResource, so both files are searched.

    Only styles this window declares for itself are policed: the auto-synced
    shared block is copied verbatim into all 53 tool windows and is expected to
    carry styles any individual one does not use.
    """
    print('[lock: dead styles]')
    own = XAML_SRC.split('<!-- ═══ HẾT T3 STYLES ═══ -->', 1)[-1]
    dead = []
    for key in re.findall(r'<Style x:Key="(\w+)"', own):
        uses = (XAML_SRC.count('StaticResource ' + key + '}')
                + XAML_SRC.count('BasedOn="{StaticResource ' + key + '}')
                + SCRIPT_SRC.count("'" + key + "'")
                + SCRIPT_SRC.count('"' + key + '"'))
        if uses == 0:
            dead.append(key)
    check('every declared style is referenced', not dead, dead)


def test_popups_fit_a_docked_pane():
    """A pane docked beside the Project Browser is routinely 300-340px, and the
    skills popup alone declares MinWidth 340."""
    print('[layout: narrow pane]')
    check('popup widths are clamped to the pane',
          'def _fit_popups_to_width(' in SCRIPT_SRC)
    check('it runs on every width change, not just the compact transition',
          re.search(r'if e\.WidthChanged:.*?_fit_popups_to_width',
                    SCRIPT_SRC, re.S) is not None)
    check('static DPs are not read off the instance',
          'FrameworkElement.MinWidthProperty' in SCRIPT_SRC)


_P = '{http://schemas.microsoft.com/winfx/2006/xaml/presentation}'
_X = '{http://schemas.microsoft.com/winfx/2006/xaml}'


def _tree():
    root = ET.fromstring(XAML_SRC)
    parent = {c: p for p in root.iter() for c in p}
    return root, parent


def _named(root, name):
    for el in root.iter():
        if el.get(_X + 'Name') == name:
            return el
    return None


def _method(name):
    m = re.search(r'\n    def {}\(.*?(?=\n    def |\nclass |\Z)'.format(name),
                  SCRIPT_SRC, re.S)
    return m.group() if m else ''


def _column_width(el, parent):
    """Width of the ColumnDefinition `el` sits in, or None."""
    grid = parent.get(el)
    if grid is None or grid.tag != _P + 'Grid':
        return None
    defs = grid.find(_P + 'Grid.ColumnDefinitions')
    if defs is None:
        return None
    cols = list(defs)
    idx = int(el.get('Grid.Column', '0'))
    return cols[idx].get('Width', '*') if idx < len(cols) else None


def test_pane_has_no_wide_floor():
    """Owner report 2026-10-02: docked at ~400px beside Properties, the
    greeting, the composer hint, the project / mode row and the copyright were
    cut off on the right. The pane content had MinWidth 380 — and a 400px
    dock at 125% scaling is only 320 DIP, so everything past 320 was clipped
    instead of laid out."""
    print('[layout: pane floor]')
    pane_src = _read(os.path.join(EXT, 'lib', 'GUI', 'AssistantPaneControl.py'))
    m = re.search(r'^PANE_MIN_WIDTH\s*=\s*(\d+)', pane_src, re.M)
    floor = int(m.group(1)) if m else None
    check('the pane floor is a named constant', floor is not None)
    check('the pane floor is below a 300px dock at 125% (240 DIP)',
          floor is not None and floor <= 240, floor)
    check('the pane content uses it',
          'content.MinWidth = PANE_MIN_WIDTH' in pane_src)
    check('no hardcoded 380 floor on the pane content',
          re.search(r'content\.MinWidth\s*=\s*3\d\d', pane_src) is None)
    root, _ = _tree()
    chrome = _named(root, 'root_chrome')
    mw = chrome.get('MinWidth') if chrome is not None else None
    check('root_chrome (= the docked content) carries no wide MinWidth',
          mw is None or float(mw) <= 240, mw)


def test_text_adapts_instead_of_clipping():
    print('[layout: text]')
    root, parent = _tree()
    g = _named(root, 'welcome_greeting_text')
    check('the greeting wraps', g is not None and g.get('TextWrapping') == 'Wrap')
    check('the greeting is centred line by line',
          g is not None and g.get('TextAlignment') == 'Center')
    panel = _named(root, 'welcome_greeting_panel')
    check('the greeting panel spans the width (a centred panel would not)',
          panel is not None
          and panel.get('HorizontalAlignment', 'Stretch') == 'Stretch')

    ph = _named(root, 'composer_placeholder')
    check('the composer hint trims',
          ph is not None and ph.get('TextTrimming') == 'CharacterEllipsis')
    short = re.search(r'_PLACEHOLDER_SHORT\s*=\s*u?"([^"]+)"', SCRIPT_SRC)
    full = re.search(r'_PLACEHOLDER_FULL\s*=\s*u?"([^"]+)"', SCRIPT_SRC)
    check('the narrow hint is shorter than the full one',
          short and full and len(short.group(1)) < len(full.group(1)))
    check('the full hint matches the XAML text',
          full is not None and ph is not None
          and ph.get('Text') == full.group(1))

    for name in ('model_chip_text', 'project_chip_text', 'revit_ctx_view'):
        el = _named(root, name)
        check('{} trims'.format(name),
              el is not None and el.get('TextTrimming') == 'CharacterEllipsis')

    # TextTrimming inside a horizontal StackPanel never fires: the panel
    # measures its children with infinite width. Every one of these was a
    # label that clipped at the pane edge instead of trimming.
    dead = []
    for el in root.iter(_P + 'TextBlock'):
        if not el.get('TextTrimming'):
            continue
        anc = [a.tag for a in _ancestors(el, parent)]
        if _P + 'Window.Resources' in anc:
            continue
        p = parent.get(el)
        if (p is not None and p.tag == _P + 'StackPanel'
                and p.get('Orientation') == 'Horizontal'):
            dead.append(el.get(_X + 'Name') or el.get('Text') or el.get('Style'))
    check('no TextTrimming inside a horizontal StackPanel', not dead, dead)


def _ancestors(el, parent):
    while el in parent:
        el = parent[el]
        yield el


def test_trimming_chips_sit_in_star_columns():
    """An Auto column measures its content with infinite width, so a chip in
    one never trims — it pushes its neighbours out of the pane instead."""
    print('[layout: chip columns]')
    root, parent = _tree()
    model = _named(root, 'model_chip_btn')
    check('the model chip sits in a * column',
          model is not None and _column_width(model, parent) == '*',
          model is not None and _column_width(model, parent))
    check('... right-aligned, next to Send',
          model is not None and model.get('HorizontalAlignment') == 'Right')
    proj = _named(root, 'project_chip_btn')
    check('the project chip sits in a * column',
          proj is not None and _column_width(proj, parent) == '*')
    inner = parent.get(proj) if proj is not None else None
    check('... of a LEFT-aligned grid, so Mode stays beside it on a wide pane',
          inner is not None and inner.get('HorizontalAlignment') == 'Left')
    check('... and that grid fills a * column of the row',
          inner is not None and _column_width(inner, parent) == '*')


def test_footer_keeps_the_copyright_in_the_pane():
    print('[layout: footer]')
    root, parent = _tree()
    crs = [el for el in root.iter(_P + 'TextBlock')
           if el.get('Style') == '{StaticResource T3.Copyright}']
    check('exactly one copyright line', len(crs) == 1, len(crs))
    cr = crs[0] if crs else None
    box = parent.get(cr) if cr is not None else None
    check('the footer is a DockPanel (children measured against what is left)',
          box is not None and box.tag == _P + 'DockPanel',
          box is not None and box.tag)
    check('the copyright is the left-most child',
          box is not None and list(box)[0] is cr
          and cr.get('DockPanel.Dock') == 'Left')
    check('the copyright trims rather than being pushed out',
          cr is not None and cr.get('TextTrimming') == 'CharacterEllipsis')
    check('the footer is not centred any more (T3: copyright left-aligned)',
          box is not None and box.get('HorizontalAlignment') in (None, 'Stretch'))


def test_labels_collapse_on_a_narrow_pane():
    print('[layout: breakpoints]')
    root, _ = _tree()
    body = _method('_apply_narrow_layout')
    check('_apply_narrow_layout exists', bool(body))
    check('it runs on every width change',
          re.search(r'if e\.WidthChanged:.*?_apply_narrow_layout',
                    _method('_on_size_changed'), re.S) is not None)
    check('it runs once at startup too',
          '_apply_narrow_layout(' in _method('_apply_revit_skin'))
    labels = re.findall(r"'(\w+_(?:label|text))'", body)
    check('it collapses the secondary labels', len(labels) >= 4, labels)
    missing = [n for n in labels if _named(root, n) is None]
    check('every label it collapses exists in the XAML', not missing, missing)
    check('it accounts for the floating window controls',
          'float_ctrls_panel' in body and '_FLOAT_CTRLS_WIDTH' in body)

    def _const(name):
        m = re.search(r'^\s+{}\s*=\s*(\d+)'.format(name), SCRIPT_SRC, re.M)
        return int(m.group(1)) if m else None
    compact_body = _method('_apply_compact_layout')
    check('the first-run card gets compact padding too',
          _named(root, 'onboarding_card') is not None
          and 'onboarding_card.Padding' in compact_body)

    narrow, compact = _const('NARROW_WIDTH'), _const('COMPACT_WIDTH')
    check('NARROW_WIDTH < COMPACT_WIDTH',
          narrow is not None and compact is not None and narrow < compact,
          (narrow, compact))

    # An icon-only button must stay symmetric: the gap rides on the label.
    lopsided = []
    for n in labels:
        el = _named(root, n)
        if el is not None and el.get('Margin', '0').split(',')[0] in ('0', ''):
            lopsided.append(n)
    check('the icon-to-label gap sits on the label', not lopsided, lopsided)


def test_popups_stay_inside_the_pane():
    """Placement Top/Bottom aligns a popup's LEFT edge with its target. The
    model chip and Prompts & Skills sit on the right, so their popups hung
    past the pane over Revit's Properties palette."""
    print('[layout: popup placement]')
    check('the placement helper exists',
          'def _keep_popup_in_pane(' in SCRIPT_SRC)
    for popup in ('model_popup', 'saved_prompts_popup', 'project_popup'):
        check('{} is kept inside the pane before it opens'.format(popup),
              re.search(r'_keep_popup_in_pane\(self\.{0}\)\s*\n\s*self\.{0}'
                        r'\.IsOpen = True'.format(popup), SCRIPT_SRC)
              is not None)


def test_theme_survives_the_docked_detach():
    """AssistantPaneControl hands the content to Revit and detaches it:

        content = win.Content
        win.Content = None
        data.FrameworkElement = content

    Window.Resources is where RevitTheme.apply() writes the T3Theme* brushes,
    so after that detach it is no longer an ancestor scope of the tree that
    binds to them. Every {DynamicResource T3Theme*} in the docked pane then
    resolves to nothing, Background falls back to null, and the pane shows
    Revit's black HwndSource through it while Revit itself is in Light theme.
    The tokens must therefore also live on the content root, which travels
    with the content.
    """
    print('[theme: docked pane]')
    pane_src = _read(os.path.join(EXT, 'lib', 'GUI', 'AssistantPaneControl.py'))
    check('the pane really does detach the content',
          re.search(r'win\.Content\s*=\s*None', pane_src) is not None)

    check('theme scopes are enumerated', 'def _theme_scopes(' in SCRIPT_SRC)
    scopes = re.search(r'def _theme_scopes\(.*?(?=\n    def )', SCRIPT_SRC, re.S)
    check('the content root is one of them',
          scopes is not None and 'root_chrome' in scopes.group(), )
    check('the window is still one of them',
          scopes is not None and re.search(r'scopes\.append\(self\)',
                                           scopes.group()) is not None)

    sync = re.search(r'def _sync_theme\(.*?(?=\n    def )', SCRIPT_SRC, re.S)
    check('_sync_theme applies to every scope',
          sync is not None and '_theme_scopes()' in sync.group())
    check('_sync_theme no longer skins the window alone',
          sync is not None
          and re.search(r'_theme\.apply\(self,', sync.group()) is None)


def test_docked_pane_keeps_following_the_host():
    """Docked, the Window is never shown and never activated: IsVisible is
    permanently False and Activated never fires. Gating the poll on the
    Window meant it returned early on every tick for the life of the pane,
    so a theme change made in Options was never picked up."""
    print('[theme: docked resync]')
    tick = re.search(r'def _on_context_tick\(.*?(?=\n    def )', SCRIPT_SRC, re.S)
    check('context tick found', tick is not None)
    body = tick.group() if tick else ''
    check('visibility is probed on the content, not the window',
          'root_chrome' in body and not re.search(r'if not self\.IsVisible', body))
    check('the tick re-syncs the theme', '_sync_theme()' in body)


def test_xaml_is_well_formed():
    print('[xaml: syntax]')
    try:
        ET.parse(XAML)
        ok, detail = True, None
    except ET.ParseError as ex:
        ok, detail = False, str(ex)
    check('T3LabAssistant.xaml parses', ok, detail)


def main():
    print('')
    for fn in (test_palettes_stay_in_step,
               test_light_palette_is_copied_faithfully,
               test_every_token_reference_resolves,
               test_apply_publishes_brush_and_colour,
               test_rendered_messages_follow_the_theme,
               test_thin_scrollbar_handles_both_orientations,
               test_thin_scrollbar_covers_the_whole_window,
               test_shared_style_block_is_untouched,
               test_no_stray_hardcoded_colours,
               test_no_dead_styles,
               test_popups_fit_a_docked_pane,
               test_pane_has_no_wide_floor,
               test_text_adapts_instead_of_clipping,
               test_trimming_chips_sit_in_star_columns,
               test_footer_keeps_the_copyright_in_the_pane,
               test_labels_collapse_on_a_narrow_pane,
               test_popups_stay_inside_the_pane,
               test_theme_survives_the_docked_detach,
               test_docked_pane_keeps_following_the_host,
               test_xaml_is_well_formed):
        fn()
        print('')

    if FAILURES:
        print('{} FAILURE(S): {}'.format(len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('All assistant UI tests passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
