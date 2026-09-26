# -*- coding: utf-8 -*-
"""
View Template Core Execution Logic
Handles Revit API operations for View Templates.
"""

from pyrevit import DB

def _eid_int(element_id):
    """Get integer value from ElementId - compatible across Revit 2024/2025/2026.
    Revit 2025+ uses .Value (Int64), older versions use .IntegerValue."""
    try:
        return element_id.Value  # Revit 2025+
    except AttributeError:
        return element_id.IntegerValue  # Revit 2024

def _eid_invalid_value():
    """Get the invalid ElementId integer value across versions."""
    return _eid_int(DB.ElementId.InvalidElementId)

def calculate_viewtemplate_usage(doc, template_items):
    """Calculate usage for view templates"""
    # Reset all counts
    for item in template_items:
        item.usage_count = 0
        item.usage_percentage = 0.0
    
    # Build lookup by template ID
    template_lookup = {}
    for item in template_items:
        template_lookup[item.id] = item
    
    total_views_with_template = 0
    invalid_id = _eid_invalid_value()
    
    try:
        # Get all views
        collector = DB.FilteredElementCollector(doc).OfClass(DB.View)
        
        for view in collector:
            try:
                if view.IsTemplate:
                    continue
                
                template_id = view.ViewTemplateId
                if template_id:
                    tid_int = _eid_int(template_id)
                    if tid_int > 0 and tid_int != invalid_id:
                        if tid_int in template_lookup:
                            template_lookup[tid_int].usage_count += 1
                            total_views_with_template += 1
            except:
                pass
        
        # Calculate percentages
        if total_views_with_template > 0:
            for item in template_items:
                item.usage_percentage = (item.usage_count / float(total_views_with_template)) * 100.0
                
    except Exception as ex:
        print("Error calculating view template usage: {}".format(str(ex)))
    
    return total_views_with_template

def rename_template(doc, template, new_name):
    """Rename a single view template within a transaction"""
    t = DB.Transaction(doc, "DQT - Rename View Template")
    t.Start()
    try:
        template.Name = new_name
        t.Commit()
        return True
    except Exception as e:
        t.RollBack()
        raise e


def duplicate_templates(doc, templates):
    """Duplicate multiple view templates in a single transaction.
    templates is a list of view template elements.
    """
    t = DB.Transaction(doc, "DQT - Duplicate View Templates")
    t.Start()
    success_count = 0
    try:
        for template in templates:
            try:
                new_id = template.Duplicate(DB.ViewDuplicateOption.Duplicate)
                if new_id and new_id != DB.ElementId.InvalidElementId:
                    new_view = doc.GetElement(new_id)
                    new_view.Name = "Copy of " + template.Name
                    success_count += 1
            except:
                pass
        t.Commit()
        return success_count
    except Exception as e:
        t.RollBack()
        raise e

def delete_templates(doc, templates):
    """Delete multiple view templates in a single transaction.
    templates is a list of view template elements.
    """
    t = DB.Transaction(doc, "DQT - Delete View Templates")
    t.Start()
    success_count = 0
    error_count = 0
    try:
        for template in templates:
            try:
                doc.Delete(template.Id)
                success_count += 1
            except:
                error_count += 1
        t.Commit()
        return success_count, error_count
    except Exception as e:
        t.RollBack()
        raise e
