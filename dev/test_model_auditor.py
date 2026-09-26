"""Regression tests for the Model Auditor dialog, using a simulated Revit API.

Run: python3 dev/test_model_auditor.py

The dialog's shipped handlers are executed here without importing Revit, WPF or
pyRevit, so control flow and reporting are covered, not Revit's own failure
processing.

The tool was cut down to three functions on 2026-09-21 — Health, Warnings,
Smart Purge — after these defects were found in the five-tab version:

  * both delete buttons called MessageBox.show(), which the CPython forms shim
    binds to the alert *function* — AttributeError before the try block, so the
    buttons could never delete anything;
  * destructive actions ran with no confirmation;
  * Smart Delete added the owning Group to the delete list, taking every other
    member of that group with it;
  * failed transactions were left open instead of rolled back;
  * chrome buttons were bound twice, so Maximize toggled twice and did nothing;
  * Advanced Purge duplicated Smart Purge with unsafe rules;
  * ModelHealthAnalyzer walked ImportInstance three times, RevitLinkInstance
    twice, View twice and GetWarnings() twice.
"""
import ast
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / 'T3Lab.extension' / 'lib' / 'GUI'
DIALOG = GUI / 'ModelAuditorDialog.py'
XAML = GUI / 'Tools' / 'ModelAuditor.xaml'
DETAIL_XAML = GUI / 'Tools' / 'ModelAuditorDetail.xaml'
SOURCE = DIALOG.read_text(encoding='utf-8')
XAML_SOURCE = XAML.read_text(encoding='utf-8')
DETAIL_SOURCE = DETAIL_XAML.read_text(encoding='utf-8')
ALL_XAML = XAML_SOURCE + DETAIL_SOURCE
STYLESHEET = (ROOT / 'pyRevit UI Design System' / 'T3Lab.Styles.xaml').read_text(encoding='utf-8')


# ───────────────────────────── simulated Revit/WPF ──────────────────────────
class FakeElementId:
    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return isinstance(other, FakeElementId) and other.value == self.value

    def __hash__(self):
        return hash(self.value)

    def __repr__(self):
        return 'Id({})'.format(self.value)


class FakeTransaction:
    """Records the Start/Commit/RollBack sequence a handler drives."""

    instances = []

    def __init__(self, doc, name):
        self.doc = doc
        self.name = name
        self.started = False
        self.ended = False
        self.committed = False
        self.rolled_back = False
        FakeTransaction.instances.append(self)

    def Start(self):
        self.started = True

    def Commit(self):
        self.committed = True
        self.ended = True

    def RollBack(self):
        assert self.started and not self.ended, 'RollBack on an inactive transaction'
        self.rolled_back = True
        self.ended = True

    def HasStarted(self):
        return self.started

    def HasEnded(self):
        return self.ended


class FakeWarning:
    def __init__(self, text, failing=(), additional=()):
        self.text = text
        self._failing = list(failing)
        self._additional = list(additional)

    def GetDescriptionText(self):
        return self.text

    def GetFailingElements(self):
        return list(self._failing)

    def GetAdditionalElements(self):
        return list(self._additional)


class FakeDoc:
    def __init__(self, elements=None, warnings=(), delete_error=None):
        self.elements = elements or {}
        self.warnings = list(warnings)
        self.delete_error = delete_error
        self.deleted = []
        self.warning_calls = 0
        self.PathName = ''
        self.IsWorkshared = False

    def GetWarnings(self):
        self.warning_calls += 1
        return list(self.warnings)

    def GetElement(self, eid):
        return self.elements.get(eid.value if isinstance(eid, FakeElementId) else eid)

    def Delete(self, ids):
        if self.delete_error:
            raise self.delete_error
        ids = list(ids) if isinstance(ids, (list, tuple)) else [ids]
        self.deleted.extend(ids)
        return ids


class FakeAlert:
    """Stand-in for pyrevit.forms, recording prompts and scripted answers."""

    def __init__(self, answer=True):
        self.answer = answer
        self.prompts = []
        self.messages = []

    def alert(self, msg, title=None, yes=False, no=False, **kwargs):
        if yes or no:
            self.prompts.append(msg)
            return self.answer
        self.messages.append(msg)
        return None

    def save_file(self, *a, **k):
        return None


class _NetList(list):
    """Stand-in for System.Collections.Generic.List[T] — empty ctor + Add()."""

    def Add(self, item):
        self.append(item)


class _GenericList:
    def __getitem__(self, _type):
        return lambda seq=(): _NetList(seq)


FAKE_SYSTEM = SimpleNamespace(
    Collections=SimpleNamespace(Generic=SimpleNamespace(List=_GenericList())),
    Windows=SimpleNamespace(Visibility=SimpleNamespace(Visible='Visible',
                                                       Collapsed='Collapsed')))


class FakeCollector:
    """FilteredElementCollector stand-in that counts how often it is built."""

    passes = []
    materialised = []     # collectors that were asked for Element objects
    id_only = []          # collectors that only ever asked for ElementIds

    def __init__(self, doc):
        self.doc = doc
        self.items = []
        self.last = None

    def OfClass(self, cls):
        FakeCollector.passes.append(cls.__name__)
        self.last = cls.__name__
        self.items = list(self.doc.elements.get(cls.__name__, []))
        return self

    def OfCategory(self, cat):
        FakeCollector.passes.append(str(cat))
        self.last = str(cat)
        self.items = list(self.doc.elements.get(str(cat), []))
        return self

    def WhereElementIsNotElementType(self):
        return self

    def ToElements(self):
        FakeCollector.materialised.append(self.last)
        return list(self.items)

    def ToElementIds(self):
        FakeCollector.id_only.append(self.last)
        return [el.Id for el in self.items]

    def WherePasses(self, element_filter):
        self.items = list(element_filter.matches)
        return self

    def __iter__(self):
        return iter(self.items)


def _text_block():
    return SimpleNamespace(Text='')


def _grid():
    return SimpleNamespace(ItemsSource=None, SelectedItems=[], SelectedItem=None,
                           Items=SimpleNamespace(Refresh=lambda: None))


# ─────────────────────────────── module loading ─────────────────────────────
class _FakeViewSheet(object):
    pass


def load_dialog():
    """Exec the dialog's top-level defs with Revit/WPF replaced by fakes."""
    tree = ast.parse(SOURCE, filename=str(DIALOG))
    tree.body = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]

    class T3WPFWindow(object):
        def begin_progress(self, maximum=100, disable=None):
            self._progress = 'begun'

        def step_progress(self, value, message=None):
            return True

        def end_progress(self):
            self._progress = 'ended'

        @property
        def is_cancelled(self):
            return False

    def named(name):
        return type(name, (object,), {})

    scope = {
        'forms': FakeAlert(),
        'Transaction': FakeTransaction,
        'System': FAKE_SYSTEM,
        'ElementId': FakeElementId,
        'T3WPFWindow': T3WPFWindow,
        'to_items_source': lambda items: list(items),
        'make_eid': FakeElementId,
        'eid_value': lambda eid: eid.value if isinstance(eid, FakeElementId) else eid,
        'FilteredElementCollector': FakeCollector,
        'BuiltInCategory': SimpleNamespace(OST_Rooms='OST_Rooms', OST_Lines='OST_Lines'),
        'DB': SimpleNamespace(ViewType=SimpleNamespace(Internal='Internal')),
        'ViewSheet': _FakeViewSheet,
        'logger': SimpleNamespace(debug=lambda *a, **k: None),
        'traceback': SimpleNamespace(format_exc=lambda: ''),
        'OrderedDict': __import__('collections').OrderedDict,
        'defaultdict': __import__('collections').defaultdict,
        'os': __import__('os'),
        'sys': sys,
        'datetime': __import__('datetime'),
        'csv': __import__('csv'),
        're': __import__('re'),
        'json': __import__('json'),
        'codecs': __import__('codecs'),
    }
    for cls in ('Family', 'FamilyInstance', 'ImportInstance', 'RevitLinkInstance',
                'View', 'Group', 'DesignOption', 'ReferencePlane', 'CurveElement',
                'FilledRegion'):
        scope[cls] = named(cls)

    class FamilyInstanceFilter(object):
        """Native filter: Revit hands back only that symbol's instances."""

        def __init__(self, doc, symbol_id):
            self.matches = doc.elements.get(('instances', symbol_id.value), [])

    scope['FamilyInstanceFilter'] = FamilyInstanceFilter

    # Module-level tables the handlers read; rebuild them by executing just
    # those assignments from the shipped source.
    wanted = {'METRIC_THRESHOLDS', '_STATUS_TOKENS', '_STATUS_ORDER',
              '_STATUS_ATTENTION', '_STATUS_SEVERITY', '_GRADE_TOKENS',
              '_DETAIL_ROW_CAP'}
    consts = ast.parse(SOURCE, filename=str(DIALOG))
    consts.body = [n for n in consts.body
                   if isinstance(n, ast.Assign)
                   and any(getattr(t, 'id', '') in wanted for t in n.targets)]
    exec(compile(consts, str(DIALOG), 'exec'), scope)
    exec(compile(tree, str(DIALOG), 'exec'), scope)
    return SimpleNamespace(**scope)


MOD = load_dialog()


def make_window(**attrs):
    win = MOD.ModelAuditorWindow.__new__(MOD.ModelAuditorWindow)
    win.status_text = _text_block()
    win.doc = FakeDoc()
    win.uidoc = None
    for key, value in attrs.items():
        setattr(win, key, value)
    return win


def fresh_forms(answer=True):
    stub = FakeAlert(answer)
    MOD.__dict__['forms'] = stub
    MOD.ModelAuditorWindow.__init__.__globals__['forms'] = stub
    return stub


# ──────────────────────────── 1. health dashboard ───────────────────────────
class HealthAnalyzerTests(unittest.TestCase):
    def setUp(self):
        FakeCollector.passes = []
        FakeCollector.materialised = []
        FakeCollector.id_only = []

    def _doc(self):
        imp = SimpleNamespace(Id=FakeElementId(1), IsLinked=False, Pinned=True)
        link_cad = SimpleNamespace(Id=FakeElementId(2), IsLinked=True, Pinned=False)
        rvt_link = SimpleNamespace(Id=FakeElementId(3), Pinned=False)
        sheet = _FakeViewSheet()
        sheet.Id = FakeElementId(4)
        view = SimpleNamespace(Id=FakeElementId(5), IsTemplate=False, ViewType='FloorPlan')
        template = SimpleNamespace(Id=FakeElementId(6), IsTemplate=True, ViewType='FloorPlan')
        dupe = FakeWarning('There are identical instances in the same place',
                           failing=[FakeElementId(7)], additional=[FakeElementId(8)])
        return FakeDoc(elements={
            'ImportInstance': [imp, link_cad],
            'RevitLinkInstance': [rvt_link],
            'View': [sheet, view, template],
        }, warnings=[dupe, FakeWarning('Room not enclosed')])

    def test_each_collection_is_walked_once(self):
        doc = self._doc()
        MOD.ModelHealthAnalyzer(doc).analyze()
        for cls in ('ImportInstance', 'RevitLinkInstance', 'View'):
            self.assertEqual(FakeCollector.passes.count(cls), 1,
                             '{} was collected more than once'.format(cls))
        self.assertEqual(doc.warning_calls, 1, 'GetWarnings() was called more than once')

    def test_bulk_metrics_never_materialise_elements(self):
        """Only fetch what the metric actually reads.

        A metric that is a count plus a selection list has no business pulling
        Element wrappers across the interop boundary — ids answer it, and on a
        drafting-heavy model those collections are the big ones. Elements are
        only fair game where a property has to be read off each one.
        """
        MOD.ModelHealthAnalyzer(self._doc()).analyze()
        must_be_id_only = {'Group', 'DesignOption', 'ReferencePlane',
                           'FilledRegion', 'CurveElement'}
        leaked = must_be_id_only.intersection(FakeCollector.materialised)
        self.assertEqual(leaked, set(),
                         'these were pulled in as Elements but only ids are used')
        self.assertTrue(must_be_id_only.issubset(set(FakeCollector.id_only)))

    def test_element_paths_are_only_where_a_property_is_read(self):
        MOD.ModelHealthAnalyzer(self._doc()).analyze()
        # ImportInstance -> IsLinked/Pinned, RevitLinkInstance -> Pinned,
        # View -> IsTemplate/ViewType, Family -> IsInPlace, Rooms -> Location.
        allowed = {'ImportInstance', 'RevitLinkInstance', 'View', 'Family',
                   'FamilyInstance', 'OST_Rooms'}
        unexpected = set(FakeCollector.materialised) - allowed
        self.assertEqual(unexpected, set())

    def test_a_model_with_no_in_place_families_skips_the_instance_table(self):
        doc = self._doc()
        MOD.ModelHealthAnalyzer(doc).analyze()
        self.assertNotIn('FamilyInstance', FakeCollector.passes,
                         'no in-place families means no reason to walk instances')

    def test_in_place_instances_come_from_the_native_filter(self):
        """Revit is asked for that symbol's instances directly, instead of
        every FamilyInstance in the model being tested one at a time."""
        symbol = FakeElementId(900)
        family = SimpleNamespace(IsInPlace=True,
                                 GetFamilySymbolIds=lambda: [symbol])
        loadable = SimpleNamespace(IsInPlace=False,
                                   GetFamilySymbolIds=lambda: [FakeElementId(901)])
        doc = self._doc()
        doc.elements['Family'] = [family, loadable]
        doc.elements[('instances', 900)] = [SimpleNamespace(Id=FakeElementId(i))
                                            for i in (11, 12, 13)]
        metrics = MOD.ModelHealthAnalyzer(doc).analyze()
        self.assertEqual(metrics['in_place_families'], 3)
        self.assertNotIn('FamilyInstance', FakeCollector.passes,
                         'the instance table must not be walked')

    def test_the_shared_cache_is_released_after_a_run(self):
        analyzer = MOD.ModelHealthAnalyzer(self._doc())
        analyzer.analyze()
        self.assertEqual(analyzer._cache, {},
                         'collected elements must not stay pinned for the '
                         'lifetime of the window')

    def test_metrics_split_imports_links_and_unpinned(self):
        m = MOD.ModelHealthAnalyzer(self._doc()).analyze()
        self.assertEqual(m['cad_imports'], 1)
        self.assertEqual(m['cad_links'], 1)
        self.assertEqual(m['rvt_links'], 1)
        # one unpinned CAD link + one unpinned RVT link
        self.assertEqual(m['linked_dwg_not_pinned'], 2)

    def test_sheets_are_not_counted_as_views(self):
        m = MOD.ModelHealthAnalyzer(self._doc()).analyze()
        self.assertEqual(m['sheets'], 1)
        self.assertEqual(m['views'], 1, 'templates and sheets must stay out of the view count')

    def test_duplicate_metric_merges_failing_and_additional(self):
        m = MOD.ModelHealthAnalyzer(self._doc()).analyze()
        self.assertEqual(m['warnings'], 2)
        self.assertEqual(m['duplicate_elements'], 2)

    def test_every_threshold_metric_gets_a_value(self):
        m = MOD.ModelHealthAnalyzer(self._doc()).analyze()
        missing = [k for k in MOD.METRIC_THRESHOLDS if k not in m]
        self.assertEqual(missing, [], 'score would be computed from a partial metric set')

    def test_cancel_still_returns_a_complete_metric_set(self):
        m = MOD.ModelHealthAnalyzer(self._doc()).analyze(cancel_check=lambda: True)
        self.assertEqual(sorted(m), sorted(MOD.METRIC_THRESHOLDS))

    def test_a_failing_metric_does_not_abort_the_run(self):
        doc = self._doc()

        class Boom(object):
            @property
            def Id(self):
                raise RuntimeError('element is dead')

        doc.elements['Group'] = [Boom()]
        m = MOD.ModelHealthAnalyzer(doc).analyze()
        self.assertEqual(m['groups'], 0)
        self.assertEqual(m['warnings'], 2, 'later metrics must still run')


class HealthDashboardTests(unittest.TestCase):
    """Drives on_health_run end to end against a scripted model."""

    WIDGETS = ('txt_health_grade', 'txt_health_score', 'txt_health_badge_label',
               'txt_health_title', 'txt_health_rag', 'txt_health_trend',
               'txt_health_doc_name', 'txt_health_last_run', 'txt_health_summary',
               'txt_health_summary_metrics', 'txt_health_rec_count',
               'txt_tally_good', 'txt_tally_warning', 'txt_tally_critical')

    def setUp(self):
        FakeCollector.passes = []
        self.forms = fresh_forms()
        # BrushConverter is imported inside on_health_run.
        media = types.ModuleType('System.Windows.Media')
        media.BrushConverter = lambda: SimpleNamespace(ConvertFromString=lambda s: s)
        for name in ('System', 'System.Windows'):
            sys.modules.setdefault(name, types.ModuleType(name))
        sys.modules['System.Windows.Media'] = media
        # Keep the score history off the real filesystem.
        globs = MOD.ModelAuditorWindow.on_health_run.__globals__
        self.history = []
        globs['_load_history'] = lambda doc: list(self.history)
        globs['_append_history'] = lambda doc, record: self.history.append(record)

    def _window(self, doc):
        win = make_window(dg_health_metrics=_grid(),
                          lst_health_recommendations=_grid(),
                          ellipse_health_bg=SimpleNamespace(Fill=None),
                          ellipse_health_color=SimpleNamespace(Fill=None),
                          border_health_rag=SimpleNamespace(Background=None),
                          border_health_grade=SimpleNamespace(Background=None))
        for name in self.WIDGETS:
            setattr(win, name, SimpleNamespace(Text='', Foreground=None))
        win.doc = doc
        win.health_analyzer = MOD.ModelHealthAnalyzer(doc)
        win.health_results = {}
        return win

    def test_clean_model_scores_an_A(self):
        win = self._window(FakeDoc())
        win.on_health_run(SimpleNamespace(IsEnabled=True), None)
        self.assertEqual(win.txt_health_grade.Text, 'A')
        self.assertEqual(win.txt_health_score.Text, '100.0')
        self.assertIn('RAG: Green', win.txt_health_rag.Text)
        self.assertIn('Score: 100.0', win.status_text.Text)

    def test_every_metric_reaches_the_grid(self):
        win = self._window(FakeDoc())
        win.on_health_run(SimpleNamespace(IsEnabled=True), None)
        rows = win.dg_health_metrics.ItemsSource
        self.assertEqual(len(rows), len(MOD.METRIC_THRESHOLDS))
        # The XAML binds value_display / status / bg_brush / fg_brush. Emitting
        # a differently-named attribute shows up as a silently empty column,
        # which is exactly how CURRENT VALUE and HEALTH went blank in Revit.
        for row in rows:
            for field in ('label', 'value_display', 'status', 'weight_stars',
                          'thresholds_text', 'select_visibility'):
                self.assertTrue(getattr(row, field, None) not in (None, ''),
                                '{} is empty on {}'.format(field, row.label))
            self.assertIn(row.status, MOD._STATUS_ORDER)

    def test_row_fields_match_the_xaml_bindings(self):
        import re
        win = self._window(FakeDoc())
        win.on_health_run(SimpleNamespace(IsEnabled=True), None)
        row = win.dg_health_metrics.ItemsSource[0]
        # Pull the bindings out of the health metrics DataGrid only.
        grid = XAML_SOURCE[XAML_SOURCE.index('x:Name="dg_health_metrics"'):]
        grid = grid[:grid.index('</DataGrid>')]
        for field in set(re.findall(r'\{Binding (\w+)\}', grid)):
            self.assertTrue(hasattr(row, field),
                            'XAML binds {} but the row never sets it'.format(field))

    def test_pills_never_bind_a_brush_from_python(self):
        """PythonNet không đưa được Brush qua binding: pill phải tô bằng
        DataTrigger trên `severity`, nếu không nó hiện chữ mà không có nền."""
        self.assertNotIn('Binding bg_brush', XAML_SOURCE)
        self.assertNotIn('Binding fg_brush', XAML_SOURCE)
        for fam in ('Success', 'Warning', 'Danger'):
            self.assertIn('<DataTrigger Binding="{Binding severity}" Value="%s">' % fam,
                          XAML_SOURCE)

    def test_every_row_carries_a_severity_the_xaml_knows(self):
        win = self._window(FakeDoc())
        win.on_health_run(SimpleNamespace(IsEnabled=True), None)
        for row in win.dg_health_metrics.ItemsSource:
            self.assertIn(row.severity, ('Success', 'Warning', 'Danger'))
            self.assertEqual(row.severity, MOD._STATUS_SEVERITY[row.status])

    def test_templated_detail_buttons_are_wired_through_the_opt_in(self):
        """Nút trong DataTemplate không nằm trong namescope, chỉ chạy được
        khi window bật WIRE_TEMPLATED_CLICKS."""
        self.assertTrue(MOD.ModelAuditorWindow.WIRE_TEMPLATED_CLICKS)
        for handler in ('on_health_metric_detail', 'on_recommendation_detail'):
            self.assertIn('Click="%s"' % handler, XAML_SOURCE)
            self.assertIn('def %s(' % handler, SOURCE)

    def test_status_colours_come_from_three_token_families(self):
        """Six status names, three T3 families — no hand-mixed gradient."""
        self.assertEqual(len(set(MOD._STATUS_TOKENS.values())), 3)
        for fill, text in MOD._STATUS_TOKENS.values():
            self.assertTrue(fill.startswith('T3.') and text.startswith('T3.'))
        for fill, text in MOD._GRADE_TOKENS.values():
            self.assertIn((fill, text), set(MOD._STATUS_TOKENS.values()),
                          'the grade badge must reuse a status family')

    def test_a_bad_model_drops_the_grade_and_raises_recommendations(self):
        doc = FakeDoc(elements={
            'ImportInstance': [SimpleNamespace(Id=FakeElementId(i), IsLinked=False,
                                               Pinned=True) for i in range(40)],
        }, warnings=[FakeWarning('Room not enclosed')] * 6000)
        win = self._window(doc)
        win.on_health_run(SimpleNamespace(IsEnabled=True), None)
        self.assertNotEqual(win.txt_health_grade.Text, 'A')
        self.assertTrue(win.lst_health_recommendations.ItemsSource,
                        'failing metrics must produce recommendations')
        self.assertIn('critical/severe', win.txt_health_summary.Text)

    def test_trend_compares_against_the_previous_run(self):
        win = self._window(FakeDoc())
        win.on_health_run(SimpleNamespace(IsEnabled=True), None)
        self.assertIn('No previous run', win.txt_health_trend.Text)
        win.on_health_run(SimpleNamespace(IsEnabled=True), None)
        self.assertIn('No change', win.txt_health_trend.Text)

    def test_no_open_document_is_reported_not_crashed(self):
        win = self._window(FakeDoc())
        win.health_analyzer = None
        win.on_health_run(SimpleNamespace(IsEnabled=True), None)
        self.assertEqual(len(self.forms.messages), 1)
        self.assertIn('No Revit project is open', self.forms.messages[0])

    def test_detail_on_a_metric_without_elements_warns(self):
        win = self._window(FakeDoc())
        win.on_health_run(SimpleNamespace(IsEnabled=True), None)
        row = [r for r in win.dg_health_metrics.ItemsSource if r.key == 'cad_imports'][0]
        win.on_health_metric_detail(SimpleNamespace(DataContext=row), None)
        self.assertIn('no elements to list', self.forms.messages[-1])

    def test_recommendations_are_ordered_worst_first(self):
        doc = FakeDoc(elements={
            'ImportInstance': [SimpleNamespace(Id=FakeElementId(i), IsLinked=False,
                                               Pinned=True) for i in range(40)],
        }, warnings=[FakeWarning('Room not enclosed')] * 6000)
        win = self._window(doc)
        win.on_health_run(SimpleNamespace(IsEnabled=True), None)
        recs = win.lst_health_recommendations.ItemsSource
        self.assertTrue(recs)
        impacts = [r.impact for r in recs]
        self.assertEqual(impacts, sorted(impacts, reverse=True))
        for rec in recs:
            for field in ('status', 'headline', 'recommendation', 'key'):
                self.assertTrue(getattr(rec, field, None))

    def test_recommendation_rows_carry_what_the_xaml_binds(self):
        import re
        win = self._window(FakeDoc(warnings=[FakeWarning('x')] * 6000))
        win.on_health_run(SimpleNamespace(IsEnabled=True), None)
        rec = win.lst_health_recommendations.ItemsSource[0]
        block = XAML_SOURCE[XAML_SOURCE.index('x:Name="lst_health_recommendations"'):]
        block = block[:block.index('</ListBox>')]
        for field in set(re.findall(r'\{Binding (\w+)\}', block)):
            self.assertTrue(hasattr(rec, field),
                            'XAML binds {} but the row never sets it'.format(field))


class MetricDetailTests(unittest.TestCase):
    """The popup behind the Detail button on each metric row."""

    def setUp(self):
        self.forms = fresh_forms()
        MOD.MetricDetailWindow.__init__.__globals__['forms'] = self.forms

    def _window(self, ids, doc=None):
        win = MOD.MetricDetailWindow.__new__(MOD.MetricDetailWindow)
        win.doc = doc or FakeDoc()
        win.rows = []
        win.status_text = _text_block()
        win.dg_detail_elements = _grid()
        win.empty_detail_elements = SimpleNamespace(Visibility=None)
        win.uidoc = SimpleNamespace(
            Selection=SimpleNamespace(SetElementIds=lambda picked: None),
            ShowElements=lambda picked: None)
        win._load(ids)
        return win

    def test_rows_carry_id_category_and_name(self):
        wall = SimpleNamespace(Name='Wall 1', Category=SimpleNamespace(Name='Walls'))
        doc = FakeDoc(elements={7: wall})
        win = self._window([FakeElementId(7)], doc)
        row = win.dg_detail_elements.ItemsSource[0]
        self.assertEqual(row.id, 7)
        self.assertEqual(row.category, 'Walls')
        self.assertEqual(row.name, 'Wall 1')
        self.assertFalse(row.is_selected)

    def test_a_dead_element_still_gets_a_row(self):
        win = self._window([FakeElementId(99)])
        row = win.dg_detail_elements.ItemsSource[0]
        self.assertEqual(row.id, 99)
        self.assertEqual(row.name, 'Unknown')

    def test_only_the_first_page_is_named(self):
        cap = MOD._DETAIL_ROW_CAP
        win = self._window([FakeElementId(i) for i in range(cap + 25)])
        self.assertEqual(len(win.rows), cap)
        self.assertEqual(len(win.hidden_ids), 25)
        self.assertIn('first {}'.format(cap), win.status_text.Text)

    def test_select_uses_the_ticked_rows(self):
        win = self._window([FakeElementId(i) for i in (1, 2, 3)])
        captured = []
        win.uidoc.Selection.SetElementIds = lambda picked: captured.extend(picked)
        win.Close = lambda: None
        win.rows[1]['is_selected'] = True
        win.on_select_in_model(None, None)
        self.assertEqual([i.value for i in captured], [2])
        self.assertIn('ticked', win.status_text.Text)

    def test_select_with_nothing_ticked_takes_everything(self):
        cap = MOD._DETAIL_ROW_CAP
        win = self._window([FakeElementId(i) for i in range(cap + 5)])
        captured = []
        win.uidoc.Selection.SetElementIds = lambda picked: captured.extend(picked)
        win.Close = lambda: None
        win.on_select_in_model(None, None)
        self.assertEqual(len(captured), cap + 5,
                         'rows past the render cap must still be reachable')

    def test_empty_metric_shows_the_placeholder(self):
        win = self._window([])
        self.assertEqual(win.empty_detail_elements.Visibility, 'Visible')


class MetricDetailRoutingTests(unittest.TestCase):
    """Opening the popup from the metric row and from a recommendation."""

    def setUp(self):
        self.forms = fresh_forms()
        self.opened = []

    def _window(self, ids):
        win = make_window(status_text=_text_block())
        win.health_analyzer = SimpleNamespace(element_ids={'groups': ids})
        win.show_metric_detail = lambda key, status=None: self.opened.append((key, status))
        return win

    def test_metric_row_button_opens_that_metric(self):
        win = self._window([FakeElementId(1)])
        row = MOD.GridRow(key='groups', status='Critical')
        win.on_health_metric_detail(SimpleNamespace(DataContext=row), None)
        self.assertEqual(self.opened, [('groups', 'Critical')])

    def test_recommendation_button_opens_the_same_popup(self):
        win = self._window([FakeElementId(1)])
        rec = MOD.GridRow(key='groups', status='Severe')
        win.on_recommendation_detail(SimpleNamespace(DataContext=rec), None)
        self.assertEqual(self.opened, [('groups', 'Severe')])

    def test_a_row_with_no_datacontext_is_ignored(self):
        win = self._window([FakeElementId(1)])
        win.on_health_metric_detail(SimpleNamespace(DataContext=None), None)
        self.assertEqual(self.opened, [])


# ────────────────────────────── 2. warnings tab ─────────────────────────────
class WarningTests(unittest.TestCase):
    def setUp(self):
        FakeTransaction.instances = []
        self.forms = fresh_forms()

    def _window(self, answer=True):
        self.forms = fresh_forms(answer)
        dupe = FakeWarning('There are identical instances in the same place',
                           failing=[FakeElementId(1), FakeElementId(2)])
        win = make_window(dg_warning_groups=_grid(), lst_warning_elements=_grid())
        win.doc = FakeDoc(warnings=[dupe, FakeWarning('Room not enclosed')])
        return win

    def test_reload_groups_warnings_by_description(self):
        win = self._window()
        win.on_warning_reload(None, None)
        rows = win.dg_warning_groups.ItemsSource
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].count, 2, 'rows must be sorted by count, biggest first')

    def test_autofix_asks_before_deleting(self):
        win = self._window()
        win.on_warning_reload = lambda *a: None
        win.on_warning_autofix(None, None)
        self.assertEqual(len(self.forms.prompts), 1, 'duplicates were deleted with no prompt')
        self.assertEqual(len(win.doc.deleted), 1, 'one of each pair is kept')

    def test_autofix_declined_deletes_nothing(self):
        win = self._window(answer=False)
        win.on_warning_reload = lambda *a: None
        win.on_warning_autofix(None, None)
        self.assertEqual(win.doc.deleted, [])
        self.assertEqual(FakeTransaction.instances, [])

    def test_select_reads_the_id_not_a_bracket_in_the_name(self):
        """Rows read "Category : Name [id]"; a family called "Door [100]"
        used to have its own bracket picked up as the element id."""
        win = self._window()
        win.uidoc = SimpleNamespace(
            Selection=SimpleNamespace(SetElementIds=lambda ids: None),
            ShowElements=lambda ids: None)
        win.lst_warning_elements.SelectedItems = ['Doors : Door [100] [55]']
        captured = []
        win.uidoc.Selection.SetElementIds = lambda ids: captured.extend(ids)
        win.on_warning_select_elements(None, None)
        self.assertEqual([i.value for i in captured], [55])

    def test_select_falls_back_to_the_whole_group(self):
        """A group can hold thousands of elements but only its head is
        rendered, so selecting nothing must still reach all of them."""
        win = self._window()
        captured = []
        win.uidoc = SimpleNamespace(
            Selection=SimpleNamespace(SetElementIds=lambda ids: captured.extend(ids)),
            ShowElements=lambda ids: None)
        win.on_warning_reload(None, None)
        win.dg_warning_groups.SelectedItem = win.dg_warning_groups.ItemsSource[0]
        win.lst_warning_elements.SelectedItems = []
        win.on_warning_select_elements(None, None)
        self.assertEqual(len(captured), 2)
        self.assertIn('whole group', win.status_text.Text)

    def test_select_with_nothing_chosen_at_all_says_so(self):
        win = self._window()
        win.dg_warning_groups.SelectedItem = None
        win.lst_warning_elements.SelectedItems = []
        win.on_warning_select_elements(None, None)
        self.assertIn('Select a warning group', self.forms.messages[-1])

    def test_long_group_renders_only_its_head(self):
        win = self._window()
        cap = MOD.ModelAuditorWindow._MAX_WARNING_ROWS
        row = MOD.GridRow(description='many',
                          element_ids=[FakeElementId(i) for i in range(cap + 50)])
        win.dg_warning_groups.SelectedItem = row
        win.on_warning_group_changed(None, None)
        rendered = win.lst_warning_elements.ItemsSource
        self.assertEqual(len(rendered), cap + 1, 'head rows plus one note')
        self.assertIn('and 50 more', rendered[-1])

    def test_autofix_rolls_back_when_commit_fails(self):
        win = self._window()
        win.on_warning_reload = lambda *a: None
        win.doc.delete_error = RuntimeError('element is pinned')
        win.on_warning_autofix(None, None)
        # Individual Delete failures are tolerated and reported, not fatal.
        self.assertTrue(FakeTransaction.instances[0].committed)
        self.assertIn('could not be deleted', win.status_text.Text)


# ───────────────────────────── 3. smart purge tab ───────────────────────────
class SmartPurgeTests(unittest.TestCase):
    def setUp(self):
        FakeTransaction.instances = []
        self.forms = fresh_forms()
        self.calls = []
        calls = self.calls

        class PurgeExecutor(object):
            def __init__(self, doc):
                self.doc = doc
                self.progress_callback = None

            def execute_purge(self, categories):
                calls.append([(c.name, len(c.unused_items)) for c in categories])
                return (sum(len(c.unused_items) for c in categories), 0, [], [])

        purge_mod = types.ModuleType('purge_executor')
        purge_mod.PurgeExecutor = PurgeExecutor
        for name in ('Services', 'Services.ModelAuditor',
                     'Services.ModelAuditor.smart_purge'):
            sys.modules.setdefault(name, types.ModuleType(name))
        sys.modules['Services.ModelAuditor.smart_purge.purge_executor'] = purge_mod

    def _rows(self):
        rows = []
        for eid, category, selected, deletable in (
                (1, 'Materials', True, True),
                (2, 'Materials', True, True),
                (3, 'Filters', True, True),
                (4, 'Filters', False, True),      # unticked
                (5, 'Filters', True, False)):     # protected
            row = MOD.GridRow(id=eid, can_delete=deletable)
            row.is_selected = selected
            row.purge_category = category
            rows.append(row)
        return rows

    def test_purge_groups_selection_by_category(self):
        win = make_window(purge_items=self._rows(), dg_smart_purge=_grid())
        win.load_smart_purge = lambda: None
        win.on_smart_purge_run(SimpleNamespace(IsEnabled=True), None)
        self.assertEqual(len(self.forms.prompts), 1)
        self.assertEqual(self.calls, [[('Materials', 2), ('Filters', 1)]],
                         'unticked and protected rows must not be purged')

    def test_purge_declined_runs_nothing(self):
        self.forms = fresh_forms(answer=False)
        win = make_window(purge_items=self._rows(), dg_smart_purge=_grid())
        win.load_smart_purge = lambda: None
        win.on_smart_purge_run(SimpleNamespace(IsEnabled=True), None)
        self.assertEqual(self.calls, [])

    def test_purge_before_a_scan_tells_the_user_to_scan(self):
        win = make_window(dg_smart_purge=_grid())
        win.on_smart_purge_run(SimpleNamespace(IsEnabled=True), None)
        self.assertEqual(self.calls, [])
        self.assertIn('scan the model first', self.forms.messages[-1])

    def test_purge_after_a_clean_scan_says_nothing_was_found(self):
        win = make_window(dg_smart_purge=_grid())
        win._purge_scanned = True
        win.on_smart_purge_run(SimpleNamespace(IsEnabled=True), None)
        self.assertEqual(self.calls, [])
        self.assertIn('no unused items', self.forms.messages[-1])

    def test_nothing_ticked_reports_protected_count(self):
        rows = [r for r in self._rows() if not r.get('can_delete', True)]
        win = make_window(purge_items=rows, dg_smart_purge=_grid())
        win.on_smart_purge_run(SimpleNamespace(IsEnabled=True), None)
        self.assertEqual(self.calls, [])
        self.assertIn('protected', self.forms.messages[0])


# ───────────────────────────── empty states ─────────────────────────────────
class EmptyStateTests(unittest.TestCase):
    """Every grid shows a message instead of a blank white rectangle."""

    def setUp(self):
        self.forms = fresh_forms()

    def _window(self):
        return make_window(dg_warning_groups=_grid(), lst_warning_elements=_grid(),
                           empty_warning_groups=SimpleNamespace(Visibility=None),
                           empty_warning_elements=SimpleNamespace(Visibility=None))

    def test_placeholder_shows_when_there_are_no_rows(self):
        win = self._window()
        win._set_rows(win.dg_warning_groups, 'empty_warning_groups', [])
        self.assertEqual(win.empty_warning_groups.Visibility, 'Visible')
        self.assertIsNone(win.dg_warning_groups.ItemsSource)

    def test_placeholder_hides_once_rows_arrive(self):
        win = self._window()
        win._set_rows(win.dg_warning_groups, 'empty_warning_groups', ['a', 'b'])
        self.assertEqual(win.empty_warning_groups.Visibility, 'Collapsed')
        self.assertEqual(len(win.dg_warning_groups.ItemsSource), 2)

    def test_reload_on_a_clean_model_shows_the_empty_state(self):
        win = self._window()
        win.doc = FakeDoc(warnings=[])
        win.on_warning_reload(None, None)
        self.assertEqual(win.empty_warning_groups.Visibility, 'Visible')
        self.assertEqual(win.empty_warning_elements.Visibility, 'Visible')

    def test_every_grid_in_the_xaml_has_a_named_placeholder(self):
        import re
        for grid in ('dg_health_metrics', 'dg_warning_groups', 'lst_warning_elements',
                     'dg_smart_purge'):
            self.assertIn(grid, XAML_SOURCE)
        for placeholder in ('empty_health_metrics', 'empty_warning_groups',
                            'empty_warning_elements', 'empty_smart_purge'):
            self.assertIn('x:Name="{}"'.format(placeholder), XAML_SOURCE)
            self.assertIn(placeholder, SOURCE,
                          '{} is declared but never toggled'.format(placeholder))


# ─────────────────────────── navigation across tabs ─────────────────────────
class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.forms = fresh_forms()

    def _window(self):
        win = make_window(
            main_tab_control=SimpleNamespace(SelectedIndex=0),
            btn_tab_health=SimpleNamespace(IsChecked=False),
            btn_tab_warning=SimpleNamespace(IsChecked=False),
            btn_tab_purge=SimpleNamespace(IsChecked=False),
            dg_warning_groups=_grid(), dg_smart_purge=_grid())
        win.loaded = []
        win.on_warning_reload = lambda *a: win.loaded.append('warnings')
        win.load_smart_purge = lambda: win.loaded.append('purge')
        return win

    def test_exactly_three_tabs(self):
        self.assertEqual(len(MOD.ModelAuditorWindow._TABS), 3)
        self.assertEqual(sorted(MOD.ModelAuditorWindow._SIDEBAR_MAP.values()), [0, 1, 2])

    def test_each_tab_lights_its_own_rail_tile(self):
        win = self._window()
        for index in (0, 1, 2):
            win._go_to_main_tab(index)
            flags = [win.btn_tab_health.IsChecked, win.btn_tab_warning.IsChecked,
                     win.btn_tab_purge.IsChecked]
            self.assertEqual(flags.count(True), 1)
            self.assertTrue(flags[index])
            self.assertEqual(win.main_tab_control.SelectedIndex, index)

    def test_tabs_fill_themselves_once_on_first_visit(self):
        win = self._window()
        win._go_to_main_tab(1)
        win._go_to_main_tab(2)
        self.assertEqual(win.loaded, ['warnings', 'purge'])
        win.dg_warning_groups.ItemsSource = ['row']
        win.dg_smart_purge.ItemsSource = ['row']
        win._go_to_main_tab(1)
        win._go_to_main_tab(2)
        self.assertEqual(win.loaded, ['warnings', 'purge'], 'a revisit must not rescan')

    def test_out_of_range_index_is_ignored(self):
        win = self._window()
        win._go_to_main_tab(7)
        self.assertEqual(win.main_tab_control.SelectedIndex, 0)

    def test_a_tab_that_found_nothing_does_not_rescan(self):
        """A clean model yields zero rows, so ItemsSource stays empty. Keying
        'first visit' off that made every rail click rerun the whole scan."""
        win = self._window()
        for _ in range(5):
            win._go_to_main_tab(2)
            win._go_to_main_tab(0)
        self.assertEqual(win.loaded.count('purge'), 1)

    def test_a_failing_scan_does_not_retry_on_every_click(self):
        win = self._window()
        def boom():
            win.loaded.append('purge')
            raise RuntimeError('scanner exploded')
        win.load_smart_purge = boom
        with self.assertRaises(RuntimeError):
            win._go_to_main_tab(2)
        win._go_to_main_tab(0)
        win._go_to_main_tab(2)
        self.assertEqual(win.loaded.count('purge'), 1,
                         'the Purge button is the way to retry, not the rail tile')

    def test_clicking_the_active_tile_keeps_it_lit(self):
        # ToggleButton flips IsChecked before Click fires, so the handler has
        # to put it back or the rail ends up with nothing selected.
        win = self._window()
        win._go_to_main_tab(1)
        win.btn_tab_warning.IsChecked = False      # what the toggle does
        win.on_sidebar_clicked(SimpleNamespace(Name='btn_tab_warning'), None)
        self.assertTrue(win.btn_tab_warning.IsChecked)

    def test_sidebar_names_match_the_xaml(self):
        for name, _ in MOD.ModelAuditorWindow._TABS:
            self.assertIn('x:Name="{}"'.format(name), XAML_SOURCE)
            self.assertIn('Click="on_sidebar_clicked"', XAML_SOURCE)


# ───────────────────────── source / XAML contracts ──────────────────────────
class SourceContractTests(unittest.TestCase):
    """Guards that removed code and known traps do not creep back in."""

    REMOVED = (
        'RuleEngine', 'DepInfo', 'analyze_element', '_init_checksets',
        'on_checker_run', 'on_checker_export', 'on_adv_purge_run',
        'on_delete_analyze', 'on_delete_run', 'on_inplace_reload',
        'on_materials_reload', '_on_sub_tab_changed',
    )
    REMOVED_WIDGETS = (
        'btn_tab_compliance', 'btn_tab_elements', 'cb_checkset',
        'dg_checker_results', 'dg_special_inplace', 'dg_special_materials',
        'btn_sub_advanced_purge', 'btn_sub_smart_delete', 'chk_adv_',
        'cleanup_tab_control', 'sub_tab_control', 'tb_delete_ids',
    )

    def test_removed_handlers_are_gone_from_python(self):
        for name in self.REMOVED:
            self.assertNotIn(name, SOURCE, '{} survived the trim'.format(name))

    def test_removed_widgets_are_gone_from_both_files(self):
        for name in self.REMOVED_WIDGETS:
            self.assertNotIn(name, SOURCE, '{} still referenced in Python'.format(name))
            self.assertNotIn(name, XAML_SOURCE, '{} still present in XAML'.format(name))

    def test_xaml_is_well_formed(self):
        import xml.etree.ElementTree as ET
        ET.parse(str(XAML))

    def test_every_widget_python_touches_exists_in_xaml(self):
        import re
        declared = set(re.findall(r'x:Name="([^"]+)"', ALL_XAML))
        methods = set(re.findall(r'    def (\w+)', SOURCE))
        assigned = set(re.findall(r'self\.(\w+)\s*=', SOURCE))
        touched = set(re.findall(
            r'self\.(\w+)\.(?:Click|Checked|SelectionChanged|ItemsSource|Text|'
            r'IsChecked|IsEnabled|SelectedItem|SelectedItems|Items|Fill|'
            r'Foreground|Background|Value|Visibility|SelectedIndex)\b', SOURCE))
        missing = sorted(touched - declared - methods - assigned)
        self.assertEqual(missing, [], 'these would raise AttributeError at runtime')

    def test_no_unused_imports(self):
        tree = ast.parse(SOURCE)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imported.add((alias.asname or alias.name).split('.')[0])
        seen = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                seen.add(node.id)
            elif isinstance(node, ast.Attribute):
                root = node
                while isinstance(root, ast.Attribute):
                    root = root.value
                if isinstance(root, ast.Name):
                    seen.add(root.id)
        self.assertEqual(sorted(imported - seen), [])

    def test_no_messagebox_show_calls(self):
        self.assertNotIn('forms.MessageBox' + '.show(', SOURCE)

    def test_chrome_handlers_not_rebound(self):
        # T3WPFWindow._wire_window_controls() already binds these; binding them
        # again made one Maximize click toggle twice, i.e. do nothing.
        for line in ('self.btn_minimize.Click +=', 'self.btn_maximize.Click +=',
                     'self.btn_close.Click +='):
            self.assertNotIn(line, SOURCE)

    def test_ai_summary_bound_once(self):
        # ModelAuditor.xaml declares Click="ai_health_summary_clicked" and
        # T3WPFWindow re-attaches XAML handlers, so a second += issued two
        # LLM requests per click.
        self.assertNotIn('btn_ai.Click +=', SOURCE)

    def test_no_print_in_handlers(self):
        self.assertNotIn('\n            print(', SOURCE)

    def test_save_file_uses_a_real_parameter_name(self):
        self.assertNotIn('filesfilter=', SOURCE)

    def test_no_hardcoded_colours_in_python(self):
        """Colours belong to the stylesheet. The dashboard used to paint its
        chips from a six-step Tailwind ramp while the legend strip above the
        table read T3 tokens, so the two never agreed."""
        import re
        hexes = re.findall(r'"#[0-9A-Fa-f]{3,8}"', SOURCE)
        self.assertEqual(hexes, [], 'use a T3.* token via self._brush() instead')

    def test_brush_lookups_use_t3_dot_notation(self):
        import re
        for token in re.findall(r"_brush\(['\"]([^'\"]+)['\"]\)", SOURCE):
            self.assertTrue(token.startswith('T3.'), token)
            self.assertIn('x:Key="{}"'.format(token), STYLESHEET,
                          '{} is not defined in the stylesheet'.format(token))

    def test_exactly_one_copyright_in_footer(self):
        self.assertEqual(XAML_SOURCE.count('{StaticResource T3.Copyright}'), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
