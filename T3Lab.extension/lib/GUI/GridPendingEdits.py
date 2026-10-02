# -*- coding: utf-8 -*-
"""
GridPendingEdits.py
===================
Staged inline editing for T3 DataGrids.

The user types into a cell, the cell turns amber, and **nothing reaches Revit
until Apply is pressed**. Three pieces, none of which touch the Revit API:

* :func:`column_key` — which column a ``CellEditEnding`` fired for, resolved
  from the column's **binding path**, never from its header text.
* :func:`editor_text` / :func:`revert_editor` — read what was typed, and put the
  old value back when an edit has to be undone.
* :func:`init_pending` and friends — per-row store of what is staged, plus the
  ``dirty_<field>`` flags a column's ``CellStyle`` reads in order to paint the
  amber highlight (through the cell bridge below).

Why the binding path and not the header
---------------------------------------
View Manager and Sheet Manager both dispatched on ``column.Header``::

    if column.Header == "View Name":      # XAML actually says "VIEW NAME"
    if column.Header == "Sheet Number":   # XAML actually says "NUMBER"

No branch ever matched, so every inline edit was silently discarded: the grid
showed the new text because the binding had written it into the row object, but
the model never heard about it, and Sheet Manager's Apply button kept reporting
"No pending changes to apply". A header is display text — the moment somebody
retitles a column, logic keyed to it dies without a sound. The binding path is
the column's actual contract with the row, so that is what is matched here.

How the amber cell reads its flag (cell string bridge)
------------------------------------------------------
Rows are plain Python objects, so pythonnet hands WPF every attribute as a
``PyObject``. WPF turns that into text for a string property (which is why every
column shows its value), but a ``DataTrigger Binding="{Binding dirty_name}"
Value="True"`` compares the PyObject itself with the string "True" and never
matches: the highlight silently never appears. A ``DataGridCell`` has no
template of its own to hold the hidden-TextBlock bridge, so the cell is its own
bridge — the column's ``CellStyle`` copies the flag into a string property of
the cell, :data:`CELL_BRIDGE_PROPERTY`, and a property ``Trigger`` tests that
string::

    <Style TargetType="DataGridCell" BasedOn="{StaticResource T3.DataGridCell}">
      <Setter Property="AutomationProperties.ItemStatus" Value="{Binding dirty_name}"/>
      <Style.Triggers>
        <Trigger Property="AutomationProperties.ItemStatus" Value="True"> ... </Trigger>
      </Style.Triggers>
    </Style>

The columns stay ``DataGridTextColumn`` / ``DataGridComboBoxColumn``, so
:func:`column_key`, :func:`editor_text` and ``CellEditEnding`` see exactly what
they saw before. The flag is a plain attribute (no INotifyPropertyChanged), so
the grid still needs ``Items.Refresh()`` after a stage/unstage to repaint.

Part of T3Lab Extension.
"""

# Attribute that holds a row's staged values. Underscore-prefixed so WPF never
# tries to bind to it.
PENDING_ATTR = "_t3_pending"

# Prefix of the per-column flag a CellStyle reads, e.g. a column bound to
# `sheet_name` is highlighted through `dirty_sheet_name`.
DIRTY_PREFIX = "dirty_"

# String property of the DataGridCell the CellStyle copies `dirty_<field>` into
# (see "cell string bridge" above). Accessibility-wise it is the right slot too:
# "item status" of the cell. dev/test_grid_pending_edits.py holds the XAML to it.
CELL_BRIDGE_PROPERTY = "AutomationProperties.ItemStatus"


# ── COLUMN IDENTITY ──────────────────────────────────────────────────────────

def _binding_path(binding):
    """Path of a WPF Binding, or "" when it has none."""
    if binding is None:
        return ""
    try:
        path = binding.Path
        if path is not None and path.Path:
            return str(path.Path)
    except Exception:
        pass
    return ""


def column_key(column):
    """The row field a DataGrid column edits.

    Tried in order: the column's own Binding (text / check-box columns), the
    SelectedItem or SelectedValue binding (combo-box columns), then
    SortMemberPath, which WPF derives from those same bindings. Returns "" when
    the column is a template column carrying no binding of its own.
    """
    if column is None:
        return ""

    for attribute in ("Binding", "SelectedItemBinding", "SelectedValueBinding"):
        try:
            path = _binding_path(getattr(column, attribute, None))
        except Exception:
            path = ""
        if path:
            return path

    try:
        return str(column.SortMemberPath or "")
    except Exception:
        return ""


# ── EDITING ELEMENT ──────────────────────────────────────────────────────────

def editor_text(editing_element):
    """What the user actually typed or picked, as text.

    A text column hands back a TextBox, a combo-box column a ComboBox whose
    SelectedItem is the real answer and whose Text is the fallback for the
    editable-combo case.
    """
    if editing_element is None:
        return None
    selected = None
    try:
        selected = editing_element.SelectedItem
    except Exception:
        selected = None
    if selected is not None:
        return selected if isinstance(selected, str) else str(selected)
    try:
        return editing_element.Text
    except Exception:
        return None


def revert_editor(editing_element, value):
    """Put `value` back into the editor so the binding commits the old value.

    CellEditEnding runs *before* the binding writes back, so rewriting the
    editor here is what makes a rejected edit disappear instead of leaving the
    grid showing a value the model does not have.
    """
    if editing_element is None:
        return
    # Probe for an EXISTING SelectedItem before assigning one. Assigning first
    # and catching the failure works on a real WPF TextBox, which refuses the
    # unknown member -- but it silently succeeds on any plain object, so the
    # text would never be put back.
    if hasattr(editing_element, "SelectedItem"):
        try:
            editing_element.SelectedItem = value
            return
        except Exception:
            pass
    try:
        editing_element.Text = u"" if value is None else value
    except Exception:
        pass


# ── PER-ROW STAGING ──────────────────────────────────────────────────────────

def init_pending(row, fields):
    """Give `row` an empty staging store and one `dirty_<field>` flag each.

    The flags must exist as real attributes before the grid binds to them:
    a binding to a missing path silently yields nothing, which would leave
    every edit unhighlighted.
    """
    setattr(row, PENDING_ATTR, {})
    for field in fields:
        setattr(row, DIRTY_PREFIX + field, False)


def stage(row, field, value):
    """Record `value` as this row's pending edit for `field`."""
    store = getattr(row, PENDING_ATTR, None)
    if store is None:
        store = {}
        setattr(row, PENDING_ATTR, store)
    store[field] = value
    setattr(row, DIRTY_PREFIX + field, True)


def unstage(row, field):
    """Drop one staged edit and clear its highlight."""
    store = getattr(row, PENDING_ATTR, None)
    if store is not None:
        store.pop(field, None)
    setattr(row, DIRTY_PREFIX + field, False)


def clear_pending(row):
    """Drop every staged edit on `row` and clear all of its highlights."""
    store = getattr(row, PENDING_ATTR, None) or {}
    for field in list(store.keys()):
        setattr(row, DIRTY_PREFIX + field, False)
    store.clear()
    setattr(row, PENDING_ATTR, store)


def pending_of(row):
    """{field: value} staged on `row` — empty dict when it is clean."""
    return dict(getattr(row, PENDING_ATTR, None) or {})


def has_pending(row):
    """True when `row` carries at least one staged edit."""
    return bool(getattr(row, PENDING_ATTR, None))


def pending_rows(rows):
    """Every row in `rows` carrying a staged edit, in order."""
    return [row for row in rows or [] if has_pending(row)]


def pending_count(rows):
    """How many individual cells are staged across `rows`."""
    return sum(len(getattr(row, PENDING_ATTR, None) or {}) for row in rows or [])


def same_text(left, right):
    """True when two cell values mean the same thing.

    Compared as trimmed text: a cell whose editor hands back `"A2-01 "` has not
    really been changed from `"A2-01"`, and staging it would put an amber
    highlight on an edit that does nothing.
    """
    def norm(value):
        if value is None:
            return u""
        text = value if isinstance(value, str) else str(value)
        return text.strip()
    return norm(left) == norm(right)
