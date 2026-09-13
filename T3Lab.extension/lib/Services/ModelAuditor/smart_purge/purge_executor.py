# -*- coding: utf-8 -*-
"""
Purge Executor
Handles actual deletion of unused elements with transaction support
Copyright (c) 2025 Dang Quoc Truong (DQT)
"""

__author__ = "Dang Quoc Truong (DQT)"

from Autodesk.Revit.DB import Transaction, TransactionGroup, TransactionStatus, ElementId
from System.Collections.Generic import List


class _PendingTransactionError(RuntimeError):
    """Revit is still resolving failures; no further transactions may run."""


class PurgeExecutor(object):
    """Executes purge operations with transaction support"""
    
    def __init__(self, doc):
        """
        Initialize purge executor
        
        Args:
            doc: Revit document
        """
        self.doc = doc
        self.deleted_items = []
        self.failed_items = []
        self.progress_callback = None
        self.dry_run = False  # Default to false (actually delete)
    
    def execute_purge(self, categories_to_purge):
        """
        Execute purge for selected categories
        
        Args:
            categories_to_purge: List of PurgeCategory objects to purge
            
        Returns:
            Tuple of (deleted_count, failed_count, deleted_items, failed_items)
        """
        self.deleted_items = []
        self.failed_items = []
        
        # Create transaction group for undo
        tg = TransactionGroup(self.doc, "T3Lab: Smart Purge")
        
        try:
            tg.Start()
            total_items = sum(len(cat.unused_items) for cat in categories_to_purge)
            current_item = 0
            
            # Process each category
            for category in categories_to_purge:
                if not category.unused_items:
                    continue
                
                self.report_progress(
                    current_item, 
                    total_items,
                    "Purging {}...".format(category.name)
                )
                
                # Delete items in this category
                deleted, failed = self.delete_category_items(
                    category.name,
                    category.unused_items
                )
                
                self.deleted_items.extend(deleted)
                self.failed_items.extend(failed)
                
                current_item += len(category.unused_items)
            
            # DRY RUN: Rollback all changes (don't save)
            if self.dry_run:
                tg.RollBack()
            else:
                # REAL RUN: Assimilate transaction group (makes it one undo operation)
                status = tg.Assimilate()
                if status != TransactionStatus.Committed:
                    raise RuntimeError("Transaction group was not committed: {}".format(status))
            
            self.report_progress(
                total_items,
                total_items,
                "Purge complete! {}: {}, Failed: {}".format(
                    "Would delete" if self.dry_run else "Deleted",
                    len(self.deleted_items),
                    len(self.failed_items)
                )
            )
            
            return (
                len(self.deleted_items),
                len(self.failed_items),
                self.deleted_items,
                self.failed_items
            )
            
        except Exception as e:
            self.deleted_items = []
            if not isinstance(e, _PendingTransactionError) and tg.GetStatus() == TransactionStatus.Started:
                tg.RollBack()
            raise Exception("Purge failed: {}".format(str(e)))
    
    def delete_category_items(self, category_name, items):
        """
        Delete items from a single category
        
        Args:
            category_name: Name of category
            items: List of item dictionaries
            
        Returns:
            Tuple of (deleted_items, failed_items)
        """
        deleted = []
        failed = []
        
        # Create transaction for this category
        t = Transaction(self.doc, "T3Lab: Purge {}".format(category_name))
        
        try:
            t.Start()
            for item in items:
                try:
                    # Skip if marked as can't delete
                    if not item.get('can_delete', True):
                        failed.append({
                            'item': item,
                            'reason': item.get('warning', 'Cannot delete')
                        })
                        continue
                    
                    # Get element
                    element = item['element']
                    element_id = element.Id
                    
                    # DRY RUN: Just count, don't actually delete
                    if self.dry_run:
                        # Simulate success - just count what WOULD be deleted
                        deleted.append(item)
                    else:
                        # ACTUALLY DELETE
                        deleted_ids = self.doc.Delete(element_id)
                        
                        if deleted_ids and deleted_ids.Count > 0:
                            deleted.append(item)
                        else:
                            failed.append({
                                'item': item,
                                'reason': 'Delete returned no IDs'
                            })
                    
                except Exception as e:
                    failed.append({
                        'item': item,
                        'reason': str(e)
                    })
            
            # DRY RUN: Always rollback (don't save changes)
            if self.dry_run:
                t.RollBack()
            else:
                # REAL RUN: Commit changes
                options = t.GetFailureHandlingOptions()
                options.SetForcedModalHandling(True)
                t.SetFailureHandlingOptions(options)
                status = t.Commit()
                if status != TransactionStatus.Committed:
                    raise RuntimeError("Transaction was not committed: {}".format(status))
            
        except Exception as e:
            if t.GetStatus() == TransactionStatus.Pending:
                # Do not start another category or roll back its enclosing group
                # while Revit still owns failure processing for this transaction.
                raise _PendingTransactionError(
                    "Purge is awaiting Revit failure resolution; stop and resolve the Revit dialog.")
            if t.GetStatus() == TransactionStatus.Started:
                t.RollBack()
            # A failed category transaction saved none of its deletions.
            # Keep existing item errors, but never count rolled-back items as deleted.
            deleted = []
            failed_item_ids = set(id(entry['item']) for entry in failed)
            for item in items:
                if id(item) not in failed_item_ids:
                    failed.append({
                        'item': item,
                        'reason': "Transaction failed: {}".format(str(e))
                    })
                    failed_item_ids.add(id(item))
        
        return deleted, failed
    
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
            except Exception:
                pass


class PurgeResult(object):
    """Represents result of a purge operation"""
    
    def __init__(self, deleted_count, failed_count, deleted_items, failed_items):
        """
        Initialize purge result
        
        Args:
            deleted_count: Number of deleted items
            failed_count: Number of failed items
            deleted_items: List of successfully deleted items
            failed_items: List of failed items with reasons
        """
        self.deleted_count = deleted_count
        self.failed_count = failed_count
        self.deleted_items = deleted_items
        self.failed_items = failed_items
        self.total_count = deleted_count + failed_count
    
    def get_summary(self):
        """Get summary text"""
        if self.failed_count == 0:
            return "Successfully deleted {} items!".format(self.deleted_count)
        else:
            return "Deleted: {}, Failed: {} items".format(
                self.deleted_count,
                self.failed_count
            )
    
    def get_detailed_report(self):
        """Get detailed report text"""
        lines = []
        lines.append("=" * 50)
        lines.append("PURGE RESULTS")
        lines.append("=" * 50)
        lines.append("")
        lines.append("Total items processed: {}".format(self.total_count))
        lines.append("Successfully deleted: {}".format(self.deleted_count))
        lines.append("Failed to delete: {}".format(self.failed_count))
        lines.append("")
        
        if self.deleted_items:
            lines.append("DELETED ITEMS:")
            lines.append("-" * 50)
            for item in self.deleted_items:
                lines.append("  - {} (ID: {})".format(
                    item['name'],
                    item['id']
                ))
            lines.append("")
        
        if self.failed_items:
            lines.append("FAILED ITEMS:")
            lines.append("-" * 50)
            for failed in self.failed_items:
                item = failed['item']
                reason = failed['reason']
                lines.append("  - {} (ID: {}): {}".format(
                    item['name'],
                    item['id'],
                    reason
                ))
            lines.append("")
        
        lines.append("=" * 50)
        
        return "\n".join(lines)
