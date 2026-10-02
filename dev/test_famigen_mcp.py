"""FamiGen MCP tools: registry/threading/drift locks (no Revit needed).

Run:  python3 dev/test_famigen_mcp.py

core.server cannot be imported headlessly, so like dev/test_tool_registry.py
this reads the registry literal and the dispatch source with ast.
"""
import ast
import importlib.util
import io
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, 'T3Lab.extension', 'lib')
SERVER = os.path.join(LIB, 'core', 'server.py')
DIALOG = os.path.join(LIB, 'GUI', 'FamiGenDialog.py')

spec = importlib.util.spec_from_file_location(
    'family_schema', os.path.join(LIB, 'Intelligence', 'family_schema.py'))
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)

TOOLS = ('famigen_get_schema', 'famigen_propose_family', 'famigen_create_family')


def _read(path):
    with io.open(path, encoding='utf-8') as handle:
        return handle.read()


SRC = _read(SERVER)
TREE = ast.parse(SRC)


def _registry():
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == '_register_default_tools':
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Dict):
                    return ast.literal_eval(stmt.value)
    raise AssertionError('registry not found')


def _class_set(attr):
    for node in ast.walk(TREE):
        if isinstance(node, ast.ClassDef) and node.name == 'T3LabAIServer':
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == attr for t in stmt.targets):
                    return set(ast.literal_eval(stmt.value.args[0]))
    raise AssertionError(attr)


def _branch(name):
    start = SRC.index("elif tool_name == '{}':".format(name))
    nxt = re.compile(r"^        elif tool_name == ", re.M).search(SRC, start + 10)
    return SRC[start:nxt.start()]


def _module_set(path, name):
    tree = ast.parse(_read(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                                for t in node.targets):
            value = node.value
            if isinstance(value, ast.Call):
                return set(ast.literal_eval(value.args[0]))
            if isinstance(value, ast.BinOp):
                return set(ast.literal_eval(value.right.args[0]))
    raise AssertionError(name)


REGISTRY = _registry()


class FamiGenMcpTests(unittest.TestCase):
    def test_registered_with_schemas(self):
        for name in TOOLS:
            self.assertIn(name, REGISTRY)
        self.assertEqual(REGISTRY['famigen_propose_family']['inputSchema']['required'], ['schema'])

    def test_category_enum_matches_the_contract(self):
        enum = REGISTRY['famigen_get_schema']['inputSchema']['properties']['category']['enum']
        self.assertEqual(enum, list(contract.SUPPORTED_CATEGORIES))

    def test_threading_and_side_effect_classification(self):
        write = _class_set('_WRITE_TOOLS')
        docless = _class_set('_DOCLESS_TOOLS')
        self.assertIn('famigen_propose_family', write)      # WPF window: UI thread only
        self.assertIn('famigen_create_family', write)       # transactions + SaveAs
        self.assertNotIn('famigen_get_schema', write)
        self.assertIn('famigen_get_schema', docless)
        read_only = _module_set(os.path.join(LIB, 'Intelligence', 'tool_schema.py'),
                                'READ_ONLY_TOOL_NAMES')
        self.assertIn('famigen_propose_family', read_only)  # model untouched
        self.assertNotIn('famigen_create_family', read_only)
        modeling = _module_set(os.path.join(LIB, 'Intelligence', 'agents', 'specialists.py'),
                               'MODELING_TOOLS')
        self.assertTrue(set(TOOLS) <= modeling)

    def test_propose_never_blocks_on_the_user(self):
        branch = _branch('famigen_propose_family')
        self.assertIn('open_review', branch)
        self.assertNotIn('ShowDialog', branch)
        review = _read(os.path.join(LIB, 'FamilyGen', 'review.py'))
        self.assertIn('show_proposal(', review)
        dialog = _read(DIALOG)
        body = dialog[dialog.index('def show_proposal('):]
        self.assertIn('window.Show()', body)
        self.assertNotIn('.ShowDialog(', body)
        self.assertIn('modeless=True', body)
        # invalid schemas return errors and open nothing
        self.assertLess(branch.index("'window': 'not_opened'"), branch.index('ProposalStore().save'))

    def test_create_uses_the_shared_builder(self):
        branch = _branch('famigen_create_family')
        self.assertIn('create_family(', branch)
        self.assertIn('ProposalStore().load', branch)
        self.assertIn('FamilyBuildError', branch)
        dialog = _read(DIALOG)
        self.assertIn('family_builder.create_family(', dialog)
        self.assertNotIn('def _generate_json_family', dialog)   # one builder, not two

    def test_modeless_create_goes_through_external_event(self):
        dialog = _read(DIALOG)
        create = dialog[dialog.index('    def create_clicked('):dialog.index('    def _create_family_impl(')]
        self.assertIn('self._run_in_revit(', create)
        self.assertNotIn('Transaction(', create)
        self.assertNotIn('NewFamilyDocument', create)
        self.assertIn('__namespace__ = "T3Lab.FamiGenAction"', dialog)


if __name__ == '__main__':
    unittest.main()
