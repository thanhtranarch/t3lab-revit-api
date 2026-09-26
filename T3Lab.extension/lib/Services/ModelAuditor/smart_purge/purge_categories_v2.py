# -*- coding: utf-8 -*-
"""
Purge Categories Configuration v2.0
Defines all 28 purge categories organized in 5 groups
Copyright (c) 2025 Dang Quoc Truong (DQT)
"""

__author__ = "Dang Quoc Truong (DQT)"


# Safety levels
SAFETY_SAFE = "SAFE"
SAFETY_WARNING = "WARNING"

# Priority levels
PRIORITY_HIGH = "HIGH"
PRIORITY_MEDIUM = "MEDIUM"
PRIORITY_LOW = "LOW"


class PurgeCategoryItem:
    """Individual item that can be purged"""
    
    def __init__(self, element, name, element_id, item_type="", warning=""):
        self.element = element
        self.name = name
        self.element_id = element_id
        self.item_type = item_type
        self.warning = warning


class PurgeCategory:
    """Purge category definition"""
    
    def __init__(self, id, name, icon, scanner_class, group_id,
                 description="", default_checked=True, 
                 safety_level=SAFETY_SAFE, priority=PRIORITY_HIGH):
        """
        Initialize purge category
        
        Args:
            id: Unique category ID
            name: Display name
            icon: Unicode icon
            scanner_class: Scanner class name (string)
            group_id: Parent group ID
            description: Category description
            default_checked: Default checked state
            safety_level: SAFETY_SAFE or SAFETY_WARNING
            priority: Priority level (HIGH/MEDIUM/LOW)
        """
        self.id = id
        self.name = name
        self.icon = icon
        self.scanner_class = scanner_class
        self.group_id = group_id
        self.description = description
        self.default_checked = default_checked
        self.safety_level = safety_level
        self.priority = priority
        
        # Runtime state
        self.is_checked = default_checked
        self.is_scanned = False
        self.unused_items = []
        self.scan_error = None
    

# ============================================================================
# GROUP 1: ELEMENT TYPES (9 categories)
# ============================================================================

ELEMENT_TYPES_CATEGORIES = [
    {
        "id": "materials",
        "name": "Materials",
        "icon": u"\U0001F3A8",  # 🎨
        "scanner_class": "MaterialScanner",
        "group_id": "element_types",
        "description": "Materials not used by any elements or views",
        "default_checked": True,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_HIGH
    },
    {
        "id": "line_patterns",
        "name": "Line Patterns",
        "icon": u"\u2501\u2501",  # ━━
        "scanner_class": "LinePatternScanner",
        "group_id": "element_types",
        "description": "Line patterns not used by any lines or model edges",
        "default_checked": True,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_HIGH
    },
    {
        "id": "fill_patterns",
        "name": "Fill Patterns",
        "icon": u"\u2593\u2593",  # ▓▓
        "scanner_class": "FillPatternScanner",
        "group_id": "element_types",
        "description": "Fill patterns not used by materials or filled regions",
        "default_checked": True,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_HIGH
    },
    {
        "id": "text_note_types",
        "name": "Text Note Types",
        "icon": u"\U0001F4DD",  # 📝
        "scanner_class": "TextTypeScanner",
        "group_id": "element_types",
        "description": "Text note types not used by any text notes",
        "default_checked": True,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_HIGH
    },
    {
        "id": "dimension_types",
        "name": "Dimension Types",
        "icon": u"\U0001F4CF",  # 📏
        "scanner_class": "DimensionTypeScanner",
        "group_id": "element_types",
        "description": "Dimension types not used by any dimensions",
        "default_checked": True,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_HIGH
    },
    {
        "id": "line_styles",
        "name": "Line Styles",
        "icon": u"\u2500",  # ─
        "scanner_class": "LineStyleScanner",
        "group_id": "element_types",
        "description": "Line styles (subcategories) not used by model or detail lines",
        "default_checked": True,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_HIGH
    },
    {
        "id": "wall_types",
        "name": "Wall Types",
        "icon": u"\U0001F9F1",  # 🧱
        "scanner_class": "WallTypeScanner",
        "group_id": "element_types",
        "description": "Wall types with no instances in the model",
        "default_checked": False,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "floor_types",
        "name": "Floor Types",
        "icon": u"\u25A2",  # ▢
        "scanner_class": "FloorTypeScanner",
        "group_id": "element_types",
        "description": "Floor types with no instances in the model",
        "default_checked": False,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "roof_types",
        "name": "Roof Types",
        "icon": u"\u2302",  # ⌂
        "scanner_class": "RoofTypeScanner",
        "group_id": "element_types",
        "description": "Roof types with no instances in the model",
        "default_checked": False,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_MEDIUM
    }
]


# ============================================================================
# GROUP 2: VIEWS & SHEETS (6 categories)
# ============================================================================

VIEWS_SHEETS_CATEGORIES = [
    {
        "id": "view_templates",
        "name": "View Templates",
        "icon": u"\U0001F4CB",  # 📋
        "scanner_class": "ViewTemplateScanner",
        "group_id": "views_sheets",
        "description": "View templates not applied to any views",
        "default_checked": False,
        "safety_level": SAFETY_WARNING,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "filters",
        "name": "Filters",
        "icon": u"\U0001F50D",  # 🔍
        "scanner_class": "FilterScanner",
        "group_id": "views_sheets",
        "description": "Filters not applied to any views or templates",
        "default_checked": False,
        "safety_level": SAFETY_WARNING,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "empty_sheets",
        "name": "Empty Sheets",
        "icon": u"\U0001F4C4",  # 📄
        "scanner_class": "EmptySheetsScanner",
        "group_id": "views_sheets",
        "description": "Sheets with no views placed on them",
        "default_checked": True,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "unused_schedules",
        "name": "Unused Schedules",
        "icon": u"\U0001F4CA",  # 📊
        "scanner_class": "UnusedSchedulesScanner",
        "group_id": "views_sheets",
        "description": "Schedules not placed on any sheets",
        "default_checked": False,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "legend_views",
        "name": "Legend Views",
        "icon": u"\U0001F5FA",  # 🗺
        "scanner_class": "LegendViewsScanner",
        "group_id": "views_sheets",
        "description": "Legend views not placed on any sheets",
        "default_checked": False,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_LOW
    },
    {
        "id": "temp_working_views",
        "name": "Temp/Working Views",
        "icon": u"\U0001F527",  # 🔧
        "scanner_class": "TempWorkingViewsScanner",
        "group_id": "views_sheets",
        "description": "Views with temporary naming patterns (test_, temp_, working_, _old)",
        "default_checked": False,
        "safety_level": SAFETY_WARNING,
        "priority": PRIORITY_LOW
    }
]


# ============================================================================
# GROUP 3: FAMILIES & TYPES (5 categories)
# ============================================================================

FAMILIES_CATEGORIES = [
    {
        "id": "detail_components",
        "name": "Detail Components",
        "icon": u"\U0001F529",  # 🔩
        "scanner_class": "DetailComponentsScanner",
        "group_id": "families",
        "description": "Detail component families with no instances placed",
        "default_checked": True,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "unused_families",
        "name": "Unused Families",
        "icon": u"\U0001F465",  # 👥
        "scanner_class": "UnusedFamiliesScanner",
        "group_id": "families",
        "description": "Families with no instances placed in the model",
        "default_checked": False,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "unused_family_types",
        "name": "Unused Family Types",
        "icon": u"\U0001F4D0",  # 📐
        "scanner_class": "UnusedFamilyTypesScanner",
        "group_id": "families",
        "description": "Family types with no instances placed",
        "default_checked": False,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "annotation_families",
        "name": "Annotation Families",
        "icon": u"\U0001F4DD",  # 📝
        "scanner_class": "AnnotationFamiliesScanner",
        "group_id": "families",
        "description": "Annotation families (tags, symbols) not used in views",
        "default_checked": False,
        "safety_level": SAFETY_WARNING,
        "priority": PRIORITY_LOW
    },
    {
        "id": "profile_families",
        "name": "Profile Families",
        "icon": u"\u3030",  # 〰
        "scanner_class": "ProfileFamiliesScanner",
        "group_id": "families",
        "description": "Profile families not used by wall sweeps, railings, or cornices",
        "default_checked": False,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_LOW
    }
]


# ============================================================================
# GROUP 4: SYSTEM CLEANUP (6 categories)
# ============================================================================

SYSTEM_CLEANUP_CATEGORIES = [
    {
        "id": "import_symbols",
        "name": "Import Symbols",
        "icon": u"\U0001F4E5",  # 📥
        "scanner_class": "ImportSymbolsScanner",
        "group_id": "system_cleanup",
        "description": "CAD import symbol types not used in any views",
        "default_checked": True,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "cad_links",
        "name": "CAD Links",
        "icon": u"\U0001F517",  # 🔗
        "scanner_class": "CADLinksScanner",
        "group_id": "system_cleanup",
        "description": "CAD link types with no instances placed",
        "default_checked": True,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "unused_groups",
        "name": "Unused Groups",
        "icon": u"\U0001F465",  # 👥
        "scanner_class": "UnusedGroupsScanner",
        "group_id": "system_cleanup",
        "description": "Model and detail groups with no instances placed",
        "default_checked": True,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "design_options",
        "name": "Design Options",
        "icon": u"\U0001F3A8",  # 🎨
        "scanner_class": "DesignOptionsScanner",
        "group_id": "system_cleanup",
        "description": "Design options with no elements",
        "default_checked": False,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_LOW
    },
    {
        "id": "unplaced_separators",
        "name": "Unplaced Separators",
        "icon": u"\U0001F6AA",  # 🚪
        "scanner_class": "UnplacedSeparatorsScanner",
        "group_id": "system_cleanup",
        "description": "Room and space separation lines that are unplaced or invalid",
        "default_checked": True,
        "safety_level": SAFETY_SAFE,
        "priority": PRIORITY_MEDIUM
    },
    {
        "id": "orphaned_rooms",
        "name": "Orphaned Rooms/Areas",
        "icon": u"\U0001F3DA",  # 🏚
        "scanner_class": "OrphanedRoomsScanner",
        "group_id": "system_cleanup",
        "description": "Rooms and areas without valid boundaries (not enclosed or not placed)",
        "default_checked": True,
        "safety_level": SAFETY_WARNING,
        "priority": PRIORITY_HIGH
    }
]


# ============================================================================
# ALL CATEGORIES COMBINED
# ============================================================================

ALL_CATEGORIES_DATA = (
    ELEMENT_TYPES_CATEGORIES +
    VIEWS_SHEETS_CATEGORIES +
    FAMILIES_CATEGORIES +
    SYSTEM_CLEANUP_CATEGORIES
)


def create_purge_categories():
    """Create all purge category objects"""
    categories = []
    
    for cat_data in ALL_CATEGORIES_DATA:
        category = PurgeCategory(
            id=cat_data["id"],
            name=cat_data["name"],
            icon=cat_data["icon"],
            scanner_class=cat_data["scanner_class"],
            group_id=cat_data["group_id"],
            description=cat_data.get("description", ""),
            default_checked=cat_data.get("default_checked", True),
            safety_level=cat_data.get("safety_level", SAFETY_SAFE),
            priority=cat_data.get("priority", PRIORITY_HIGH)
        )
        categories.append(category)
    
    return categories


