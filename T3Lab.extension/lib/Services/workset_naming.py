# -*- coding: utf-8 -*-
"""Pure helpers for workset names — no Revit, no WPF.

Kept import-free on purpose so `dev/test_workset_naming.py` runs them without
Revit. `ManaWorksetDialog` uses them for:
  * `natural_sort_key` — WORKSET NAME column order (01_, 02_, ... 10_, ARC_...).
  * `validate_workset_rename` — inline rename checks before the Revit call.
"""

import re

# Characters Revit refuses in a workset name (same set as the Revit
# "Rename Workset" dialog and most element names).
INVALID_NAME_CHARS = '\\:{}[]|;<>?`~'

_DIGITS_RE = re.compile(r'(\d+)')


def natural_sort_key(name):
    """Natural, case-insensitive sort key.

    Digit runs compare as numbers ("Level 2" < "Level 10", "01_" == "1_" by
    value), text compares case-insensitively. `re.split` with a capturing group
    always alternates text, number, text, ... so position i holds the same type
    in every key and two keys never compare str against int.
    Ties (leading zeros, case) are broken by the folded string, then the raw
    string, so the order is total and stable.
    """
    text = name or ""
    parts = _DIGITS_RE.split(text)
    key = []
    for i, part in enumerate(parts):
        key.append(int(part) if i % 2 else part.casefold())
    return (tuple(key), text.casefold(), text)


def sort_names(names, descending=False):
    """Return `names` in natural order (helper for tests and callers)."""
    return sorted(names, key=natural_sort_key, reverse=bool(descending))


def validate_workset_rename(old_name, new_name, existing_names, is_unique=None):
    """Validate an inline rename.

    Args:
        old_name: current workset name.
        new_name: raw text typed by the user (trimmed here).
        existing_names: names of every user workset in the document.
        is_unique: optional callable(name) -> bool, e.g. a wrapper around
            `WorksetTable.IsWorksetNameUnique(doc, name)`; it is only called
            once the cheap checks pass.

    Returns:
        (ok, clean_name, message). `message` follows "what · where · next" and
        is empty when ok. An unchanged name returns ok=False with message ""
        so the caller simply leaves edit mode without touching the model.
    """
    clean = (new_name or "").strip()
    where = "Workset '{}'".format(old_name)

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
    for other in existing_names or ():
        if other is None or other == old_name:
            continue
        if other.casefold() == folded and folded != old_folded:
            return False, clean, (
                "{}: a workset named '{}' already exists. "
                "Choose another name or press Esc to cancel.".format(where, other))

    if is_unique is not None and folded != old_folded:
        try:
            unique = bool(is_unique(clean))
        except Exception:
            unique = True       # the API check is a backstop, not the only one
        if not unique:
            return False, clean, (
                "{}: Revit reports that '{}' is already used. "
                "Choose another name or press Esc to cancel.".format(where, clean))

    return True, clean, ""
