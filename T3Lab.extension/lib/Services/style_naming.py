# -*- coding: utf-8 -*-
"""Pure name checks for Style Manager (ManaStyles) — no Revit, no WPF.

Kept import-free on purpose so `dev/test_style_naming.py` runs them without
Revit. `ManaStylesDialog` uses them for BOTH rename paths of the Line Styles,
Line Patterns and Fill Patterns tabs:
  * inline rename (double-click / F2 / Enter on the NAME cell),
  * the footer "Rename" button.

Every message follows "what · where · next" and is plain English (UI text).
"""

# Characters Revit refuses in a line style (Lines subcategory), line pattern
# and fill pattern name — the set Revit's own rename dialogs list:
#   \ : { } [ ] | ; < > ? ` ~
# Note: `/`, `*` and `"` are ALLOWED — imperial templates ship names such as
# `Dash 1/16"` and `Diagonal crosshatch 3/32"`.
INVALID_NAME_CHARS = '\\:{}[]|;<>?`~'

LINE_STYLE = 'line_style'
LINE_PATTERN = 'line_pattern'
FILL_PATTERN = 'fill_pattern'

KIND_LABELS = {
    LINE_STYLE: 'line style',
    LINE_PATTERN: 'line pattern',
    FILL_PATTERN: 'fill pattern',
}

# A line style rename creates a NEW Lines subcategory before deleting the old
# one, and Revit compares category names without case — "dash" -> "Dash"
# would collide with itself. Patterns rename in place, so a case-only change
# is fine there.
_CASE_ONLY_ALLOWED = {
    LINE_STYLE: False,
    LINE_PATTERN: True,
    FILL_PATTERN: True,
}


RENAME_HINT = "Double-click or press F2 to rename"


def rename_tooltip(kind, is_system):
    """Tooltip of a NAME cell: how to rename, or why it cannot be renamed."""
    if is_system:
        return "Built-in {} - cannot be renamed".format(kind_label(kind))
    return RENAME_HINT


def kind_label(kind):
    """'line style' / 'line pattern' / 'fill pattern'."""
    return KIND_LABELS.get(kind, 'item')


def _where(kind, old_name):
    label = kind_label(kind)
    return "{} '{}'".format(label[:1].upper() + label[1:], old_name)


def check_renamable(kind, old_name, is_system):
    """Can this row be renamed at all? Asked BEFORE entering edit mode.

    Returns (ok, message). Built-in rows (`<Thin Lines>`, `<Solid fill>`,
    system line patterns...) are refused with a message saying what to do.
    """
    if is_system:
        return False, (
            "{}: built-in {}s cannot be renamed in Revit. "
            "Duplicate it or pick a custom one to rename."
            .format(_where(kind, old_name), kind_label(kind)))
    return True, ""


def validate_style_rename(kind, old_name, new_name, existing_names, is_system=False):
    """Validate a rename of one Style Manager row.

    Args:
        kind: LINE_STYLE, LINE_PATTERN or FILL_PATTERN.
        old_name: current name of the row.
        new_name: raw text typed by the user (trimmed here).
        existing_names: current names of the other items of the same kind
            (for fill patterns: of the same Drafting/Model target). The row's
            own name may be included; it is skipped.
        is_system: True for built-in rows.

    Returns:
        (ok, clean_name, message). An unchanged name returns
        (False, clean, "") so the caller simply leaves edit mode.
    """
    clean = (new_name or "").strip()
    where = _where(kind, old_name)
    label = kind_label(kind)

    ok, message = check_renamable(kind, old_name, is_system)
    if not ok:
        return False, clean, message

    if not clean:
        return False, clean, (
            "{}: the name cannot be empty. Type a name or press Esc to cancel."
            .format(where))

    if clean == old_name:
        return False, clean, ""

    bad = sorted(set(c for c in clean if c in INVALID_NAME_CHARS or ord(c) < 32))
    if bad:
        shown = " ".join(c if ord(c) >= 32 else "\\x{:02x}".format(ord(c)) for c in bad)
        return False, clean, (
            "{}: '{}' contains characters Revit does not allow ({}). "
            "Remove them or press Esc to cancel.".format(where, clean, shown))

    folded = clean.casefold()
    old_folded = (old_name or "").casefold()

    if folded == old_folded and not _CASE_ONLY_ALLOWED.get(kind, True):
        return False, clean, (
            "{}: Revit treats '{}' as the same {} name (only the letter case "
            "differs). Rename it to a different name first, or press Esc to cancel."
            .format(where, clean, label))

    for other in existing_names or ():
        if other is None or other == old_name:
            continue
        if other.casefold() == folded:
            return False, clean, (
                "{}: a {} named '{}' already exists. "
                "Choose another name or press Esc to cancel.".format(where, label, other))

    return True, clean, ""


def renamed_status(kind, old_name, new_name, extra=""):
    """Status line after a successful rename: "Renamed line pattern 'A' to 'B'"."""
    text = "Renamed {} '{}' to '{}'".format(kind_label(kind), old_name, new_name)
    return text + (" " + extra if extra else "")


def sorted_position(names, name, current_index):
    """Index `name` must move to so `names` (sorted, case-sensitive like the
    tool's own `list.sort(key=name)`) stays sorted after the row at
    `current_index` was renamed to `name`.

    `names` is the list AFTER the rename (row already holds `name`).
    """
    others = [n for i, n in enumerate(names) if i != current_index]
    pos = 0
    for other in others:
        if other <= name:
            pos += 1
        else:
            break
    return pos
