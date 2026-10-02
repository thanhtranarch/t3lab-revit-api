# -*- coding: utf-8 -*-
"""Select-Similar predicates, used by the MCP server / Assistant tools.

The rules for "what counts as similar" (which categories have no meaningful
Type and must fall back to Category matching, how to read an element's
BuiltInCategory across Revit versions, how to find its Family) used to be
duplicated in `lib/Selection/dqt_select/core.py`, which ManaSelect's old
Select Similar mode used. That mode and module were removed on 2026-10-02
(Explore + Revit's own "Select All Instances" cover it), so this file is now
the only copy.

These three predicates are the whole reusable part, and they are pure — they
take an element and return a value, touch no UI and no ambient document.

Author: Tran Tien Thanh
"""
from __future__ import unicode_literals

from Autodesk.Revit.DB import BuiltInCategory, FamilyInstance

try:
    from Snippets._compat import eid_value
except (ImportError, ValueError):      # running from inside the Snippets package
    from ._compat import eid_value


# Categories whose elements have no meaningful "Type", so "select similar by
# type" must fall back to matching by Category instead.
CATEGORY_ONLY_BICS = set()
for _member in ('OST_Lines', 'OST_SketchLines', 'OST_CLines', 'OST_Grids',
                'OST_Levels', 'OST_Rooms', 'OST_Areas', 'OST_MEPSpaces',
                'OST_RoomSeparationLines', 'OST_AreaSchemeLines',
                'OST_SectionBox', 'OST_VolumeOfInterest', 'OST_RvtLinks',
                'OST_Cameras', 'OST_IOSModelGroups'):
    # getattr, not attribute access: BuiltInCategory members come and go
    # between Revit releases and one missing name must not kill the module.
    _bic = getattr(BuiltInCategory, _member, None)
    if _bic is not None:
        CATEGORY_ONLY_BICS.add(_bic)
del _member, _bic


def bic_of(element):
    """Safe BuiltInCategory of an element, or None."""
    try:
        cat = element.Category
        if cat is None:
            return None
        # Revit 2023+: Category.BuiltInCategory ; older: derive from the Id.
        try:
            return cat.BuiltInCategory
        except AttributeError:
            try:
                return BuiltInCategory(eid_value(cat.Id))
            except Exception:
                return None
    except Exception:
        return None


def is_category_only(element):
    """True if this element should be matched by Category rather than Type."""
    bic = bic_of(element)
    if bic is None:
        # No category -> sketch elements, lines, etc. Match by python class.
        return True
    return bic in CATEGORY_ONLY_BICS


def family_id_of(element, doc):
    """Return the Family ElementId of an element, or None.

    Works for FamilyInstance (loadable & in-place) and for annotation types
    that expose a Family. System families (Walls, Floors, ...) have no usable
    Family, so this returns None and the caller falls back to category matching.
    """
    try:
        if isinstance(element, FamilyInstance):
            sym = element.Symbol
            if sym is not None and sym.Family is not None:
                return sym.Family.Id
    except Exception:
        pass

    try:
        type_id = element.GetTypeId()
        if type_id is not None and eid_value(type_id) != -1:
            etype = doc.GetElement(type_id)
            fam = getattr(etype, 'Family', None)
            if fam is not None:
                return fam.Id
    except Exception:
        pass

    return None
