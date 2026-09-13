# -*- coding: utf-8 -*-
"""
Advanced Purge Executor
Executes purge operations with enhanced safety features

Copyright © 2025 Dang Quoc Truong (DQT)
"""

__author__ = "Dang Quoc Truong (DQT)"

from Autodesk.Revit.DB import Transaction, TransactionGroup, TransactionStatus, ElementId


class _PendingTransactionError(RuntimeError):
    """No further transactions may run until Revit resolves its failures."""


class AdvancedPurgeExecutor(object):
    """Executes advanced purge operations safely"""
    
    def __init__(self, doc):
        """
        Initialize executor
        
        Args:
            doc: Revit document
        """
        self.doc = doc
        self.dry_run = True  # Always start in dry run mode
        self.progress_errors = []

    def _report_progress(self, callback, current, total, message):
        """A failed UI update must not change transaction results."""
        if callback:
            try:
                callback(current, total, message)
            except Exception as exc:
                self.progress_errors.append(str(exc))
        
    def execute_purge(self, items_by_category, progress_callback=None):
        """
        Execute purge for multiple categories
        
        Args:
            items_by_category: Dictionary of {category: [items]}
            progress_callback: Optional callback(current, total, message)
            
        Returns:
            Tuple of (deleted_items, failed_items)
        """
        deleted = []
        failed = []
        self.progress_errors = []
        
        total_items = sum(len(items) for items in items_by_category.values())
        current = 0
        
        # Create transaction group for all operations
        tg = TransactionGroup(self.doc, "T3Lab: Advanced Purge")
        
        try:
            tg.Start()
            for category, items in items_by_category.items():
                self._report_progress(progress_callback, current, total_items,
                                      "Processing {}...".format(category.name))
                
                # Process items in this category
                cat_deleted, cat_failed = self._execute_category(
                    category, items, progress_callback, current, total_items
                )
                
                deleted.extend(cat_deleted)
                failed.extend(cat_failed)
                current += len(items)
            
            # Rollback if dry run, commit if real
            if self.dry_run:
                tg.RollBack()
                self._report_progress(progress_callback, total_items, total_items,
                                      "Dry run complete - no changes made")
            else:
                status = tg.Assimilate()
                if status != TransactionStatus.Committed:
                    raise RuntimeError("Transaction group was not committed: {}".format(status))
                self._report_progress(progress_callback, total_items, total_items,
                                      "Purge complete!")
            
            return deleted, failed
            
        except Exception as e:
            if not isinstance(e, _PendingTransactionError) and tg.GetStatus() == TransactionStatus.Started:
                tg.RollBack()
            self._report_progress(progress_callback, 0, total_items, "Error: {}".format(str(e)))
            raise
    
    def _execute_category(self, category, items, progress_callback, 
                         current_base, total_items):
        """
        Execute purge for a single category
        
        Args:
            category: PurgeCategory object
            items: List of items to purge
            progress_callback: Progress callback
            current_base: Base index for progress
            total_items: Total items across all categories
            
        Returns:
            Tuple of (deleted_items, failed_items)
        """
        deleted = []
        failed = []
        
        # Create transaction for this category
        t = Transaction(self.doc, "T3Lab: Purge {}".format(category.name))
        
        try:
            t.Start()
            for i, item in enumerate(items):
                if progress_callback and i % 10 == 0:
                    self._report_progress(
                        progress_callback,
                        current_base + i, 
                        total_items,
                        "Processing {} ({}/{})".format(
                            category.name, i+1, len(items)
                        )
                    )
                
                # Try to delete item
                try:
                    element_id = item.get('id')
                    
                    if self.dry_run:
                        # Dry run - just record what would be deleted
                        deleted.append(item)
                    else:
                        # Real deletion
                        if element_id:
                            self.doc.Delete(element_id)
                            deleted.append(item)
                        else:
                            failed.append({
                                'item': item,
                                'error': 'No element ID'
                            })
                            
                except Exception as e:
                    failed.append({
                        'item': item,
                        'error': str(e)
                    })
            
            # Rollback transaction if dry run, commit if real
            if self.dry_run:
                t.RollBack()
            else:
                options = t.GetFailureHandlingOptions()
                options.SetForcedModalHandling(True)
                t.SetFailureHandlingOptions(options)
                status = t.Commit()
                if status != TransactionStatus.Committed:
                    raise RuntimeError("Transaction was not committed: {}".format(status))
                
        except Exception as e:
            if t.GetStatus() == TransactionStatus.Pending:
                raise _PendingTransactionError(
                    "Purge is awaiting Revit failure resolution; stop and resolve the Revit dialog.")
            if t.GetStatus() == TransactionStatus.Started:
                t.RollBack()
            deleted = []
            failed_item_ids = set(id(entry['item']) for entry in failed)
            for item in items:
                if id(item) not in failed_item_ids:
                    failed.append({
                        'item': item,
                        'error': str(e)
                    })
                    failed_item_ids.add(id(item))
        
        return deleted, failed
    
    def can_delete_element(self, element_id):
        """
        Check if element can be deleted
        
        Args:
            element_id: ElementId to check
            
        Returns:
            True if element can be deleted
        """
        try:
            element = self.doc.GetElement(element_id)
            if not element:
                return False
            
            # Check if element is deletable
            if not element.CanBeDeleted(self.doc):
                return False
            
            return True
            
        except:
            return False
    
    def get_deletion_preview(self, element_id):
        """
        Get preview of what would be deleted
        
        Args:
            element_id: ElementId to check
            
        Returns:
            List of element IDs that would be deleted
        """
        try:
            from Autodesk.Revit.DB import ElementTransformUtils
            from System.Collections.Generic import List
            
            ids = List[ElementId]()
            ids.Add(element_id)
            
            # Get dependent elements
            dependent = self.doc.GetDependentElements(element_id)
            
            return [element_id] + list(dependent)
            
        except:
            return [element_id]
