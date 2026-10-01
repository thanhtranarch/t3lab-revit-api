"""Pure FamiGen AI contract and bounded repair regression tests."""
import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/Intelligence/family_schema.py'
spec = importlib.util.spec_from_file_location('family_schema', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def schema():
    return {'family_name': 'Chair', 'family_category': 'Furniture', 'geometry': [
        {'type': 'Cylinder', 'start': [0, 0, 0], 'end': [0, 0, 900], 'radius': 15}]}


class Bridge:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []
    def ask_json(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return next(self.results)


class FamilyContractTests(unittest.TestCase):
    def test_prompt_contract_precedes_and_follows_overlay(self):
        prompt = module.build_system_prompt('Furniture', 'Prefer four legs')
        self.assertIn('millimeters', prompt)
        self.assertIn('radians', prompt)
        self.assertIn('Cylinder', prompt)
        self.assertIn('"Furniture"', prompt)
        self.assertIn('Prefer four legs', prompt)
        self.assertTrue(prompt.rstrip().endswith('take precedence.'))

    def test_valid_all_supported_forms_and_curves(self):
        circle = {'type': 'Circle', 'center': [0, 0, 0], 'radius': 20}
        value = schema()
        value['geometry'] += [
            {'type': 'Extrusion', 'profile': [circle], 'extrusion_start': 0, 'extrusion_end': 100},
            {'type': 'Blend', 'profile': [circle], 'top_profile': [circle], 'top_offset': 100},
            {'type': 'Revolution', 'profile': [circle], 'axis_start': [0,0,0], 'axis_end': [0,0,100]},
            {'type': 'Sweep', 'profile': [circle], 'path': [{'type': 'Line', 'start': [0,0,0], 'end': [0,0,100]}]}]
        self.assertEqual(module.validate_ai_schema(value, 'Furniture'), [])
        for segment in (
            {'type':'Arc3P','start':[0,0,0],'end':[1,1,0],'mid':[0,1,0]},
            {'type':'ArcThreePoint','start':[0,0,0],'end':[1,1,0],'mid':[0,1,0]},
            {'type':'Spline','points':[[0,0,0],[1,1,0],[2,2,0]]},
            {'type':'Arc','center':[0,0,0],'radius':1,'start_angle':0,'end_angle':3.14},
            {'type':'Ellipse','center':[0,0,0],'radius_x':2,'radius_y':1}):
            value['geometry'][1]['profile'] = [segment]
            self.assertEqual(module.validate_ai_schema(value, 'Furniture'), [])

    def test_root_aliases_and_unsupported_types_rejected(self):
        self.assertTrue(module.validate_ai_schema([schema()], 'Furniture'))
        for alias in ('forms', 'shapes', 'primitives', 'elements'):
            value = schema(); value[alias] = []
            self.assertIn('$.' + alias, '\n'.join(module.validate_ai_schema(value, 'Furniture')))
        for kind in ('Box', 'cylinder', None):
            value = schema(); value['geometry'][0]['type'] = kind
            self.assertIn('$.geometry[0].type', '\n'.join(module.validate_ai_schema(value, 'Furniture')))

    def test_finite_numbers_coordinates_and_category_paths(self):
        for value in (True, float('nan'), float('inf'), '15'):
            data = schema(); data['geometry'][0]['radius'] = value
            self.assertIn('$.geometry[0].radius', '\n'.join(module.validate_ai_schema(data, 'Furniture')))
        data = schema(); data['geometry'][0]['start'] = [0,0]
        self.assertIn('$.geometry[0].start', '\n'.join(module.validate_ai_schema(data, 'Furniture')))
        self.assertIn('$.family_category', '\n'.join(module.validate_ai_schema(schema(), 'Lighting Fixtures')))

    def test_required_fields_and_preserved_input(self):
        value = schema(); value['geometry'][0].pop('end')
        before = copy.deepcopy(value)
        self.assertIn('$.geometry[0].end', '\n'.join(module.validate_ai_schema(value, 'Furniture')))
        self.assertEqual(value, before)
        value = schema(); value['geometry'][0] = {'type': 'Sweep', 'profile': []}
        errors = '\n'.join(module.validate_ai_schema(value, 'Furniture'))
        self.assertIn('.profile', errors); self.assertIn('.path', errors)

    def test_exact_degeneracy_rejected_without_dimension_limits(self):
        circle = {'type': 'Circle', 'center': [0, 0, 0], 'radius': 20}
        geometries = [
            {'type': 'Cylinder', 'start': [0,0,0], 'end': [0,0,0], 'radius': 1},
            {'type': 'Revolution', 'profile': [circle], 'axis_start': [0,0,0], 'axis_end': [0,0,0]},
            {'type': 'Revolution', 'profile': [circle], 'axis_start': [0,0,0], 'axis_end': [0,0,1], 'start_angle': 2, 'end_angle': 1},
            {'type': 'Extrusion', 'profile': [circle], 'extrusion_start': 2, 'extrusion_end': 2},
            {'type': 'Blend', 'profile': [circle], 'top_profile': [circle], 'base_offset': 2, 'top_offset': 1},
        ]
        for geometry in geometries:
            value = schema(); value['geometry'] = [geometry]
            self.assertTrue(module.validate_ai_schema(value, 'Furniture'))
        for kind in ('Line', 'Arc3P', 'ArcThreePoint'):
            geom = {'type': 'Extrusion', 'extrusion_start': -100, 'extrusion_end': -20,
                    'profile': [{'type': kind, 'start': [1,2,3], 'end': [1,2,3], 'mid': [4,5,6]}]}
            value = schema(); value['geometry'] = [geom]
            self.assertIn('zero-length', '\n'.join(module.validate_ai_schema(value, 'Furniture')))
        value = schema(); value['geometry'] = [
            {'type': 'Extrusion', 'profile': [circle], 'extrusion_start': -100, 'extrusion_end': -20},
            {'type': 'Blend', 'profile': [circle], 'top_profile': [circle], 'base_offset': -100, 'top_offset': -20},
            {'type': 'Cylinder', 'start': [0,0,0], 'end': [0,0,0.01], 'radius': 0.01}]
        self.assertEqual(module.validate_ai_schema(value, 'Furniture'), [])

    def test_initial_valid_needs_one_call(self):
        value = schema(); bridge = Bridge([value])
        self.assertIs(module.generate_family_schema(bridge, 'a chair', 'Furniture'), value)
        self.assertEqual(len(bridge.calls), 1)
        self.assertEqual(bridge.calls[0][1]['max_tokens'], 6000)

    def test_precise_single_repair_preserves_description_and_input(self):
        invalid = schema(); invalid['family_category'] = 'Doors'
        before = copy.deepcopy(invalid)
        bridge = Bridge([invalid, schema()])
        result = module.generate_family_schema(bridge, 'four leg chair', 'Furniture', 'Wood finish')
        self.assertEqual(result['family_category'], 'Furniture')
        self.assertEqual(len(bridge.calls), 2)
        self.assertEqual(bridge.calls[0][1], bridge.calls[1][1])
        self.assertIn('four leg chair', bridge.calls[1][0])
        self.assertIn('$.family_category', bridge.calls[1][0])
        self.assertEqual(invalid, before)

    def test_second_invalid_rejected_without_loop(self):
        bridge = Bridge([[], []])
        with self.assertRaisesRegex(ValueError, 'after one repair'):
            module.generate_family_schema(bridge, 'chair', 'Furniture')
        self.assertEqual(len(bridge.calls), 2)

    def test_unparseable_or_unavailable_provider_clearly_rejected(self):
        bridge = Bridge([None])
        with self.assertRaisesRegex(ValueError, 'parseable JSON'):
            module.generate_family_schema(bridge, 'chair', 'Furniture')
        self.assertEqual(len(bridge.calls), 1)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            module.generate_family_schema(None, 'chair', 'Furniture')
        class FailingBridge:
            def ask_json(self, *args, **kwargs):
                raise RuntimeError('connection lost')
        with self.assertRaisesRegex(ValueError, 'provider request failed'):
            module.generate_family_schema(FailingBridge(), 'chair', 'Furniture')


if __name__ == '__main__':
    unittest.main()
