# -*- coding: utf-8 -*-
"""Element explorer — thu thập và gom nhóm phần tử thành cây có đếm.

Tầng LOGIC REVIT thuần của ManaSelect Explore: không import WPF, không biết gì
về cửa sổ. UI chỉ gọi `collect()` -> `build_tree()` rồi dựng TreeViewItem.

Cây ra giống Ideate Explorer: một node gốc mang tổng số, dưới nó là các bậc gom
nhóm (mặc định Category -> Family -> Type), mỗi node kèm số phần tử. Node lá giữ
danh sách ElementId thật nên UI chọn/zoom/isolate được ngay mà không phải hỏi
lại Revit.

Author: T3Lab
"""

import clr

clr.AddReference('RevitAPI')

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    CategoryType,
    ElementId,
    FilteredElementCollector,
    ImportInstance,
    RevitLinkInstance,
)
from System.Collections.Generic import List

# -- Scope (ô "Display" trên UI) -------------------------------------------
SCOPE_VIEW = 'Active View'
SCOPE_MODEL = 'Entire Model'
SCOPE_SELECTION = 'Current Selection'
SCOPES = (SCOPE_VIEW, SCOPE_MODEL, SCOPE_SELECTION)

# -- Sort by (bậc gom nhóm) ------------------------------------------------
GROUP_CATEGORY = 'Category'
GROUP_FAMILY = 'Family'
GROUP_TYPE = 'Type'
GROUP_WORKSET = 'Workset'
GROUP_LEVEL = 'Level'
GROUP_PHASE = 'Phase Created'
GROUP_OPTION = 'Design Option'

GROUPINGS = {
    GROUP_CATEGORY: ('category', 'family', 'type_name'),
    GROUP_FAMILY: ('family', 'type_name'),
    GROUP_TYPE: ('type_name',),
    GROUP_WORKSET: ('workset', 'category', 'type_name'),
    GROUP_LEVEL: ('level', 'category', 'type_name'),
    GROUP_PHASE: ('phase', 'category', 'type_name'),
    GROUP_OPTION: ('design_option', 'category', 'type_name'),
}
GROUP_ORDER = (GROUP_CATEGORY, GROUP_FAMILY, GROUP_TYPE, GROUP_WORKSET,
               GROUP_LEVEL, GROUP_PHASE, GROUP_OPTION)

# -- Filter (ô "Filter" trên UI) -------------------------------------------
FILTER_NONE = '<None>'
FILTER_MODEL = 'Model Elements'
FILTER_ANNOTATION = 'Annotation Elements'
FILTER_INPLACE = 'In-Place Families'
FILTER_GROUPED = 'Grouped Elements'
FILTER_IMPORTS = 'Imports & Links'
FILTER_PINNED = 'Pinned Elements'
FILTER_WARNINGS = 'Elements with Warnings'
FILTER_ORDER = (FILTER_NONE, FILTER_MODEL, FILTER_ANNOTATION, FILTER_INPLACE,
                FILTER_GROUPED, FILTER_IMPORTS, FILTER_PINNED, FILTER_WARNINGS)

# Node lá liệt kê từng instance -- trần để một node không dựng hàng nghìn
# TreeViewItem. Vượt trần thì thêm một node báo còn bao nhiêu chưa hiện.
INSTANCE_CAP = 500

NONE_LABEL = '(none)'

# Category không bao giờ có ích trong cây chọn phần tử: chúng là view/sheet
# hoặc phần tử hệ thống mà người dùng không chọn trong view.
_SKIP_CATEGORY_BICS = (
    BuiltInCategory.OST_Views,
    BuiltInCategory.OST_Sheets,
    BuiltInCategory.OST_Viewports,
    BuiltInCategory.OST_Cameras,
    BuiltInCategory.OST_SectionBox,
    BuiltInCategory.OST_ProjectInformation,
    BuiltInCategory.OST_Materials,
)


def eid_int(element_id):
    """Giá trị int của ElementId — Revit 2024+ đổi IntegerValue -> Value."""
    val = getattr(element_id, 'Value', None)
    if val is None:
        val = getattr(element_id, 'IntegerValue', None)
    return int(val)


def to_id_list(ids):
    """List[ElementId] của .NET — Selection.SetElementIds không nhận list Python."""
    return List[ElementId](list(ids))


def _skip_category_ids():
    out = set()
    for bic in _SKIP_CATEGORY_BICS:
        try:
            out.add(int(bic))
        except Exception:
            continue
    return frozenset(out)


_SKIP_CATEGORIES = _skip_category_ids()


# ==========================================================================
# RECORD
# ==========================================================================
class ElementRecord(object):
    """Một phần tử trong cây.

    Category/Family/Type đọc ngay (rẻ, chỉ là lookup trong doc). Workset /
    Level / Phase / Design Option đọc LƯỜI: mỗi cái là một `get_Parameter`,
    nhân với 8000 phần tử là vài giây chờ mà phần lớn lần dùng không cần tới.
    """

    __slots__ = ('element', 'doc', 'id', 'id_int', 'category', 'family',
                 'type_name', 'label', '_lazy')

    def __init__(self, element, doc, category_name, family_name, type_name):
        self.element = element
        self.doc = doc
        self.id = element.Id
        self.id_int = eid_int(element.Id)
        self.category = category_name
        self.family = family_name
        self.type_name = type_name
        self.label = self._mark_or_name()
        self._lazy = {}

    # -- keys đọc lười ----------------------------------------------------
    def key(self, name):
        """Giá trị của một bậc gom nhóm, không bao giờ trả chuỗi rỗng."""
        if name in ('category', 'family', 'type_name'):
            return getattr(self, name) or NONE_LABEL
        if name not in self._lazy:
            try:
                value = getattr(self, '_read_' + name)()
            except Exception:
                value = None
            self._lazy[name] = value or NONE_LABEL
        return self._lazy[name]

    @property
    def workset(self):
        return self.key('workset')

    @property
    def level(self):
        return self.key('level')

    @property
    def phase(self):
        return self.key('phase')

    @property
    def design_option(self):
        return self.key('design_option')

    # -- đọc thật ---------------------------------------------------------
    def _mark_or_name(self):
        try:
            mark = self.element.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
            if mark is not None:
                text = mark.AsString()
                if text:
                    return text
        except Exception:
            pass
        try:
            name = self.element.Name
            if name:
                return name
        except Exception:
            pass
        return 'Id %d' % self.id_int

    def _read_workset(self):
        try:
            table = self.doc.GetWorksetTable()
            workset = table.GetWorkset(self.element.WorksetId)
            return workset.Name if workset is not None else None
        except Exception:
            return None

    def _read_level(self):
        for param_id in (BuiltInParameter.FAMILY_LEVEL_PARAM,
                         BuiltInParameter.SCHEDULE_LEVEL_PARAM,
                         BuiltInParameter.FAMILY_BASE_LEVEL_PARAM,
                         BuiltInParameter.LEVEL_PARAM):
            try:
                param = self.element.get_Parameter(param_id)
                if param is None:
                    continue
                level_id = param.AsElementId()
                if level_id is None or level_id == ElementId.InvalidElementId:
                    continue
                level = self.doc.GetElement(level_id)
                if level is not None:
                    return level.Name
            except Exception:
                continue
        try:
            level_id = self.element.LevelId
            if level_id is not None and level_id != ElementId.InvalidElementId:
                level = self.doc.GetElement(level_id)
                if level is not None:
                    return level.Name
        except Exception:
            pass
        return None

    def _read_phase(self):
        try:
            param = self.element.get_Parameter(BuiltInParameter.PHASE_CREATED)
            if param is not None:
                phase_id = param.AsElementId()
                if phase_id is not None and phase_id != ElementId.InvalidElementId:
                    phase = self.doc.GetElement(phase_id)
                    if phase is not None:
                        return phase.Name
        except Exception:
            pass
        return None

    def _read_design_option(self):
        try:
            option = self.element.DesignOption
            if option is not None:
                return option.Name
        except Exception:
            pass
        return 'Main Model'


# ==========================================================================
# NODE
# ==========================================================================
class Node(object):
    """Một node của cây. `records` chỉ có ở node lá; node cha gộp từ con."""

    __slots__ = ('label', 'kind', 'children', 'records', 'note')

    def __init__(self, label, kind='group'):
        self.label = label
        self.kind = kind            # 'root' | 'group' | 'leaf' | 'element' | 'note'
        self.children = []
        self.records = []
        self.note = None

    @property
    def count(self):
        if self.records:
            return len(self.records)
        return sum(child.count for child in self.children)

    def ids(self):
        """Mọi ElementId dưới node này, không trùng."""
        seen = set()
        out = []
        for record in self.walk_records():
            if record.id_int not in seen:
                seen.add(record.id_int)
                out.append(record.id)
        return out

    def walk_records(self):
        for record in self.records:
            yield record
        for child in self.children:
            for record in child.walk_records():
                yield record

    def walk(self):
        yield self
        for child in self.children:
            for node in child.walk():
                yield node


# ==========================================================================
# COLLECT
# ==========================================================================
def _category_of(element):
    try:
        category = element.Category
        if category is None:
            return None, None
        return category.Name, category
    except Exception:
        return None, None


def _family_and_type(element, doc, type_cache):
    """(family, type) của một phần tử — cache theo type id vì cả nghìn instance
    dùng chung một type, đọc lại mỗi lần là lãng phí."""
    try:
        type_id = element.GetTypeId()
    except Exception:
        type_id = None

    if type_id is None or type_id == ElementId.InvalidElementId:
        try:
            return 'System', element.Name or 'Unknown'
        except Exception:
            return 'System', 'Unknown'

    key = eid_int(type_id)
    cached = type_cache.get(key)
    if cached is not None:
        return cached

    family = 'System'
    type_name = 'Unknown'
    try:
        element_type = doc.GetElement(type_id)
        if element_type is not None:
            try:
                type_name = element_type.Name or 'Unknown'
            except Exception:
                pass
            try:
                if getattr(element_type, 'Family', None) is not None:
                    family = element_type.Family.Name or 'System'
                else:
                    param = element_type.get_Parameter(
                        BuiltInParameter.ALL_MODEL_FAMILY_NAME)
                    if param is not None and param.AsString():
                        family = param.AsString()
            except Exception:
                pass
    except Exception:
        pass

    type_cache[key] = (family, type_name)
    return family, type_name


def _raw_elements(doc, uidoc, scope):
    """Phần tử thô theo scope. Một collector cho cả model thay vì lặp theo
    từng BuiltInCategory — nhanh hơn và không bỏ sót category nào."""
    if scope == SCOPE_SELECTION:
        if uidoc is None:
            return []
        out = []
        for element_id in uidoc.Selection.GetElementIds():
            element = doc.GetElement(element_id)
            if element is not None:
                out.append(element)
        return out

    if scope == SCOPE_VIEW:
        view = doc.ActiveView
        if view is None:
            return []
        collector = FilteredElementCollector(doc, view.Id)
    else:
        collector = FilteredElementCollector(doc)

    return list(collector.WhereElementIsNotElementType())


def warning_element_ids(doc):
    """int id của mọi phần tử đang bị Revit cảnh báo."""
    ids = set()
    try:
        warnings = doc.GetWarnings()
    except Exception:
        return ids
    for warning in warnings or []:
        for getter in ('GetFailingElements', 'GetAdditionalElements'):
            try:
                for element_id in getattr(warning, getter)() or []:
                    ids.add(eid_int(element_id))
            except Exception:
                continue
    return ids


def _passes_filter(element, filter_name, category, warning_ids):
    if filter_name in (None, FILTER_NONE):
        return True

    if filter_name == FILTER_MODEL:
        try:
            return category.CategoryType == CategoryType.Model
        except Exception:
            return False

    if filter_name == FILTER_ANNOTATION:
        try:
            return category.CategoryType == CategoryType.Annotation
        except Exception:
            return False

    if filter_name == FILTER_INPLACE:
        try:
            symbol = getattr(element, 'Symbol', None)
            return (symbol is not None and symbol.Family is not None
                    and symbol.Family.IsInPlace)
        except Exception:
            return False

    if filter_name == FILTER_GROUPED:
        try:
            group_id = element.GroupId
            return group_id is not None and group_id != ElementId.InvalidElementId
        except Exception:
            return False

    if filter_name == FILTER_IMPORTS:
        return isinstance(element, (ImportInstance, RevitLinkInstance))

    if filter_name == FILTER_PINNED:
        try:
            return bool(element.Pinned)
        except Exception:
            return False

    if filter_name == FILTER_WARNINGS:
        return eid_int(element.Id) in warning_ids

    return True


def collect(doc, uidoc=None, scope=SCOPE_VIEW, filter_name=FILTER_NONE):
    """[ElementRecord] theo scope + filter. Phần tử không có Category bị bỏ:
    chúng là phần tử hệ thống, không chọn được trong view."""
    warning_ids = (warning_element_ids(doc)
                   if filter_name == FILTER_WARNINGS else frozenset())
    type_cache = {}
    records = []

    for element in _raw_elements(doc, uidoc, scope):
        category_name, category = _category_of(element)
        if not category_name:
            continue
        try:
            if eid_int(category.Id) in _SKIP_CATEGORIES:
                continue
        except Exception:
            pass
        if not _passes_filter(element, filter_name, category, warning_ids):
            continue
        try:
            family, type_name = _family_and_type(element, doc, type_cache)
            records.append(ElementRecord(element, doc, category_name,
                                         family, type_name))
        except Exception:
            continue

    return records


# ==========================================================================
# TREE
# ==========================================================================
def _matches(record, needle, levels):
    if not needle:
        return True
    for name in levels:
        if needle in record.key(name).lower():
            return True
    return needle in record.label.lower()


def build_tree(records, group_by=GROUP_CATEGORY, search='', root_label=None,
               with_instances=True):
    """Node gốc của cây gom nhóm.

    `search` lọc theo MỌI bậc gom nhóm cộng nhãn instance, nên gõ tên type ra
    đúng type, gõ tên category ra cả nhánh category.
    """
    levels = GROUPINGS.get(group_by, GROUPINGS[GROUP_CATEGORY])
    needle = (search or '').strip().lower()

    kept = [r for r in records if _matches(r, needle, levels)]

    root = Node(root_label or 'All elements', kind='root')
    buckets = {}                    # tuple khoá -> Node

    for record in kept:
        parent = root
        path = ()
        for depth, name in enumerate(levels):
            path = path + (record.key(name),)
            node = buckets.get(path)
            if node is None:
                node = Node(path[-1],
                            kind='leaf' if depth == len(levels) - 1 else 'group')
                buckets[path] = node
                parent.children.append(node)
            parent = node
        parent.records.append(record)

    _sort(root)
    if with_instances:
        _attach_instances(root)
    return root


def _sort(node):
    node.children.sort(key=lambda child: child.label.lower())
    for child in node.children:
        _sort(child)


def _attach_instances(root):
    """Treo từng instance làm con của node lá, trong hạn INSTANCE_CAP."""
    for leaf in list(root.walk()):
        if not leaf.records or leaf.children:
            continue
        shown = sorted(leaf.records[:INSTANCE_CAP],
                       key=lambda r: r.label.lower())
        for record in shown:
            child = Node(record.label, kind='element')
            child.records.append(record)
            leaf.children.append(child)
        hidden = len(leaf.records) - len(shown)
        if hidden > 0:
            note = Node('%d more not listed here — Select still takes all %d'
                        % (hidden, len(leaf.records)), kind='note')
            leaf.children.append(note)
            leaf.note = note


# ==========================================================================
# WARNINGS
# ==========================================================================
def build_warning_tree(doc, search=''):
    """Cây cảnh báo: loại cảnh báo -> từng lần xảy ra -> phần tử liên quan.

    Trả (root, total) — total là số cảnh báo thật, khác `root.count` (đếm phần
    tử) vì một cảnh báo có thể chạm nhiều phần tử.
    """
    needle = (search or '').strip().lower()
    root = Node('All warnings', kind='root')
    try:
        warnings = doc.GetWarnings() or []
    except Exception:
        return root, 0

    by_text = {}
    total = 0
    type_cache = {}

    for warning in warnings:
        try:
            text = warning.GetDescriptionText() or 'Unnamed warning'
        except Exception:
            text = 'Unnamed warning'

        element_ids = []
        for getter in ('GetFailingElements', 'GetAdditionalElements'):
            try:
                for element_id in getattr(warning, getter)() or []:
                    element_ids.append(element_id)
            except Exception:
                continue

        records = []
        for element_id in element_ids:
            try:
                element = doc.GetElement(element_id)
                if element is None:
                    continue
                category_name, _cat = _category_of(element)
                family, type_name = _family_and_type(element, doc, type_cache)
                records.append(ElementRecord(element, doc,
                                             category_name or NONE_LABEL,
                                             family, type_name))
            except Exception:
                continue

        if needle and needle not in text.lower():
            if not any(needle in r.label.lower()
                       or needle in r.category.lower()
                       or needle in r.type_name.lower() for r in records):
                continue

        total += 1
        group = by_text.get(text)
        if group is None:
            group = Node(text, kind='group')
            by_text[text] = group
            root.children.append(group)

        occurrence = Node('Warning %d - %d element(s)'
                          % (len(group.children) + 1, len(records)),
                          kind='leaf')
        for record in records:
            child = Node('%s / %s [Id %d]'
                         % (record.category, record.type_name, record.id_int),
                         kind='element')
            child.records.append(record)
            occurrence.children.append(child)
        group.children.append(occurrence)

    root.children.sort(key=lambda child: (-len(child.children),
                                          child.label.lower()))
    return root, total


# ==========================================================================
# EXPORT
# ==========================================================================
def tree_rows(root):
    """Cây -> [(depth, label, count)] để ghi CSV, thứ tự đúng như đang hiện."""
    rows = []

    def walk(node, depth):
        if node.kind != 'note':
            rows.append((depth, node.label, node.count))
        for child in node.children:
            walk(child, depth + 1)

    walk(root, 0)
    return rows
