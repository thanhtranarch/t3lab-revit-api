"""FamilyGen.builder orchestration with stub Revit modules (no Revit needed).

Run:  python3 dev/test_famigen_builder.py

Geometry creation itself (NewExtrusion, NewBlend, ...) is Revit's and still
needs a Revit check. What is covered here is everything around it: validation
before any document is opened, template resolution without silent fallback,
materials -> Material elements + one Material family parameter each + the
association of every solid, subcategories, created/set parameters, one
transaction committed or rolled back, SaveAs, LoadFamily and Close.
"""
import os
import sys
import tempfile
import types
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'T3Lab.extension', 'lib')
sys.path.insert(0, LIB)


# ── stub Revit API ───────────────────────────────────────────────────────────

class _Any(object):
    def __init__(self, *args, **kwargs):
        self.args = args

    def __getattr__(self, name):
        return _Any()

    def __call__(self, *args, **kwargs):
        return _Any()


class ForgeTypeId(object):
    def __init__(self, type_id):
        self.TypeId = type_id


class _Ns(object):
    pass


GroupTypeId = _Ns()
for _n in ('Materials', 'Geometry', 'Data', 'Text'):
    setattr(GroupTypeId, _n, ForgeTypeId('autodesk.parameter.group:' + _n.lower() + '-1.0.0'))
SpecTypeId = _Ns()
SpecTypeId.Length = ForgeTypeId('autodesk.spec.aec:length-2.0.0')
SpecTypeId.Number = ForgeTypeId('autodesk.spec:number-2.0.0')
SpecTypeId.Int = _Ns()
SpecTypeId.Int.Integer = ForgeTypeId('autodesk.spec:int64-2.0.0')
SpecTypeId.String = _Ns()
SpecTypeId.String.Text = ForgeTypeId('autodesk.spec:string-2.0.0')
SpecTypeId.Reference = _Ns()
SpecTypeId.Reference.Material = ForgeTypeId('autodesk.spec.reference:material-2.0.0')
SPEC_OF = {'length': SpecTypeId.Length, 'number': SpecTypeId.Number,
           'integer': SpecTypeId.Int.Integer, 'text': SpecTypeId.String.Text,
           'material': SpecTypeId.Reference.Material}


class ElementId(object):
    def __init__(self, value):
        self.value = value


class Color(object):
    def __init__(self, r, g, b):
        self.rgb = (r, g, b)


class Material(object):
    created = []

    def __init__(self, doc, name):
        self.Name = name
        self.Id = ElementId(name)
        self.Color = None
        self.Transparency = None
        self.Shininess = None
        self.Smoothness = None

    @staticmethod
    def Create(doc, name):
        mat = Material(doc, name)
        doc.elements[name] = mat
        return mat.Id


class Transaction(object):
    def __init__(self, doc, name):
        self.doc, self.name, self.state = doc, name, 'new'
        doc.transactions.append(self)

    def GetFailureHandlingOptions(self):
        return _Any()

    def SetFailureHandlingOptions(self, options):
        pass

    def Start(self):
        self.state = 'started'

    def Commit(self):
        self.state = 'committed'

    def RollBack(self):
        self.state = 'rolled back'

    def HasStarted(self):
        return self.state != 'new'

    def HasEnded(self):
        return self.state in ('committed', 'rolled back')

    def Dispose(self):
        pass


class FilteredElementCollector(object):
    def __init__(self, doc):
        self.doc = doc

    def OfClass(self, cls):
        return [e for e in self.doc.elements.values() if isinstance(e, cls)]

    def Dispose(self):
        pass


class SaveAsOptions(object):
    OverwriteExistingFile = False


class _BuiltInCategory(object):
    OST_Furniture = 'OST_Furniture'
    OST_GenericModel = 'OST_GenericModel'


class Category(object):
    @staticmethod
    def GetCategory(doc, bic):
        return 'category:' + bic


DB = types.ModuleType('Autodesk.Revit.DB')
for _name in ('Arc', 'CurveArrArray', 'CurveArray', 'Ellipse', 'FailureProcessingResult',
              'FailureSeverity', 'HermiteSpline', 'Line', 'Plane', 'ProfilePlaneLocation',
              'SketchPlane', 'XYZ', 'FamilySource'):
    setattr(DB, _name, _Any())
DB.BuiltInParameter = _Ns()
DB.BuiltInParameter.MATERIAL_ID_PARAM = 'MATERIAL_ID_PARAM'
DB.IFailuresPreprocessor = object
DB.IFamilyLoadOptions = object
DB.BuiltInCategory = _BuiltInCategory
for _cls in (Category, Color, FilteredElementCollector, Material, SaveAsOptions, Transaction):
    setattr(DB, _cls.__name__, _cls)
DB.GroupTypeId, DB.SpecTypeId = GroupTypeId, SpecTypeId
revit = types.ModuleType('Autodesk.Revit')
revit.DB = DB
autodesk = types.ModuleType('Autodesk')
autodesk.Revit = revit
generic = types.ModuleType('System.Collections.Generic')
generic.List = _Any()
system = types.ModuleType('System')
collections_mod = types.ModuleType('System.Collections')
sys.modules.update({'Autodesk': autodesk, 'Autodesk.Revit': revit, 'Autodesk.Revit.DB': DB,
                    'System': system, 'System.Collections': collections_mod,
                    'System.Collections.Generic': generic})

from FamilyGen import builder  # noqa: E402
from Intelligence.family_schema import EXAMPLE_SCHEMA  # noqa: E402


# ── fake family document ─────────────────────────────────────────────────────

class Definition(object):
    def __init__(self, name, kind):
        self.Name = name
        self._spec = SPEC_OF.get(kind, ForgeTypeId('autodesk.spec:other-1.0.0'))

    def GetDataType(self):
        return self._spec


class FamilyParameter(object):
    def __init__(self, name, kind, group=None, instance=False):
        self.Definition = Definition(name, kind)
        self.kind, self.group, self.instance = kind, group, instance


class FamilyManager(object):
    def __init__(self, existing=()):
        self.Parameters = [FamilyParameter(n, k) for n, k in existing]
        self.CurrentType = None
        self.new_types, self.values, self.associations = [], {}, []

    def NewType(self, name):
        self.new_types.append(name)
        self.CurrentType = name

    def AddParameter(self, name, group, spec, instance):
        if any(p.Definition.Name == name for p in self.Parameters):
            raise ValueError('exists')
        kind = next(k for k, v in SPEC_OF.items() if v is spec)
        param = FamilyParameter(name, kind, group.TypeId, instance)
        self.Parameters.append(param)
        return param

    def Set(self, param, value):
        if self.CurrentType is None:
            raise RuntimeError('no current type')
        self.values[param.Definition.Name] = value

    def AssociateElementParameterToFamilyParameter(self, element_param, family_param):
        self.associations.append((element_param.owner, family_param.Definition.Name))


class ElementParam(object):
    def __init__(self, owner):
        self.owner = owner
        self.value = None

    def Set(self, value):
        self.value = value


class Form(object):
    def __init__(self, label):
        self.label = label
        self.Subcategory = None
        self.material_param = ElementParam(label)

    def get_Parameter(self, bip):
        return self.material_param if bip == 'MATERIAL_ID_PARAM' else None


class SubCategory(object):
    def __init__(self, name):
        self.Name = name


class Categories(object):
    def __init__(self, family_category):
        self.family_category = family_category

    def NewSubcategory(self, parent, name):
        sub = SubCategory(name)
        parent.SubCategories.append(sub)
        return sub


class FamilyDoc(object):
    def __init__(self, existing_params=()):
        self.elements = {}
        self.transactions = []
        self.FamilyManager = FamilyManager(existing_params)
        family_category = _Ns()
        family_category.SubCategories = [SubCategory('Hidden Lines')]
        self.OwnerFamily = _Ns()
        self.OwnerFamily.FamilyCategory = family_category
        self.Settings = _Ns()
        self.Settings.Categories = Categories(family_category)
        self.saved, self.closed, self.loaded_into = None, False, None
        self.IsFamilyDocument = True

    def GetElement(self, eid):
        return self.elements[eid.value]

    def SaveAs(self, path, options):
        self.saved = (path, options.OverwriteExistingFile)

    def Close(self, save):
        self.closed = True

    def LoadFamily(self, project, options):
        self.loaded_into = project
        return 'family'


class App(object):
    VersionNumber = '2026'

    def __init__(self, template_dir, doc):
        self.FamilyTemplatePath = template_dir
        self.doc = doc
        self.opened = []

    def NewFamilyDocument(self, template):
        self.opened.append(template)
        return self.doc


def fake_build_entry(self, geom, label):
    if geom.get('id') == 'Broken':
        return [], 'empty/invalid profile'
    return [Form(label)], None


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.templates = tempfile.mkdtemp()
        self.out = tempfile.mkdtemp()
        self._orig = builder.JsonFamilyBuilder._build_entry
        builder.JsonFamilyBuilder._build_entry = fake_build_entry

    def tearDown(self):
        builder.JsonFamilyBuilder._build_entry = self._orig

    def _template(self, name):
        open(os.path.join(self.templates, name), 'w').close()

    def test_materials_parameters_subcategories_and_association(self):
        doc = FamilyDoc(existing_params=[('Top Thickness', 'length'), ('Designer', 'text')])
        report = builder.build_into_document(doc, EXAMPLE_SCHEMA)
        fm = doc.FamilyManager
        self.assertEqual(doc.transactions[0].state, 'committed')
        self.assertEqual(fm.new_types, ['Side Table 500'])            # current type ensured
        self.assertEqual(sorted(report['materials_created']), ['Black Steel', 'Oak'])
        self.assertEqual(doc.elements['Oak'].Color.rgb, (0xB0, 0x80, 0x50))
        self.assertEqual(doc.elements['Black Steel'].Shininess, 90)
        self.assertEqual(sorted(report['material_parameters']), ['Frame Material', 'Top Material'])
        frame = next(p for p in fm.Parameters if p.Definition.Name == 'Frame Material')
        self.assertEqual((frame.kind, frame.group, frame.instance),
                         ('material', 'autodesk.parameter.group:materials-1.0.0', False))
        self.assertIs(fm.values['Top Material'].value, 'Oak')        # default = the material
        self.assertEqual(fm.associations, [('Top', 'Top Material'), ('Leg', 'Frame Material'),
                                           ('Base', 'Frame Material')])
        self.assertAlmostEqual(fm.values['Top Thickness'], 30 / 304.8)
        self.assertEqual(fm.values['Designer'], 'T3Lab')
        self.assertEqual(report['parameters_created'], [])
        self.assertEqual(report['subcategories'], ['Top', 'Legs'])
        self.assertEqual((report['built'], report['total'], report['skipped']), (3, 3, []))

    def test_missing_parameters_are_created_with_their_type(self):
        doc = FamilyDoc()
        schema = dict(EXAMPLE_SCHEMA)
        schema['parameters'] = [{'name': 'Seats', 'type': 'integer', 'value': 4, 'instance': True},
                                {'name': 'Leg Gap', 'value': 120}]
        report = builder.build_into_document(doc, schema)
        fm = doc.FamilyManager
        self.assertEqual(report['parameters_created'], ['Seats', 'Leg Gap'])
        seats = next(p for p in fm.Parameters if p.Definition.Name == 'Seats')
        self.assertEqual((seats.kind, seats.instance), ('integer', True))
        self.assertEqual(fm.values['Seats'], 4)
        self.assertAlmostEqual(fm.values['Leg Gap'], 120 / 304.8)

    def test_existing_non_material_parameter_is_not_hijacked(self):
        doc = FamilyDoc(existing_params=[('Top Material', 'text')])
        report = builder.build_into_document(doc, EXAMPLE_SCHEMA)
        self.assertIn('not a Material parameter', '\n'.join(report['warnings']))
        self.assertNotIn(('Top', 'Top Material'), doc.FamilyManager.associations)

    def test_invalid_schema_opens_no_document(self):
        doc = FamilyDoc()
        app = App(self.templates, doc)
        bad = dict(EXAMPLE_SCHEMA, family_category='Spaceship')
        with self.assertRaises(builder.FamilyBuildError) as ctx:
            builder.create_family(app, bad, self.out)
        self.assertIn('nothing was created', str(ctx.exception))
        self.assertEqual(app.opened, [])

    def test_create_save_load_close(self):
        self._template('Furniture.rft')
        doc = FamilyDoc()
        app = App(self.templates, doc)
        project = _Ns()
        project.IsFamilyDocument = False
        report = builder.create_family(app, EXAMPLE_SCHEMA, self.out, project_doc=project,
                                       load_into_project=True)
        self.assertEqual(app.opened, [os.path.join(self.templates, 'Furniture.rft')])
        self.assertEqual(doc.saved, (os.path.join(self.out, 'Side Table 500.rfa'), True))
        self.assertIs(doc.loaded_into, project)
        self.assertTrue(report['loaded'] and doc.closed)
        self.assertEqual(report['category'], 'Furniture')
        self.assertIn('Saved to:', '\n'.join(builder.report_lines(report)))

    def test_missing_template_uses_generic_model_and_says_so(self):
        self._template('Metric Generic Model.rft')
        doc = FamilyDoc()
        report = builder.create_family(App(self.templates, doc), EXAMPLE_SCHEMA, self.out)
        self.assertEqual(doc.OwnerFamily.FamilyCategory, 'category:OST_Furniture')
        self.assertIn('Generic Model template', report['warnings'][-1] + ' '.join(report['warnings']))

    def test_hosted_category_without_template_is_an_error(self):
        self._template('Generic Model.rft')
        schema = dict(EXAMPLE_SCHEMA, family_category='Door')
        app = App(self.templates, FamilyDoc())
        with self.assertRaises(builder.FamilyBuildError) as ctx:
            builder.create_family(app, schema, self.out)
        self.assertIn("'Door' family template", str(ctx.exception))
        self.assertEqual(app.opened, [])

    def test_nothing_built_rolls_back_and_saves_nothing(self):
        self._template('Furniture.rft')
        doc = FamilyDoc()
        schema = dict(EXAMPLE_SCHEMA)
        schema['geometry'] = [dict(EXAMPLE_SCHEMA['geometry'][0], id='Broken')]
        with self.assertRaises(builder.FamilyBuildError):
            builder.create_family(App(self.templates, doc), schema, self.out)
        self.assertEqual(doc.transactions[0].state, 'rolled back')
        self.assertIsNone(doc.saved)
        self.assertTrue(doc.closed)


if __name__ == '__main__':
    unittest.main()
