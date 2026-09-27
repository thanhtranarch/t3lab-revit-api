# -*- coding: utf-8 -*-
"""Regression tests for PythonNet module identity safety in ManaAnno.

Importing a module that defines CLR interface implementations under two names
(for example ``GUI.CopyAnnotationDialog`` and ``CopyAnnotationDialog``) makes
PythonNet define the same CLR type twice and raises:
``TypeError: Duplicate type name within an assembly``.
"""
import ast
import os
import sys
import unittest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI_DIR = os.path.join(REPO, "T3Lab.extension", "lib", "GUI")
MANA_ANNO = os.path.join(GUI_DIR, "ManaAnnoDialog.py")
TAG_CHECKER = os.path.join(GUI_DIR, "TagCheckerDialog.py")
UTILITY_MODULES = {
    "CopyAnnotationDialog",
    "TagCheckerDialog",
    "UpperAll",
    "RenumberAlongSpline",
}


def _tree(path):
    with open(path, encoding="utf-8-sig") as source:
        return ast.parse(source.read(), filename=path)


def _startup_imports(tree):
    """Yield imports executed at module load, including imports inside guards."""
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        pending.extend(ast.iter_child_nodes(node))


class ManaAnnoImportSafety(unittest.TestCase):
    def test_no_bare_utility_imports(self):
        """Utilities must always load through their canonical package name."""
        offenders = []
        for node in ast.walk(_tree(MANA_ANNO)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[-1] in UTILITY_MODULES:
                        offenders.append((node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[-1] in UTILITY_MODULES:
                    offenders.append((node.lineno, node.module))
                for alias in node.names:
                    if not node.module and alias.name in UTILITY_MODULES:
                        offenders.append((node.lineno, alias.name))
        self.assertEqual(offenders, [])

    def test_utilities_are_not_imported_at_startup(self):
        """One broken optional utility must not prevent ManaAnno from opening."""
        offenders = []
        for node in _startup_imports(_tree(MANA_ANNO)):
            names = [alias.name.split(".")[-1] for alias in node.names]
            module_name = node.module.split(".")[-1] if isinstance(node, ast.ImportFrom) and node.module else None
            if UTILITY_MODULES.intersection(names) or module_name in UTILITY_MODULES:
                offenders.append((node.lineno, module_name, names))
        self.assertEqual(offenders, [])

    def test_tag_checker_explicitly_imports_clr_interfaces(self):
        """PythonNet wildcard imports do not reliably expose CLR interfaces."""
        imported = set()
        for node in _tree(TAG_CHECKER).body:
            if isinstance(node, ast.ImportFrom) and node.module == "Autodesk.Revit.DB":
                imported.update(alias.name for alias in node.names if alias.name != "*")
        self.assertTrue(
            {"IFailuresPreprocessor", "FailureProcessingResult"}.issubset(imported),
            "TagChecker must explicitly import its CLR interface and result enum",
        )

    def test_warning_swallower_has_stable_namespace(self):
        classes = {
            node.name: node
            for node in _tree(TAG_CHECKER).body
            if isinstance(node, ast.ClassDef)
        }
        warning_swallower = classes.get("WarningSwallower")
        self.assertIsNotNone(warning_swallower)
        namespace = None
        for node in warning_swallower.body:
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__namespace__" for target in node.targets)
                and isinstance(node.value, ast.Constant)
            ):
                namespace = node.value.value
        self.assertEqual(namespace, "T3Lab.TagChecker")


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
