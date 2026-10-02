# -*- coding: utf-8 -*-
"""
BatchLinkWorksets.py
====================
Row objects and pending-change state for the **Link Workset** tab of Batch Link.

The user picks a workset per link right in the grid (or stages one for every
checked link from the bulk editor under it). Nothing reaches Revit until Apply:
a staged row is *pending* (yellow), after a successful Apply it is *applied*
(green) and a row Revit refused is *failed* (red) and keeps its pending change
so the next Apply retries it.

Pure Python on purpose -- no clr, no Revit API -- so ``dev/test_batch_link.py``
exercises the very code the dialog runs. The Revit write itself is handed in as
a callable (see :func:`apply_pending`).

Every value the XAML reads is a STRING: PythonNet does not carry bools / ints
through a binding reliably, strings it does (same as ``Severity``). The cell
highlight is a DataTrigger on :attr:`LinkWorksetRow.row_state`.

Part of T3Lab Extension.
"""

STATE_CLEAN = ""
STATE_PENDING = "pending"
STATE_APPLIED = "applied"
STATE_FAILED = "failed"

# The status pill (T3.StatusPill) truncates long text; keep "Failed: ..." short
# in the cell, the full reason goes into the tooltip.
STATUS_MAX = 26


class _Row(object):
    """Common check/status plumbing for every grid row in Batch Link."""

    def __init__(self, status_text="Ready", severity="Success",
                 is_selected=False, is_enabled=True):
        self._status_text = status_text
        self._severity = severity
        self._is_selected = bool(is_selected)
        self._is_enabled = bool(is_enabled)

    @property
    def StatusText(self):
        return self._status_text

    @StatusText.setter
    def StatusText(self, value):
        self._status_text = value

    @property
    def Severity(self):
        return self._severity

    @Severity.setter
    def Severity(self, value):
        self._severity = value

    @property
    def IsSelected(self):
        return self._is_selected

    @IsSelected.setter
    def IsSelected(self, value):
        self._is_selected = bool(value)

    @property
    def IsEnabled(self):
        return self._is_enabled

    @IsEnabled.setter
    def IsEnabled(self, value):
        self._is_enabled = bool(value)


def _short(text, limit=STATUS_MAX):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + u"…"


class LinkWorksetRow(_Row):
    """One Revit link, seen as an element sitting on a workset of THIS model.

    ``current_*`` is where the link is in the model right now; ``pending_*`` is
    what the user picked and has not applied yet (``pending_id`` None = nothing
    staged). ``note`` / ``note_severity`` describe the row when nothing is
    staged ("Ready", "Unloaded", "Owned by ..."); ``lock_reason`` is set when the
    row cannot move at all and is what its tooltip says.
    """

    def __init__(self, record, current_label, current_id, can_move, note,
                 severity="Success", lock_reason=""):
        _Row.__init__(self, note, severity, is_selected=False, is_enabled=can_move)
        self.record = record
        self.current_label = current_label or ""
        self.current_id = current_id
        self.note = note
        self.note_severity = severity
        self.lock_reason = lock_reason or ""
        self.pending_id = None
        self.pending_name = ""
        self.row_state = STATE_CLEAN
        self.result_message = ""
        self._before = None      # (current_id, current_label, pending_id, pending_name) at Apply
        self._sync_status()

    # ── what the grid reads ─────────────────────────────────────────────────

    @property
    def LinkName(self):
        return getattr(self.record, 'name', "") or ""

    @property
    def InstanceCount(self):
        count = int(getattr(self.record, 'instance_count', 0) or 0)
        return str(count) if count else u"—"

    @property
    def WorksetName(self):
        """The workset shown in the WORKSET cell: the staged one, else the real one."""
        if self.pending_id is not None:
            return self.pending_name
        return self.current_label or u"—"

    @property
    def WorksetEditable(self):
        """"yes" shows the inline ComboBox, "no" the plain text (DataTrigger)."""
        return "yes" if self.IsEnabled else "no"

    @property
    def WorksetTip(self):
        if not self.IsEnabled:
            return self.lock_reason or "This link cannot be moved."
        current = self.current_label or "no workset"
        if self.row_state == STATE_PENDING:
            return u"Pending: {} → {}. Press Apply to move it.".format(
                current, self.pending_name)
        if self.row_state == STATE_FAILED:
            return u"Failed: {}. Still on {}; Apply retries the move to {}.".format(
                self.result_message or "Revit refused the change", current,
                self.pending_name)
        if self.row_state == STATE_APPLIED:
            return u"{} — now on {}.".format(self.result_message or "Moved", current)
        return u"On {}. Pick another workset to stage a move.".format(current)

    # ── state changes ───────────────────────────────────────────────────────

    def _sync_status(self):
        if self.row_state == STATE_PENDING:
            self.StatusText, self.Severity = "Pending", "Warning"
        elif self.row_state == STATE_FAILED:
            self.StatusText = _short("Failed: " + (self.result_message or "error"))
            self.Severity = "Danger"
        elif self.row_state == STATE_APPLIED:
            self.StatusText = self.result_message or "Moved"
            self.Severity = "Success"
        else:
            self.StatusText, self.Severity = self.note, self.note_severity

    def stage(self, workset_id, workset_name):
        """Stage a move to `workset_id`. Returns True when the row changed.

        Picking the workset the link already sits on drops the pending change
        (the cell goes back to normal). Rows that cannot move ignore it.
        """
        if not self.IsEnabled or workset_id is None:
            return False
        if workset_id == self.current_id:
            # Grid regeneration re-selects the shown value and lands here: with
            # nothing staged that must NOT wipe a green "Moved" result.
            if self.pending_id is None:
                return False
            return self.unstage()
        if self.pending_id == workset_id:
            return False         # same pick again (or a failed row re-shown)
        self.pending_id = workset_id
        self.pending_name = workset_name or ""
        self.row_state = STATE_PENDING
        self.result_message = ""
        self._sync_status()
        return True

    def unstage(self):
        """Forget the pending change. Returns True when there was one (or a result)."""
        if self.pending_id is None and self.row_state == STATE_CLEAN:
            return False
        self.pending_id = None
        self.pending_name = ""
        self.row_state = STATE_CLEAN
        self.result_message = ""
        self._sync_status()
        return True

    def mark_applied(self, message="Moved"):
        """Revit took the change: the pending workset is now the real one."""
        if self.pending_id is not None:
            self.current_id = self.pending_id
            self.current_label = self.pending_name
        self.pending_id = None
        self.pending_name = ""
        self.row_state = STATE_APPLIED
        self.result_message = message or "Moved"
        self._sync_status()

    def mark_failed(self, message):
        """Revit refused: keep the pending change (Apply retries), show why."""
        self.row_state = STATE_FAILED
        self.result_message = (message or "Revit refused the change").split("\n")[0]
        self._sync_status()

    def clear_result(self):
        """Drop an applied (green) highlight. Pending / failed rows are left alone."""
        if self.row_state == STATE_APPLIED:
            self.row_state = STATE_CLEAN
            self.result_message = ""
            self._sync_status()
            return True
        return False

    def set_current(self, label, workset_id):
        """Re-read from Revit. A pending pick equal to the new place is dropped."""
        self.current_label = label or ""
        self.current_id = workset_id
        if self.pending_id is not None and self.pending_id == workset_id:
            self.pending_id = None
            self.pending_name = ""
            if self.row_state != STATE_APPLIED:
                self.row_state = STATE_CLEAN
            self._sync_status()


# ── collection helpers ──────────────────────────────────────────────────────

def pending_rows(rows):
    """Rows with a staged, unapplied change (failed rows included)."""
    return [r for r in (rows or []) if r.IsEnabled and r.pending_id is not None]


def pending_count(rows):
    return len(pending_rows(rows))


def clear_results(rows):
    """Drop every green (applied) highlight. Returns how many were cleared."""
    return sum(1 for r in (rows or []) if r.clear_result())


def stage_checked(rows, workset_id, workset_name):
    """Bulk editor: stage `workset_id` on every checked, movable row.

    Returns (staged, unchanged): rows that now carry a pending change vs rows
    already sitting on that workset (their pending change, if any, is dropped).
    """
    staged = 0
    unchanged = 0
    for row in rows or []:
        if not (row.IsSelected and row.IsEnabled):
            continue
        row.stage(workset_id, workset_name)
        if row.pending_id is not None:
            staged += 1
        else:
            unchanged += 1
    return staged, unchanged


def primary_label(count):
    """Footer button text on the Link Workset tab."""
    return "Apply ({})".format(count) if count else "Apply"


def tally_text(rows):
    """"26 links · 26 can move · 0 checked · 3 pending"."""
    rows = rows or []
    movable = sum(1 for r in rows if r.IsEnabled)
    checked = sum(1 for r in rows if r.IsSelected and r.IsEnabled)
    text = u"{} links · {} can move · {} checked".format(len(rows), movable, checked)
    count = pending_count(rows)
    if count:
        text += u" · {} pending".format(count)
    return text


def apply_pending(rows, move_one, step=None):
    """Run `move_one(row)` on every pending row and record the outcome per row.

    `move_one(row)` must return (ok, message) and may raise; an exception is a
    failure of that row only. `step(row, index, total)` is called before each
    row (progress). Returns (moved, already, failed). The caller owns the
    Transaction: one Apply is one Ctrl+Z.
    """
    targets = pending_rows(rows)
    total = len(targets)
    moved = already = failed = 0
    for index, row in enumerate(targets, 1):
        if step is not None:
            step(row, index, total)
        try:
            ok, message = move_one(row)
        except Exception as ex:          # one bad link must not stop the rest
            ok, message = False, str(ex).split("\n")[0] or type(ex).__name__
        if not ok:
            failed += 1
            row.mark_failed(message)
        elif message == "Already there":
            already += 1
            row.mark_applied("Already there")
        else:
            moved += 1
            row.mark_applied("Moved")
    return moved, already, failed


def rollback_all(rows, message):
    """The whole transaction was rolled back: every row that 'succeeded' did not.

    Rows applied in this run go back to their pre-Apply place with their target
    still pending, marked failed with `message`, so the next Apply retries them.
    Rows that had already failed keep their own reason.
    """
    for row in rows or []:
        if row.row_state == STATE_APPLIED and row._before:
            before_id, before_label, target_id, target_name = row._before
            row.current_id, row.current_label = before_id, before_label
            row.pending_id, row.pending_name = target_id, target_name
            row.mark_failed(message)


def snapshot_before(rows):
    """Remember (current, target) of every pending row before an Apply.

    Clears the snapshot of every other row so a green row left over from an
    earlier Apply is never "rolled back" by this one.
    """
    for row in rows or []:
        row._before = None
    for row in pending_rows(rows):
        row._before = (row.current_id, row.current_label, row.pending_id, row.pending_name)


def summary_text(moved, already, failed):
    parts = [u"{} link{} moved".format(moved, "" if moved == 1 else "s")]
    if already:
        parts.append(u"{} already there".format(already))
    parts.append(u"{} failed".format(failed))
    return u" · ".join(parts)
