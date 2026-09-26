# -*- coding: utf-8 -*-
"""
_project_params.py
==================
Helper Revit API cho **Project / Shared Parameter** gắn vào project
(`doc.ParameterBindings`). Không dính gì tới WPF để tool khác dùng lại được.

Nhận diện parameter bằng `Definition.Id` chứ KHÔNG bằng tên: Revit cho phép hai
parameter trùng tên (một shared + một project, hoặc hai shared khác GUID). Dò
theo tên thì xoá nhầm cái nào tìm thấy trước.

Part of T3Lab Extension.
"""

from Snippets._compat import eid_value


def definition_key(definition):
    """Khoá ổn định của một Definition trong phiên hiện tại (int Id), hoặc None."""
    try:
        return eid_value(definition.Id)
    except Exception:
        return None


def _default_transaction(doc, name):
    from Autodesk.Revit.DB import Transaction
    return Transaction(doc, name)


def _short_error(exc):
    text = (str(exc) or exc.__class__.__name__).strip().splitlines()
    return text[0] if text else "Unknown error"


def delete_project_parameters(doc, definition_keys, transaction_factory=None):
    """Gỡ nhiều parameter khỏi project trong MỘT transaction — Ctrl+Z hoàn tác tất cả.

    `definition_keys`: iterable khoá lấy từ `definition_key()`.

    Trả về `(deleted, failed)`:
      * deleted — list tên đã gỡ
      * failed  — list `(tên hoặc khoá, lý do)`; gồm cả khoá không còn trong model

    Lỗi của một parameter không chặn các parameter khác. Không gỡ được cái nào
    thì rollback để không để lại transaction rỗng trong lịch sử Undo.
    """
    wanted = [k for k in (definition_keys or ()) if k is not None]
    deleted, failed = [], []
    if doc is None or not wanted:
        return deleted, failed

    # Gom Definition TRƯỚC, rồi mới Remove: sửa BindingMap trong lúc đang
    # duyệt iterator của chính nó là hành vi không xác định.
    targets = {}
    bindings = doc.ParameterBindings
    iterator = bindings.ForwardIterator()
    while iterator.MoveNext():
        definition = iterator.Key
        key = definition_key(definition)
        if key in wanted and key not in targets:
            targets[key] = definition
    for key in wanted:
        if key not in targets:
            failed.append((str(key), "No longer in the model"))
    if not targets:
        return deleted, failed

    make = transaction_factory or _default_transaction
    count = len(targets)
    transaction = make(doc, "T3Lab: Delete {} Parameter{}".format(
        count, "" if count == 1 else "s"))
    transaction.Start()
    try:
        for key in wanted:
            definition = targets.get(key)
            if definition is None:
                continue
            name = definition.Name
            try:
                if bindings.Remove(definition):
                    deleted.append(name)
                else:
                    failed.append((name, "Revit refused to remove the binding"))
            except Exception as exc:
                failed.append((name, _short_error(exc)))
        if deleted:
            transaction.Commit()
        else:
            transaction.RollBack()
    except Exception:
        try:
            if transaction.HasStarted() and not transaction.HasEnded():
                transaction.RollBack()
        except Exception:
            pass
        raise
    return deleted, failed
