# -*- coding: utf-8 -*-
"""
Purge Scanners
Scan document for unused elements
Copyright (c) 2025 T3Lab
"""

__author__ = "T3Lab"

from Autodesk.Revit.DB import *

try:
    from .revit_utils import _eid_int
except:
    # Fallback if revit_utils not available
    def _eid_int(element_id):
        """Get ElementId integer value - works for Revit 2024-2027"""
        if element_id is None:
            return -1
        try:
            if hasattr(element_id, 'Value'):
                return element_id.Value
        except:
            pass
        try:
            if hasattr(element_id, 'IntegerValue'):
                return element_id.IntegerValue
        except:
            pass
        return -1


class BasePurgeScanner(object):
    """Base scanner class for all purge scanners"""
    
    def __init__(self, doc):
        """
        Initialize scanner
        
        Args:
            doc: Revit document
        """
        self.doc = doc
        self.unused_items = []
        self.total_items = 0
        self.progress_callback = None
        self.cancel_callback = None
    
    def scan(self):
        """
        Scan for unused items
        Must be implemented by subclass
        
        Returns:
            List of unused item dictionaries
        """
        raise NotImplementedError("Subclass must implement scan()")
    
    def report_progress(self, current, total, message):
        """
        Report progress to UI
        
        Args:
            current: Current item number
            total: Total items
            message: Progress message
        """
        if self.progress_callback:
            try:
                self.progress_callback(current, total, message)
            except:
                pass
    
    def is_cancelled(self):
        """Check if scan was cancelled"""
        if self.cancel_callback:
            try:
                return self.cancel_callback()
            except:
                return False
        return False
    
    def is_system_element(self, element):
        """
        Check if element is system/built-in
        
        Args:
            element: Element to check
            
        Returns:
            True if system element
        """
        try:
            # Check if element has Name property
            if hasattr(element, 'Name'):
                name = element.Name
                
                # System elements often have names like <By Category>
                if name.startswith('<') and name.endswith('>'):
                    return True
                
                # Check for empty or null names
                if not name or name.strip() == "":
                    return True
            
            # Check if read-only
            if hasattr(element, 'IsReadOnly'):
                try:
                    if element.IsReadOnly:
                        return True
                except:
                    pass
            
            # Check element ID - very low IDs are usually system
            if _eid_int(element.Id) < 100:
                return True
            
            return False
            
        except Exception as e:
            # If we can't determine, assume it's system for safety
            return True
    
    def can_delete_element(self, element):
        """
        Check if element can be safely deleted
        
        Args:
            element: Element to check
            
        Returns:
            Tuple of (can_delete: bool, reason: str)
        """
        # Check if system element
        if self.is_system_element(element):
            return (False, "System element")
        
        # Check if element can be deleted
        try:
            if not self.doc.GetElement(element.Id):
                return (False, "Element not found")
        except:
            return (False, "Invalid element")
        
        return (True, None)
    
    def create_item_dict(self, element, additional_info=None):
        """
        Create dictionary for unused item
        
        Args:
            element: The element
            additional_info: Additional information dictionary
            
        Returns:
            Item dictionary
        """
        can_delete, reason = self.can_delete_element(element)
        
        # Get name with multiple fallback methods
        name = "Unknown"
        
        # Check if this is a loadable family (has Family property, not just FamilyName)
        is_loadable_family = False
        try:
            if hasattr(element, 'Family') and element.Family:
                # This is a FamilySymbol (loadable family type)
                is_loadable_family = True
        except:
            pass
        
        # For loadable families, show "FamilyName: TypeName"
        if is_loadable_family and hasattr(element, 'FamilyName'):
            try:
                family = element.FamilyName
                type_name = element.Name if hasattr(element, 'Name') else ""
                if family and type_name:
                    # Show as "Family: TypeName" (e.g. "Window-Fixed: 600x900mm")
                    name = "{}: {}".format(family, type_name)
                elif family:
                    name = family
                elif type_name:
                    name = type_name
            except:
                pass
        
        # For system families (Wall, Floor, Roof) or if above failed, use Name directly
        if (not name or name == "Unknown") and hasattr(element, 'Name'):
            base_name = element.Name
            
            # Check if this is a system type (WallType, FloorType, etc.) with generic name
            try:
                elem_type_name = element.GetType().Name
                
                # For system element types, check if name is generic
                if "Type" in elem_type_name and hasattr(element, 'FamilyName'):
                    family_name = element.FamilyName
                    
                    # If Name equals FamilyName (like "Basic Wall" == "Basic Wall"), it's generic
                    # Add ID to make it unique
                    if base_name == family_name:
                        # Generic name - add ID for uniqueness
                        name = "{} [ID:{}]".format(base_name, _eid_int(element.Id))
                    else:
                        # Name is already specific (like "Exterior - Brick on CMU")
                        name = base_name
                else:
                    # Not a type or no FamilyName - just use base name
                    name = base_name
                    
            except:
                # If anything fails, just use the base name
                name = base_name
        
        # Fallback: Try get_Name() method
        if (not name or name == "Unknown") and hasattr(element, 'get_Name'):
            try:
                name = element.get_Name()
            except:
                pass
        
        # Fallback: Use element type + ID
        if not name or name == "Unknown" or name.strip() == "":
            try:
                type_name = element.GetType().Name
                name = "{} ({})".format(type_name, _eid_int(element.Id))
            except:
                name = "Element {}".format(_eid_int(element.Id))
        
        # Get category with fallback to element type
        category = "No Category"
        if element.Category:
            category = element.Category.Name
        else:
            # For elements without category, use their class type
            try:
                type_name = element.GetType().Name
                # Make it more readable
                if "Type" in type_name:
                    category = type_name  # e.g. "DimensionType", "TextNoteType"
                else:
                    category = "{} Type".format(type_name)
            except:
                category = "No Category"
        
        item = {
            'element': element,
            'name': name,
            'id': _eid_int(element.Id),
            'category': category,
            'can_delete': can_delete,
            'warning': reason if not can_delete else None
        }
        
        # Add additional info if provided
        if additional_info:
            item.update(additional_info)
        
        return item


class MaterialScanner(BasePurgeScanner):
    """Scanner for unused materials"""
    
    def scan(self):
        """Scan for unused materials"""
        self.unused_items = []
        
        try:
            # Get all materials
            collector = FilteredElementCollector(self.doc)
            all_materials = collector.OfClass(Material).ToElements()
            
            self.total_items = len(list(all_materials))
            self.report_progress(0, self.total_items, "Collecting materials...")
            
            # Build usage dictionary from element types
            usage_dict = {}
            
            # Check all element types for material assignments
            type_collector = FilteredElementCollector(self.doc).OfClass(ElementType)
            type_list = list(type_collector)
            
            for i, elem_type in enumerate(type_list):
                if self.is_cancelled():
                    return []
                
                if i % 50 == 0:
                    self.report_progress(i, len(type_list), 
                                       "Scanning element types ({}/{})...".format(i, len(type_list)))
                
                try:
                    # Check MATERIAL_ID_PARAM parameter
                    mat_param = elem_type.get_Parameter(BuiltInParameter.MATERIAL_ID_PARAM)
                    if mat_param:
                        mat_id = mat_param.AsElementId()
                        if mat_id and _eid_int(mat_id) > 0:
                            usage_dict[_eid_int(mat_id)] = True
                    
                    # For compound structures (walls, floors, etc.)
                    if hasattr(elem_type, 'GetCompoundStructure'):
                        try:
                            compound = elem_type.GetCompoundStructure()
                            if compound:
                                for layer in compound.GetLayers():
                                    mat_id = layer.MaterialId
                                    if mat_id and _eid_int(mat_id) > 0:
                                        usage_dict[_eid_int(mat_id)] = True
                        except:
                            pass
                    
                except Exception as e:
                    continue
            
            # Check which materials are unused
            material_list = list(all_materials)
            for i, mat in enumerate(material_list):
                if self.is_cancelled():
                    return []
                
                self.report_progress(i, len(material_list), 
                                   "Analyzing materials ({}/{})...".format(i + 1, len(material_list)))
                
                # Skip system materials
                if self.is_system_element(mat):
                    continue
                
                # Check if material is used
                if _eid_int(mat.Id) not in usage_dict:
                    item = self.create_item_dict(mat, {
                        'type': 'Material',
                        'class': mat.MaterialClass if hasattr(mat, 'MaterialClass') else "Unknown"
                    })
                    self.unused_items.append(item)
            
            self.report_progress(self.total_items, self.total_items, 
                               "Scan complete. Found {} unused materials.".format(len(self.unused_items)))
            
            return self.unused_items
            
        except Exception as e:
            self.report_progress(0, 0, "Error scanning materials: {}".format(str(e)))
            return []


class LinePatternScanner(BasePurgeScanner):
    """Scanner for unused line patterns"""
    
    def scan(self):
        """Scan for unused line patterns"""
        self.unused_items = []
        
        try:
            # Get all line patterns
            collector = FilteredElementCollector(self.doc)
            all_patterns = collector.OfClass(LinePatternElement).ToElements()
            
            pattern_list = list(all_patterns)
            self.total_items = len(pattern_list)
            self.report_progress(0, self.total_items, "Collecting line patterns...")
            
            # Build usage dictionary from line styles (subcategories)
            usage_dict = {}
            
            # Get Lines category
            try:
                lines_category = self.doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
                
                if lines_category and lines_category.SubCategories:
                    subcat_list = list(lines_category.SubCategories)
                    
                    for i, subcat in enumerate(subcat_list):
                        if self.is_cancelled():
                            return []
                        
                        if i % 10 == 0:
                            self.report_progress(i, len(subcat_list), 
                                               "Scanning line styles ({}/{})...".format(i, len(subcat_list)))
                        
                        try:
                            # Check projection line pattern
                            pattern_id = subcat.GetLinePatternId(GraphicsStyleType.Projection)
                            if pattern_id and _eid_int(pattern_id) > 0:
                                usage_dict[_eid_int(pattern_id)] = True
                            
                            # Check cut line pattern
                            pattern_id = subcat.GetLinePatternId(GraphicsStyleType.Cut)
                            if pattern_id and _eid_int(pattern_id) > 0:
                                usage_dict[_eid_int(pattern_id)] = True
                                
                        except Exception as e:
                            continue
                            
            except Exception as e:
                pass
            
            # Check which line patterns are unused
            for i, pattern in enumerate(pattern_list):
                if self.is_cancelled():
                    return []
                
                self.report_progress(i, self.total_items, 
                                   "Analyzing line patterns ({}/{})...".format(i + 1, self.total_items))
                
                # Skip system patterns
                if self.is_system_element(pattern):
                    continue
                
                # Check if pattern is used
                if _eid_int(pattern.Id) not in usage_dict:
                    # Get pattern details
                    line_pattern = pattern.GetLinePattern()
                    pattern_type = "Simple" if line_pattern.IsSimple() else "Complex"
                    
                    item = self.create_item_dict(pattern, {
                        'type': 'Line Pattern',
                        'pattern_type': pattern_type
                    })
                    self.unused_items.append(item)
            
            self.report_progress(self.total_items, self.total_items, 
                               "Scan complete. Found {} unused line patterns.".format(len(self.unused_items)))
            
            return self.unused_items
            
        except Exception as e:
            self.report_progress(0, 0, "Error scanning line patterns: {}".format(str(e)))
            return []


class FillPatternScanner(BasePurgeScanner):
    """Scanner for unused fill patterns"""
    
    def scan(self):
        """Scan for unused fill patterns"""
        self.unused_items = []
        
        try:
            # Get all fill patterns
            collector = FilteredElementCollector(self.doc)
            all_patterns = collector.OfClass(FillPatternElement).ToElements()
            
            pattern_list = list(all_patterns)
            self.total_items = len(pattern_list)
            self.report_progress(0, self.total_items, "Collecting fill patterns...")
            
            # Build usage dictionary
            usage_dict = {}
            
            # Check materials
            mat_collector = FilteredElementCollector(self.doc).OfClass(Material)
            mat_list = list(mat_collector)
            
            for i, mat in enumerate(mat_list):
                if self.is_cancelled():
                    return []
                
                if i % 50 == 0:
                    self.report_progress(i, len(mat_list), 
                                       "Scanning materials ({}/{})...".format(i, len(mat_list)))
                
                try:
                    # Surface pattern
                    surf_pattern_id = mat.SurfaceForegroundPatternId
                    if surf_pattern_id and _eid_int(surf_pattern_id) > 0:
                        usage_dict[_eid_int(surf_pattern_id)] = True
                    
                    # Cut pattern
                    cut_pattern_id = mat.CutForegroundPatternId
                    if cut_pattern_id and _eid_int(cut_pattern_id) > 0:
                        usage_dict[_eid_int(cut_pattern_id)] = True
                except:
                    pass
            
            # Check filled regions
            try:
                filled_region_collector = FilteredElementCollector(self.doc).OfClass(FilledRegion)
                for region in filled_region_collector:
                    try:
                        region_type = self.doc.GetElement(region.GetTypeId())
                        if region_type:
                            pattern_id = region_type.ForegroundPatternId
                            if pattern_id and _eid_int(pattern_id) > 0:
                                usage_dict[_eid_int(pattern_id)] = True
                    except:
                        pass
            except:
                pass
            
            # Check which patterns are unused
            for i, pattern in enumerate(pattern_list):
                if self.is_cancelled():
                    return []
                
                self.report_progress(i, self.total_items, 
                                   "Analyzing fill patterns ({}/{})...".format(i + 1, self.total_items))
                
                # Skip system patterns
                if self.is_system_element(pattern):
                    continue
                
                # Check if pattern is used
                if _eid_int(pattern.Id) not in usage_dict:
                    # Get pattern details
                    fill_pattern = pattern.GetFillPattern()
                    pattern_target = "Drafting" if fill_pattern.Target == FillPatternTarget.Drafting else "Model"
                    
                    item = self.create_item_dict(pattern, {
                        'type': 'Fill Pattern',
                        'pattern_target': pattern_target
                    })
                    self.unused_items.append(item)
            
            self.report_progress(self.total_items, self.total_items, 
                               "Scan complete. Found {} unused fill patterns.".format(len(self.unused_items)))
            
            return self.unused_items
            
        except Exception as e:
            self.report_progress(0, 0, "Error scanning fill patterns: {}".format(str(e)))
            return []


class TextTypeScanner(BasePurgeScanner):
    """Scanner for unused text note types"""
    
    def scan(self):
        """Scan for unused text note types"""
        self.unused_items = []
        
        try:
            # Get all text note types
            collector = FilteredElementCollector(self.doc)
            all_types = collector.OfClass(TextNoteType).ToElements()
            
            type_list = list(all_types)
            self.total_items = len(type_list)
            self.report_progress(0, self.total_items, "Collecting text note types...")
            
            # Build usage dictionary from text notes
            usage_dict = {}
            
            text_collector = FilteredElementCollector(self.doc).OfClass(TextNote)
            text_list = list(text_collector)
            
            for i, text in enumerate(text_list):
                if self.is_cancelled():
                    return []
                
                if i % 100 == 0:
                    self.report_progress(i, len(text_list), 
                                       "Scanning text notes ({}/{})...".format(i, len(text_list)))
                
                try:
                    type_id = text.GetTypeId()
                    if type_id and _eid_int(type_id) > 0:
                        usage_dict[_eid_int(type_id)] = True
                except:
                    pass
            
            # Check which types are unused
            for i, text_type in enumerate(type_list):
                if self.is_cancelled():
                    return []
                
                self.report_progress(i, self.total_items, 
                                   "Analyzing text types ({}/{})...".format(i + 1, self.total_items))
                
                # Skip system types
                if self.is_system_element(text_type):
                    continue
                
                # Check if type is used
                if _eid_int(text_type.Id) not in usage_dict:
                    item = self.create_item_dict(text_type, {
                        'type': 'Text Note Type'
                    })
                    self.unused_items.append(item)
            
            self.report_progress(self.total_items, self.total_items, 
                               "Scan complete. Found {} unused text types.".format(len(self.unused_items)))
            
            return self.unused_items
            
        except Exception as e:
            self.report_progress(0, 0, "Error scanning text types: {}".format(str(e)))
            return []


class DimensionTypeScanner(BasePurgeScanner):
    """Scanner for unused dimension types"""
    
    def scan(self):
        """Scan for unused dimension types"""
        self.unused_items = []
        
        try:
            # Get all dimension types
            collector = FilteredElementCollector(self.doc)
            all_types = collector.OfClass(DimensionType).ToElements()
            
            type_list = list(all_types)
            self.total_items = len(type_list)
            self.report_progress(0, self.total_items, "Collecting dimension types...")
            
            # Build usage dictionary from dimensions
            usage_dict = {}
            
            dim_collector = FilteredElementCollector(self.doc).OfClass(Dimension)
            dim_list = list(dim_collector)
            
            for i, dim in enumerate(dim_list):
                if self.is_cancelled():
                    return []
                
                if i % 100 == 0:
                    self.report_progress(i, len(dim_list), 
                                       "Scanning dimensions ({}/{})...".format(i, len(dim_list)))
                
                try:
                    type_id = dim.GetTypeId()
                    if type_id and _eid_int(type_id) > 0:
                        usage_dict[_eid_int(type_id)] = True
                except:
                    pass
            
            # Check which types are unused
            for i, dim_type in enumerate(type_list):
                if self.is_cancelled():
                    return []
                
                self.report_progress(i, self.total_items, 
                                   "Analyzing dimension types ({}/{})...".format(i + 1, self.total_items))
                
                # Skip system types
                if self.is_system_element(dim_type):
                    continue
                
                # Check if type is used
                if _eid_int(dim_type.Id) not in usage_dict:
                    item = self.create_item_dict(dim_type, {
                        'type': 'Dimension Type',
                        'style_type': dim_type.StyleType.ToString() if hasattr(dim_type, 'StyleType') else 'Unknown'
                    })
                    self.unused_items.append(item)
            
            self.report_progress(self.total_items, self.total_items, 
                               "Scan complete. Found {} unused dimension types.".format(len(self.unused_items)))
            
            return self.unused_items
            
        except Exception as e:
            self.report_progress(0, 0, "Error scanning dimension types: {}".format(str(e)))
            return []


class LineStyleScanner(BasePurgeScanner):
    """Scanner for unused line styles (subcategories of Lines)"""
    
    def scan(self):
        """Scan for unused line styles"""
        self.unused_items = []
        
        try:
            # Get Lines category
            lines_cat = self.doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
            if not lines_cat:
                return []
            
            # Get all subcategories (line styles)
            subcats = list(lines_cat.SubCategories)
            self.total_items = len(subcats)
            self.report_progress(0, self.total_items, "Collecting line styles...")
            
            # Build usage dictionary
            usage_dict = {}
            
            # Check model lines
            try:
                model_lines = FilteredElementCollector(self.doc).OfClass(CurveElement)
                for i, line in enumerate(model_lines):
                    if self.is_cancelled():
                        return []
                    
                    if i % 100 == 0:
                        self.report_progress(i, self.total_items, 
                                           "Scanning model lines...")
                    
                    try:
                        gs = line.LineStyle
                        if gs:
                            usage_dict[_eid_int(gs.Id)] = True
                    except:
                        pass
            except:
                pass
            
            # Check detail lines  
            try:
                detail_lines = FilteredElementCollector(self.doc).OfClass(DetailCurve)
                for line in detail_lines:
                    try:
                        gs = line.LineStyle
                        if gs:
                            usage_dict[_eid_int(gs.Id)] = True
                    except:
                        pass
            except:
                pass
            
            # Check which styles are unused
            for i, subcat in enumerate(subcats):
                if self.is_cancelled():
                    return []
                
                self.report_progress(i, self.total_items, 
                                   "Analyzing line styles ({}/{})...".format(i + 1, self.total_items))
                
                # Get GraphicsStyle from subcategory
                try:
                    gs = subcat.GetGraphicsStyle(GraphicsStyleType.Projection)
                    if not gs:
                        continue
                    
                    # Skip system styles
                    if self.is_system_element(gs):
                        continue
                    
                    # Check if used
                    if _eid_int(gs.Id) not in usage_dict:
                        item = self.create_item_dict(gs, {
                            'type': 'Line Style',
                            'category': subcat.Name
                        })
                        self.unused_items.append(item)
                except:
                    pass
            
            self.report_progress(self.total_items, self.total_items, 
                               "Scan complete. Found {} unused line styles.".format(len(self.unused_items)))
            
            return self.unused_items
            
        except Exception as e:
            self.report_progress(0, 0, "Error scanning line styles: {}".format(str(e)))
            return []


class ViewTemplateScanner(BasePurgeScanner):
    """Scanner for unused view templates"""
    
    def scan(self):
        """Scan for unused view templates"""
        self.unused_items = []
        
        try:
            # Get all view templates
            collector = FilteredElementCollector(self.doc)
            all_views = collector.OfClass(View).ToElements()
            
            templates = [v for v in all_views if v.IsTemplate]
            self.total_items = len(templates)
            self.report_progress(0, self.total_items, "Collecting view templates...")
            
            # Build usage dictionary from views
            usage_dict = {}
            
            views = [v for v in all_views if not v.IsTemplate]
            
            for i, view in enumerate(views):
                if self.is_cancelled():
                    return []
                
                if i % 50 == 0:
                    self.report_progress(i, len(views), 
                                       "Scanning views ({}/{})...".format(i, len(views)))
                
                try:
                    template_id = view.ViewTemplateId
                    if template_id and _eid_int(template_id) > 0:
                        usage_dict[_eid_int(template_id)] = True
                except:
                    pass
            
            # Check which templates are unused
            for i, template in enumerate(templates):
                if self.is_cancelled():
                    return []
                
                self.report_progress(i, self.total_items, 
                                   "Analyzing templates ({}/{})...".format(i + 1, self.total_items))
                
                # Skip if can't get name (protected)
                try:
                    name = template.Name
                except:
                    continue
                
                # Skip system templates
                if self.is_system_element(template):
                    continue
                
                # Check if used
                if _eid_int(template.Id) not in usage_dict:
                    item = self.create_item_dict(template, {
                        'type': 'View Template',
                        'view_type': template.ViewType.ToString()
                    })
                    item['warning'] = 'May be used for future views'
                    self.unused_items.append(item)
            
            self.report_progress(self.total_items, self.total_items, 
                               "Scan complete. Found {} unused templates.".format(len(self.unused_items)))
            
            return self.unused_items
            
        except Exception as e:
            self.report_progress(0, 0, "Error scanning templates: {}".format(str(e)))
            return []


class FilterScanner(BasePurgeScanner):
    """Scanner for unused filters"""
    
    def scan(self):
        """Scan for unused filters"""
        self.unused_items = []
        
        try:
            # Get all filters
            collector = FilteredElementCollector(self.doc)
            all_filters = collector.OfClass(FilterElement).ToElements()
            
            # Separate ParameterFilterElement from other filters
            param_filters = []
            for f in all_filters:
                try:
                    if isinstance(f, ParameterFilterElement):
                        param_filters.append(f)
                except:
                    pass
            
            self.total_items = len(param_filters)
            self.report_progress(0, self.total_items, "Collecting filters...")
            
            # Build usage dictionary from views
            usage_dict = {}
            
            all_views = FilteredElementCollector(self.doc).OfClass(View).ToElements()
            views_and_templates = list(all_views)
            
            for i, view in enumerate(views_and_templates):
                if self.is_cancelled():
                    return []
                
                if i % 50 == 0:
                    self.report_progress(i, len(views_and_templates), 
                                       "Scanning views/templates ({}/{})...".format(i, len(views_and_templates)))
                
                try:
                    # Get filters applied to view
                    filter_ids = view.GetFilters()
                    for fid in filter_ids:
                        if fid and _eid_int(fid) > 0:
                            usage_dict[_eid_int(fid)] = True
                except:
                    pass
            
            # Check which filters are unused
            for i, filter_elem in enumerate(param_filters):
                if self.is_cancelled():
                    return []
                
                self.report_progress(i, self.total_items, 
                                   "Analyzing filters ({}/{})...".format(i + 1, self.total_items))
                
                # Skip system filters
                if self.is_system_element(filter_elem):
                    continue
                
                # Check if used
                if _eid_int(filter_elem.Id) not in usage_dict:
                    # Get filter categories
                    try:
                        cats = filter_elem.GetCategories()
                        cat_names = []
                        for cat_id in cats:
                            try:
                                cat = self.doc.Settings.Categories.get_Item(cat_id)
                                if cat:
                                    cat_names.append(cat.Name)
                            except:
                                pass
                        
                        item = self.create_item_dict(filter_elem, {
                            'type': 'Filter',
                            'categories': ', '.join(cat_names[:3]) if cat_names else 'None'
                        })
                        item['warning'] = 'Check before deleting'
                        self.unused_items.append(item)
                    except:
                        pass
            
            self.report_progress(self.total_items, self.total_items, 
                               "Scan complete. Found {} unused filters.".format(len(self.unused_items)))
            
            return self.unused_items
            
        except Exception as e:
            self.report_progress(0, 0, "Error scanning filters: {}".format(str(e)))
            return []


# SCANNER FACTORY
def create_scanner(scanner_class_name, doc):
    """
    Create scanner instance by class name
    
    Args:
        scanner_class_name: Name of scanner class
        doc: Revit document
        
    Returns:
        Scanner instance or None
    """
    if scanner_class_name == 'MaterialScanner':
        return MaterialScanner(doc)
    elif scanner_class_name == 'LinePatternScanner':
        return LinePatternScanner(doc)
    elif scanner_class_name == 'FillPatternScanner':
        return FillPatternScanner(doc)
    elif scanner_class_name == 'TextTypeScanner':
        return TextTypeScanner(doc)
    elif scanner_class_name == 'DimensionTypeScanner':
        return DimensionTypeScanner(doc)
    elif scanner_class_name == 'LineStyleScanner':
        return LineStyleScanner(doc)
    elif scanner_class_name == 'ViewTemplateScanner':
        return ViewTemplateScanner(doc)
    elif scanner_class_name == 'FilterScanner':
        return FilterScanner(doc)
    # Phase 2: Element Type Scanners
    elif scanner_class_name == 'WallTypeScanner':
        from .purge_scanner_elements import WallTypeScanner
        return WallTypeScanner(doc)
    elif scanner_class_name == 'FloorTypeScanner':
        from .purge_scanner_elements import FloorTypeScanner
        return FloorTypeScanner(doc)
    elif scanner_class_name == 'RoofTypeScanner':
        from .purge_scanner_elements import RoofTypeScanner
        return RoofTypeScanner(doc)
    # Phase 3: System Cleanup Scanners
    elif scanner_class_name == 'ImportSymbolsScanner':
        from .purge_scanner_system import ImportSymbolsScanner
        return ImportSymbolsScanner(doc)
    elif scanner_class_name == 'CADLinksScanner':
        from .purge_scanner_system import CADLinksScanner
        return CADLinksScanner(doc)
    elif scanner_class_name == 'UnusedGroupsScanner':
        from .purge_scanner_system import UnusedGroupsScanner
        return UnusedGroupsScanner(doc)
    elif scanner_class_name == 'DesignOptionsScanner':
        from .purge_scanner_system import DesignOptionsScanner
        return DesignOptionsScanner(doc)
    elif scanner_class_name == 'UnplacedSeparatorsScanner':
        from .purge_scanner_system import UnplacedSeparatorsScanner
        return UnplacedSeparatorsScanner(doc)
    elif scanner_class_name == 'OrphanedRoomsScanner':
        from .purge_scanner_system import OrphanedRoomsScanner
        return OrphanedRoomsScanner(doc)
    # Phase 4: Views & Sheets Scanners
    elif scanner_class_name == 'EmptySheetsScanner':
        from .purge_scanner_views import EmptySheetsScanner
        return EmptySheetsScanner(doc)
    elif scanner_class_name == 'UnusedSchedulesScanner':
        from .purge_scanner_views import UnusedSchedulesScanner
        return UnusedSchedulesScanner(doc)
    elif scanner_class_name == 'LegendViewsScanner':
        from .purge_scanner_views import LegendViewsScanner
        return LegendViewsScanner(doc)
    elif scanner_class_name == 'TempWorkingViewsScanner':
        from .purge_scanner_views import TempWorkingViewsScanner
        return TempWorkingViewsScanner(doc)
    # Phase 5: Families Scanners
    elif scanner_class_name == 'DetailComponentsScanner':
        from .purge_scanner_families import DetailComponentsScanner
        return DetailComponentsScanner(doc)
    elif scanner_class_name == 'UnusedFamiliesScanner':
        from .purge_scanner_families import UnusedFamiliesScanner
        return UnusedFamiliesScanner(doc)
    elif scanner_class_name == 'UnusedFamilyTypesScanner':
        from .purge_scanner_families import UnusedFamilyTypesScanner
        return UnusedFamilyTypesScanner(doc)
    elif scanner_class_name == 'AnnotationFamiliesScanner':
        from .purge_scanner_families import AnnotationFamiliesScanner
        return AnnotationFamiliesScanner(doc)
    elif scanner_class_name == 'ProfileFamiliesScanner':
        from .purge_scanner_families import ProfileFamiliesScanner
        return ProfileFamiliesScanner(doc)
    else:
        return None