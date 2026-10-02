"""Auto Join keeps an element that lies inside another one visible.

Run: python3 dev/test_auto_join_embedded.py
Executes the shipped join_service functions against a simulated join API: the
fake tracks which element cuts which, and an element that is cut by the element
it sits inside reports no volume left — the "element disappeared" symptom.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_service_transactions import FakeTransaction, STATUS


LIB = Path(__file__).resolve().parents[1] / 'T3Lab.extension' / 'lib'
SERVICE = LIB / 'Services' / 'join_service.py'
NAMES = {'run_join', '_commit_join', 'EmbedCheck', 'EmbedState', '_box_inside',
         '_verdict_from_ratios', '_find_embedded', '_apply_join_order',
         '_rescue_swallowed', '_fill_stats'}
CONSTANTS = {'EMBEDDED_RATIO', 'BBOX_TOL', 'MIN_VOLUME', 'JOIN_CANCELLED_MESSAGE'}


def load_service(**deps):
    tree = ast.parse(SERVICE.read_text(encoding='utf-8'), filename=str(SERVICE))
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in NAMES:
            body.append(node)
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name) and node.targets[0].id in CONSTANTS):
            body.append(node)
    tree.body = body
    scope = dict(deps, TransactionStatus=STATUS)
    exec(compile(tree, str(SERVICE), 'exec'), scope)
    return SimpleNamespace(**scope)


def box(x0, y0, z0, x1, y1, z1):
    point = lambda x, y, z: SimpleNamespace(X=x, Y=y, Z=z)
    return SimpleNamespace(Min=point(x0, y0, z0), Max=point(x1, y1, z1))


class Element(SimpleNamespace):
    def get_BoundingBox(self, _view):
        return self.bb


class FakeJoins:
    """Join state: frozenset(pair ids) -> id of the cutting element.
    JoinGeometry lets the SECOND element cut, so rule order needs a switch."""

    def __init__(self, cutters=None):
        self.cutters = dict(cutters or {})

    def AreElementsJoined(self, doc, a, b):
        return frozenset((a.Id, b.Id)) in self.cutters

    def JoinGeometry(self, doc, a, b):
        self.cutters[frozenset((a.Id, b.Id))] = b.Id

    def IsCuttingElementInJoin(self, doc, a, b):
        return self.cutters[frozenset((a.Id, b.Id))] == a.Id

    def SwitchJoinOrder(self, doc, a, b):
        key = frozenset((a.Id, b.Id))
        self.cutters[key] = a.Id if self.cutters[key] == b.Id else b.Id

    def UnjoinGeometry(self, doc, a, b):
        del self.cutters[frozenset((a.Id, b.Id))]

    def cutter(self, a, b):
        return self.cutters[frozenset((a.Id, b.Id))]


class EmbeddedJoinTests(unittest.TestCase):
    def setUp(self):
        # A 200 mm deep beam lying fully inside a 300 mm slab.
        self.floor = Element(Id=1, bb=box(0, 0, 0, 10, 10, 1))
        self.beam = Element(Id=2, bb=box(2, 4, 0.2, 8, 5, 0.8))
        self.joins = FakeJoins()
        self.ratios = {(2, 1): 1.0, (1, 2): 0.05}
        self.inside = {2: 1}                        # beam sits inside floor
        self.base_volume = {1: 100.0, 2: 3.0}
        self.doc = SimpleNamespace(ActiveView=SimpleNamespace(Id=10), Regenerate=Mock())
        self.rules = [{'priority': 'Floors', 'join_with': 'Structural Framing'}]
        self.transaction = FakeTransaction()

    def volume(self, el):
        """Swallowed: cut by the element it sits inside."""
        container = self.inside.get(el.Id)
        key = frozenset((el.Id, container)) if container else None
        if key in self.joins.cutters and self.joins.cutters[key] == container:
            return 0.0
        return self.base_volume[el.Id]

    def ratio(self, inner, outer):
        value = self.ratios.get((inner.Id, outer.Id), 0.0)
        if isinstance(value, Exception):
            raise value
        return value

    def run_service(self, **kwargs):
        service = load_service(
            Transaction=Mock(return_value=self.transaction),
            JoinFailuresPreprocessor=Mock(),
            JOINABLE_CATEGORIES={'Floors': 1, 'Structural Framing': 2},
            _collect_elements=Mock(return_value=[self.floor]),
            _get_intersecting_elements=Mock(return_value=[self.beam]),
            _same_design_option=Mock(return_value=True),
            eid_value=lambda value: value, JoinGeometryUtils=self.joins, logger=Mock(),
            _solid_volume=self.volume, _inside_ratio=Mock(side_effect=self.ratio))
        self.service = service
        stats = {}
        options = dict(switch_order=True, protect_embedded=True, stats=stats)
        options.update(kwargs)
        result = service.run_join(self.rules, doc=self.doc, uidoc=Mock(), **options)
        return result, stats

    # ── pure helpers ────────────────────────────────────────────────────────
    def test_box_inside_uses_tolerance_and_treats_missing_box_as_unknown(self):
        service = load_service(eid_value=lambda v: v)
        outer = ((0, 0, 0), (10, 10, 1))
        self.assertTrue(service._box_inside(((1, 1, 0.01), (9, 9, 1.01)), outer))
        self.assertFalse(service._box_inside(((1, 1, 0), (9, 9, 1.5)), outer))
        self.assertTrue(service._box_inside(None, outer))

    def test_verdict_from_ratios(self):
        service = load_service(eid_value=lambda v: v)
        a, b = object(), object()
        self.assertIs(service._verdict_from_ratios(a, b, 0.99, 0.1).inner, a)
        self.assertIs(service._verdict_from_ratios(a, b, 0.1, 1.0).inner, b)
        self.assertTrue(service._verdict_from_ratios(a, b, 1.0, 0.99).duplicate)
        verdict = service._verdict_from_ratios(a, b, 0.5, 0.5)
        self.assertIsNone(verdict.inner)
        self.assertFalse(verdict.duplicate)

    # ── run_join ────────────────────────────────────────────────────────────
    def test_rule_order_alone_swallows_the_embedded_beam(self):
        (joined, _, errors, message), _ = self.run_service(protect_embedded=False)
        self.assertEqual((joined, errors, message), (1, 0, None))
        self.assertEqual(self.joins.cutter(self.floor, self.beam), self.floor.Id)
        self.assertEqual(self.volume(self.beam), 0.0)

    def test_embedded_beam_cuts_the_floor_against_rule_order(self):
        (joined, _, errors, message), stats = self.run_service()
        self.assertEqual((joined, errors, message), (1, 0, None))
        self.assertEqual(self.joins.cutter(self.floor, self.beam), self.beam.Id)
        self.assertEqual(self.volume(self.beam), 3.0)
        self.assertEqual(stats, {'protected': 1, 'duplicates': 0})
        self.doc.Regenerate.assert_not_called()

    def test_already_swallowed_beam_is_brought_back(self):
        self.joins.cutters[frozenset((1, 2))] = self.floor.Id
        (joined, skipped, errors, _), stats = self.run_service()
        self.assertEqual((joined, skipped, errors), (0, 1, 0))
        self.assertEqual(self.joins.cutter(self.floor, self.beam), self.beam.Id)
        self.assertEqual(stats['protected'], 1)

    def test_switch_that_would_swallow_an_existing_cutter_is_undone(self):
        self.joins.cutters[frozenset((1, 2))] = self.beam.Id   # good state already
        (_, skipped, errors, _), stats = self.run_service()
        self.assertEqual((skipped, errors), (1, 0))
        self.assertEqual(self.joins.cutter(self.floor, self.beam), self.beam.Id)
        self.doc.Regenerate.assert_called_once()
        self.assertEqual(stats['protected'], 1)

    def test_switch_on_a_joined_pair_that_is_not_embedded_stays(self):
        self.beam.bb = box(2, 4, 0.2, 8, 5, 3.0)              # pokes out of the slab
        self.inside = {}
        self.joins.cutters[frozenset((1, 2))] = self.beam.Id
        _, stats = self.run_service()
        self.assertEqual(self.joins.cutter(self.floor, self.beam), self.floor.Id)
        self.doc.Regenerate.assert_not_called()
        self.assertEqual(stats['protected'], 0)

    def test_not_nested_boxes_skip_the_geometry_check(self):
        self.beam.bb = box(2, 4, 0.2, 8, 5, 3.0)
        self.inside = {}
        _, stats = self.run_service()
        self.assertEqual(self.joins.cutter(self.floor, self.beam), self.floor.Id)
        self.service._inside_ratio.assert_not_called()
        self.assertEqual(stats['protected'], 0)

    def test_exact_overlap_is_not_joined(self):
        self.ratios = {(2, 1): 1.0, (1, 2): 1.0}
        self.beam.bb = self.floor.bb
        (joined, skipped, _, _), stats = self.run_service()
        self.assertEqual((joined, skipped), (0, 1))
        self.assertEqual(self.joins.cutters, {})
        self.assertEqual(stats, {'protected': 0, 'duplicates': 1})

    def test_failed_boolean_falls_back_to_measuring_after_the_join(self):
        self.ratios = {(2, 1): RuntimeError('boolean failed'), (1, 2): 0.0}
        (joined, _, errors, _), stats = self.run_service()
        self.assertEqual((joined, errors), (1, 0))
        self.doc.Regenerate.assert_called_once()
        self.assertEqual(self.joins.cutter(self.floor, self.beam), self.beam.Id)
        self.assertEqual(stats['protected'], 1)

    def test_pair_met_again_by_a_reverse_rule_keeps_the_embedded_cutter(self):
        self.rules.append({'priority': 'Structural Framing', 'join_with': 'Floors'})
        _, stats = self.run_service()
        self.assertEqual(self.joins.cutter(self.floor, self.beam), self.beam.Id)
        self.assertEqual(stats['protected'], 1)

    def test_rollback_reports_nothing_protected(self):
        self.transaction.outcome = STATUS.RolledBack
        (joined, _, _, message), stats = self.run_service()
        self.assertEqual(joined, 0)
        self.assertIn('RolledBack', message)
        self.assertEqual(stats['protected'], 0)

    def test_unjoin_ignores_protection(self):
        self.joins.cutters[frozenset((1, 2))] = self.floor.Id
        (joined, _, _, _), stats = self.run_service(mode='Unjoin')
        self.assertEqual(joined, 1)
        self.assertEqual(self.joins.cutters, {})
        self.assertEqual(stats, {'protected': 0, 'duplicates': 0})


if __name__ == '__main__':
    unittest.main()
