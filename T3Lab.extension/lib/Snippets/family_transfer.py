# -*- coding: utf-8 -*-
"""
Family Transfer — copy loadable families (every type, or only some) from one
open project to another.

Hai nửa tách bạch:
  * plan_transfer()  — Python thuần: quyết định family nào copy / overwrite /
                        bỏ qua và vì sao. Test được không cần Revit
                        (dev/test_family_transfer.py).
  * execute_plan()   — chạy kế hoạch trên Revit, gói trong MỘT TransactionGroup
                        ở project đích để Ctrl+Z gỡ cả lần chuyển bằng một bước.

Hai cơ chế Revit:
  * COPY      ElementTransformUtils.CopyElements với id của các FamilySymbol
              được chọn — mang theo family và đúng những type đó. Type trùng
              tên ở đích được giữ nguyên (UseDestinationTypes).
  * OVERWRITE EditFamily + LoadFamily(đích, IFamilyLoadOptions) — thay định
              nghĩa family ở đích bằng bản nguồn. LoadFamily luôn nạp MỌI type,
              nên sau đó xoá những type mới đến mà người dùng không chọn.

Author: T3Lab
"""

from collections import namedtuple

# Người dùng chọn cách xử lý family đã có trong project đích.
SKIP = "skip"               # để nguyên family ở đích
ADD = "add"                 # chỉ thêm type còn thiếu
OVERWRITE = "overwrite"     # thay bằng bản nguồn
CONFLICT_MODES = (SKIP, ADD, OVERWRITE)

# Kind của một Action.
COPY_KIND = "copy"
OVERWRITE_KIND = "overwrite"
SKIP_KIND = "skip"

Action = namedtuple("Action", "kind family types keep reason")
Result = namedtuple("Result", "family status types detail")


class FamilyInfo(object):
    """Một family nạp được (loadable) trong project nguồn."""

    def __init__(self, name, category, family_id, types, editable=True):
        self.name = name
        self.category = category or u"—"
        self.family_id = family_id
        self.types = types              # {type name: FamilySymbol ElementId}
        self.editable = editable        # EditFamily được không (cần cho overwrite)

    @property
    def type_names(self):
        return sorted(self.types)


# ── Plan (pure Python) ───────────────────────────────────────────────────────

def plan_transfer(selections, target_index, mode):
    """Quyết định việc làm cho từng family được chọn.

    selections   : [(FamilyInfo, [tên type được chọn])]
    target_index : {tên family: set(tên type)} của project đích
    mode         : SKIP / ADD / OVERWRITE — cho family đã có ở đích
    """
    if mode not in CONFLICT_MODES:
        raise ValueError("unknown conflict mode: %r" % (mode,))

    actions = []
    for info, chosen in selections:
        chosen = [t for t in chosen if t in info.types]
        if not chosen:
            actions.append(Action(SKIP_KIND, info, [], set(), u"no types selected"))
            continue

        existing = target_index.get(info.name)
        if existing is None:
            actions.append(Action(COPY_KIND, info, chosen, set(), u""))
            continue

        if mode == SKIP:
            actions.append(Action(SKIP_KIND, info, [], set(),
                                  u"already in the target project"))
        elif mode == ADD:
            missing = [t for t in chosen if t not in existing]
            if missing:
                actions.append(Action(COPY_KIND, info, missing, set(), u""))
            else:
                actions.append(Action(SKIP_KIND, info, [], set(),
                                      u"all selected types already exist"))
        else:
            if not info.editable:
                actions.append(Action(SKIP_KIND, info, [], set(),
                                      u"family cannot be edited, so it cannot be overwritten"))
            else:
                # Giữ type vốn có ở đích (có thể đang được đặt) + type được chọn.
                actions.append(Action(OVERWRITE_KIND, info, chosen,
                                      set(existing) | set(chosen), u""))
    return actions


def summarize(results):
    """{'ok': n, 'skipped': n, 'failed': n, 'types': n} từ danh sách Result."""
    out = {"ok": 0, "skipped": 0, "failed": 0, "types": 0}
    for r in results:
        out[r.status] = out.get(r.status, 0) + 1
        if r.status == "ok":
            out["types"] += r.types
    return out


# ── Revit side ───────────────────────────────────────────────────────────────

def _symbol_name(symbol):
    try:
        from Snippets._compat import elem_name
        return elem_name(symbol)
    except Exception:
        return getattr(symbol, "Name", u"")


def collect_families(doc):
    """[FamilyInfo] cho mọi family loadable (bỏ in-place) trong `doc`."""
    from Autodesk.Revit.DB import FilteredElementCollector, FamilySymbol

    by_family = {}
    for sym in FilteredElementCollector(doc).OfClass(FamilySymbol):
        try:
            fam = sym.Family
        except Exception:
            continue
        if fam is None or fam.IsInPlace:
            continue
        key = fam.Id
        entry = by_family.get(key)
        if entry is None:
            try:
                cat = fam.FamilyCategory.Name if fam.FamilyCategory else None
            except Exception:
                cat = None
            try:
                editable = bool(fam.IsEditable)
            except Exception:
                editable = False
            entry = FamilyInfo(fam.Name, cat, fam.Id, {}, editable)
            by_family[key] = entry
        entry.types[_symbol_name(sym)] = sym.Id

    return sorted(by_family.values(), key=lambda f: (f.category.lower(), f.name.lower()))


def target_index(doc):
    """{tên family: set(tên type)} của project đích."""
    return dict((f.name, set(f.types)) for f in collect_families(doc))


def same_doc(a, b):
    """True when `a` and `b` are the same open document.

    Không dựa riêng vào reference hay Equals: document lấy từ `revit.doc` và
    từ `app.Documents` có thể là hai wrapper khác nhau của cùng một model — so
    sai ở đây là project nguồn lọt vào danh sách đích (copy vào chính nó).
    """
    if a is None or b is None:
        return False
    if a is b:
        return True
    try:
        if a.Equals(b):
            return True
    except Exception:
        pass
    try:
        path_a, path_b = a.PathName or u"", b.PathName or u""
        if path_a or path_b:
            return path_a == path_b
        return a.Title == b.Title
    except Exception:
        return False


class SourceEntry(object):
    """A place families can be copied FROM: an open project, or a loaded link.

    Linked models are read straight from the host through
    RevitLinkInstance.GetLinkDocument() — the file never has to be opened.
    `host` is the project that holds the link (None for an open project); it is
    the natural target, since that is where the user is working.
    """

    def __init__(self, doc, label, host=None):
        self.doc = doc
        self.label = label
        self.host = host

    @property
    def is_link(self):
        return self.host is not None


def _loaded_link_docs(host):
    """Linked documents loaded in `host` (unloaded links have none)."""
    from Autodesk.Revit.DB import FilteredElementCollector, RevitLinkInstance
    docs = []
    for inst in FilteredElementCollector(host).OfClass(RevitLinkInstance):
        try:
            link_doc = inst.GetLinkDocument()
        except Exception:
            link_doc = None
        if link_doc is not None:
            docs.append(link_doc)
    return docs


def list_sources(projects, link_docs_of=None):
    """[SourceEntry]: every open project, then every loaded link of each of them.

    A model linked into two projects (or linked twice) is listed once — Revit
    shares one linked Document per file in a session.
    """
    link_docs_of = link_docs_of or _loaded_link_docs
    entries = [SourceEntry(p, p.Title) for p in projects]
    for host in projects:
        try:
            links = link_docs_of(host)
        except Exception:
            links = []
        for link_doc in links:
            if any(same_doc(e.doc, link_doc) for e in entries):
                continue
            entries.append(SourceEntry(
                link_doc, u"Link: %s  (in %s)" % (link_doc.Title, host.Title), host))
    return entries


def open_projects(app):
    """Các project đang mở (không phải family document, không phải link)."""
    out = []
    for d in app.Documents:
        try:
            if d.IsFamilyDocument or d.IsLinked:
                continue
        except Exception:
            continue
        out.append(d)
    return out


def _handler_classes():
    """Tạo (một lần) các class implement interface .NET.

    Class nằm trong lib/ nên `__namespace__` tĩnh là an toàn: module chỉ được
    import một lần mỗi phiên engine. Tạo lười để import module này không kéo
    theo Revit API (test chạy ngoài Revit).
    """
    global _HANDLERS
    if _HANDLERS is not None:
        return _HANDLERS
    from Autodesk.Revit.DB import (IDuplicateTypeNamesHandler, DuplicateTypeAction,
                                   IFamilyLoadOptions, FamilySource,
                                   IFailuresPreprocessor, FailureProcessingResult,
                                   FailureSeverity)

    class UseDestinationTypes(IDuplicateTypeNamesHandler):
        __namespace__ = "T3Lab.FamilyTransfer"

        def OnDuplicateTypeNamesFound(self, args):
            return DuplicateTypeAction.UseDestinationTypes

    class OverwriteLoadOptions(IFamilyLoadOptions):
        # pythonnet 3: tham số out được trả về trong tuple, theo đúng thứ tự
        # khai báo, sau giá trị trả về của method.
        __namespace__ = "T3Lab.FamilyTransfer"

        def __init__(self, overwrite_values=False):
            self._values = bool(overwrite_values)

        def OnFamilyFound(self, familyInUse, overwriteParameterValues):
            return (True, self._values)

        def OnSharedFamilyFound(self, sharedFamily, familyInUse, source,
                                overwriteParameterValues):
            return (True, FamilySource.Family, self._values)

    class SwallowWarnings(IFailuresPreprocessor):
        __namespace__ = "T3Lab.FamilyTransfer"

        def PreprocessFailures(self, accessor):
            for msg in accessor.GetFailureMessages():
                if msg.GetSeverity() == FailureSeverity.Warning:
                    accessor.DeleteWarning(msg)
            return FailureProcessingResult.Continue

    _HANDLERS = (UseDestinationTypes, OverwriteLoadOptions, SwallowWarnings)
    return _HANDLERS


_HANDLERS = None


def _silence_warnings(transaction):
    _, _, swallow = _handler_classes()
    opts = transaction.GetFailureHandlingOptions()
    opts.SetFailuresPreprocessor(swallow())
    transaction.SetFailureHandlingOptions(opts)


def _rollback(txn):
    try:
        if txn.HasStarted() and not txn.HasEnded():
            txn.RollBack()
    except Exception:
        pass


def _error_text(exc):
    msg = getattr(exc, "Message", None) or str(exc) or exc.__class__.__name__
    return msg.strip().splitlines()[0]


def _overwrite_family(src_doc, dst_doc, action, overwrite_values):
    """EditFamily + LoadFamily vào đích, rồi xoá type thừa. Trả về số type được chọn."""
    from Autodesk.Revit.DB import Transaction, ElementId
    from Snippets._compat import net_list

    _, load_options, _ = _handler_classes()
    family = src_doc.GetElement(action.family.family_id)
    fam_doc = src_doc.EditFamily(family)
    try:
        loaded = fam_doc.LoadFamily(dst_doc, load_options(overwrite_values))
    finally:
        fam_doc.Close(False)
    if loaded is None:
        raise RuntimeError(u"Revit did not load the family")

    # LoadFamily nạp mọi type của bản nguồn; bỏ những type người dùng không chọn
    # (và vốn không có ở đích). Không bao giờ xoá hết: family phải còn một type.
    ids = list(loaded.GetFamilySymbolIds())
    extra = [i for i in ids if _symbol_name(dst_doc.GetElement(i)) not in action.keep]
    if extra and len(extra) < len(ids):
        t = Transaction(dst_doc, u"Remove unselected types")
        t.Start()
        try:
            _silence_warnings(t)
            dst_doc.Delete(net_list(ElementId, extra))
            t.Commit()
        except Exception:
            _rollback(t)
            raise
    return len(action.types)


def execute_plan(src_doc, dst_doc, actions, overwrite_values=False, step=None):
    """Chạy kế hoạch vào `dst_doc` thành MỘT bước undo. Trả về [Result].

    step(index, total, label) được gọi trước mỗi family; trả False để dừng —
    những family chưa làm được báo là skipped ("stopped").
    """
    from Autodesk.Revit.DB import (TransactionGroup, Transaction, SubTransaction,
                                   ElementTransformUtils, CopyPasteOptions,
                                   Transform, ElementId)
    from Snippets._compat import net_list

    results = [Result(a.family.name, "skipped", 0, a.reason)
               for a in actions if a.kind == SKIP_KIND]
    copies = [a for a in actions if a.kind == COPY_KIND]
    overwrites = [a for a in actions if a.kind == OVERWRITE_KIND]
    total = len(copies) + len(overwrites)
    if not total:
        return results

    done = [0]

    def advance(label):
        done[0] += 1
        if step is None:
            return True
        return step(done[0], total, label) is not False

    use_destination, _, _ = _handler_classes()
    group = TransactionGroup(dst_doc, u"T3Lab: Transfer Families")
    group.Start()
    try:
        pending = []
        if copies:
            t = Transaction(dst_doc, u"Copy families")
            t.Start()
            try:
                _silence_warnings(t)
                options = CopyPasteOptions()
                options.SetDuplicateTypeNamesHandler(use_destination())
                for i, action in enumerate(copies):
                    if not advance(action.family.name):
                        pending = copies[i:]
                        break
                    ids = net_list(ElementId, [action.family.types[n] for n in action.types])
                    sub = SubTransaction(dst_doc)
                    sub.Start()
                    try:
                        ElementTransformUtils.CopyElements(
                            src_doc, ids, dst_doc, Transform.Identity, options)
                        sub.Commit()
                        results.append(Result(action.family.name, "ok",
                                              len(action.types), u"copied"))
                    except Exception as exc:
                        _rollback(sub)
                        results.append(Result(action.family.name, "failed", 0,
                                              _error_text(exc)))
                t.Commit()
            except Exception:
                _rollback(t)
                raise

        if not pending:
            for i, action in enumerate(overwrites):
                if not advance(action.family.name):
                    pending = overwrites[i:]
                    break
                try:
                    n = _overwrite_family(src_doc, dst_doc, action, overwrite_values)
                    results.append(Result(action.family.name, "ok", n, u"overwritten"))
                except Exception as exc:
                    results.append(Result(action.family.name, "failed", 0,
                                          _error_text(exc)))
        else:
            pending = pending + overwrites

        for action in pending:
            results.append(Result(action.family.name, "skipped", 0, u"stopped"))
        group.Assimilate()
    except Exception:
        _rollback(group)
        raise
    return results
