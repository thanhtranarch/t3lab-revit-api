"""FamiGen review pane: pure tessellation + MCP proposal store (no Revit, no WPF).

Run:  python3 dev/test_famigen_preview.py
"""
import importlib.util
import math
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / 'T3Lab.extension' / 'lib'


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, LIB / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pm = _load('preview_mesh', 'FamilyGen/preview_mesh.py')
proposals = _load('proposals', 'FamilyGen/proposals.py')
contract = _load('family_schema', 'Intelligence/family_schema.py')


def rect(x0, y0, x1, y1, z=0):
    pts = [[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]]
    return [{'type': 'Line', 'start': pts[i], 'end': pts[(i + 1) % 4]} for i in range(4)]


def tri_area(points, tri):
    (ax, ay), (bx, by), (cx, cy) = (points[i] for i in tri)
    return 0.5 * ((bx - ax) * (cy - ay) - (cx - ax) * (by - ay))


def mesh_area(mesh):
    total = 0.0
    for a, b, c in mesh.triangles:
        pa, pb, pc = (mesh.positions[i] for i in (a, b, c))
        u = [pb[k] - pa[k] for k in range(3)]
        v = [pc[k] - pa[k] for k in range(3)]
        cr = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        total += 0.5 * math.sqrt(sum(x * x for x in cr))
    return total


def one(geom, **extra):
    schema = {'family_name': 'T', 'family_category': 'Furniture', 'geometry': [geom]}
    schema.update(extra)
    return pm.build_preview(schema)


class TriangulationTests(unittest.TestCase):
    def test_concave_polygon_area_and_winding(self):
        l_shape = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3)]
        for loop in (l_shape, l_shape[::-1]):
            pts, tris = pm.triangulate(loop)
            self.assertEqual(len(tris), 4)
            self.assertTrue(all(tri_area(pts, t) > 0 for t in tris))
            self.assertAlmostEqual(sum(tri_area(pts, t) for t in tris), 6.0)

    def test_hole_is_cut(self):
        outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
        hole = [(4, 4), (6, 4), (6, 6), (4, 6)]
        pts, tris = pm.triangulate(outer, [hole])
        self.assertAlmostEqual(sum(tri_area(pts, t) for t in tris), 96.0)
        self.assertTrue(all(tri_area(pts, t) >= -1e-9 for t in tris))


class GeometryTests(unittest.TestCase):
    def test_extrusion_box(self):
        model = one({'type': 'Extrusion', 'profile': rect(0, 0, 400, 200),
                     'extrusion_start': 100, 'extrusion_end': 850})
        self.assertEqual(model.warnings, [])
        self.assertEqual(model.bbox_min, (0, 0, 100))
        self.assertEqual(model.bbox_max, (400, 200, 850))
        self.assertEqual(model.triangle_count, 12)
        self.assertAlmostEqual(mesh_area(model.meshes[0]), 2 * (400 * 200 + 400 * 750 + 200 * 750))

    def test_extrusion_with_hole_and_x_plane(self):
        model = one({'type': 'Extrusion', 'profile': rect(0, 0, 100, 100),
                     'inner_loops': [rect(40, 40, 60, 60)], 'extrusion_start': 0, 'extrusion_end': 10})
        side = 4 * 100 * 10 + 4 * 20 * 10
        self.assertAlmostEqual(mesh_area(model.meshes[0]), 2 * (100 * 100 - 20 * 20) + side)
        # A profile drawn in YZ on sketch_plane_x=50 extrudes along +X from 50+5 to 50+25.
        prof = [{'type': 'Circle', 'center': [999, 0, 300], 'radius': 40}]
        model = one({'type': 'Extrusion', 'sketch_plane_x': 50, 'profile': prof,
                     'extrusion_start': 5, 'extrusion_end': 25})
        self.assertAlmostEqual(model.bbox_min[0], 55)
        self.assertAlmostEqual(model.bbox_max[0], 75)
        self.assertAlmostEqual(model.bbox_max[2], 340, places=3)

    def test_cylinder_any_direction(self):
        model = one({'type': 'Cylinder', 'start': [0, 0, 0], 'end': [300, 400, 0], 'radius': 20})
        mesh = model.meshes[0]
        axis = (0.6, 0.8, 0.0)
        for p in mesh.positions:
            t = p[0] * axis[0] + p[1] * axis[1]
            radial = math.sqrt(max(0.0, p[0] ** 2 + p[1] ** 2 + p[2] ** 2 - t * t))
            self.assertTrue(radial < 20.001)
        self.assertAlmostEqual(max(abs(p[2]) for p in mesh.positions), 20, places=3)

    def test_revolution_full_and_partial(self):
        profile = rect(50, 0, 100, 0, z=0)
        profile = [{'type': 'Line', 'start': a, 'end': b} for a, b in (
            ([0, 50, 0], [0, 100, 0]), ([0, 100, 0], [0, 100, 200]),
            ([0, 100, 200], [0, 50, 200]), ([0, 50, 200], [0, 50, 0]))]
        geom = {'type': 'Revolution', 'sketch_plane_x': 0, 'profile': profile,
                'axis_start': [0, 0, 0], 'axis_end': [0, 0, 200]}
        model = one(geom)
        self.assertAlmostEqual(model.bbox_min[0], -100, places=3)
        self.assertAlmostEqual(model.bbox_max[2], 200, places=3)
        full = model.triangle_count
        geom['end_angle'] = math.pi
        half = one(geom)
        self.assertLess(half.triangle_count, full)        # half the surface ...
        self.assertGreater(half.triangle_count, full / 2)  # ... plus two caps

    def test_blend_offsets_explicit_and_drawn(self):
        model = one({'type': 'Blend', 'profile': rect(-200, -200, 200, 200),
                     'top_profile': [{'type': 'Circle', 'center': [0, 0, 0], 'radius': 80}],
                     'base_offset': 10, 'top_offset': 400})
        self.assertEqual((model.bbox_min[2], model.bbox_max[2]), (10, 400))
        model = one({'type': 'Blend', 'profile': rect(-200, -200, 200, 200),
                     'top_profile': [{'type': 'Circle', 'center': [0, 0, 650], 'radius': 80}]})
        self.assertAlmostEqual(model.bbox_max[2], 650)    # height read from the drawing

    def test_sweep_follows_path(self):
        path = [{'type': 'Line', 'start': [0, 0, 0], 'end': [0, 0, 500]},
                {'type': 'Arc3P', 'start': [0, 0, 500], 'end': [200, 0, 700], 'mid': [58.6, 0, 641.4]}]
        model = one({'type': 'Sweep', 'path': path, 'sketch_plane_y': 0,
                     'profile': [{'type': 'Circle', 'center': [0, 0, 0], 'radius': 15}]})
        self.assertEqual(model.warnings, [])
        # chord tangents (7.5 deg steps) tilt the end section by < 4 deg: < 1 mm
        self.assertAlmostEqual(model.bbox_max[0], 200, delta=1.0)
        self.assertAlmostEqual(model.bbox_max[2], 715, delta=1.0)
        self.assertAlmostEqual(model.bbox_min[2], 0, delta=1e-6)

    def test_arc3p_passes_through_mid(self):
        pts = pm.sample_segment({'type': 'Arc3P', 'start': [0, 0, 0], 'end': [10, 0, 0], 'mid': [5, -5, 0]})
        self.assertTrue(min(abs(p[0] - 5) + abs(p[1] + 5) for p in pts) < 0.5)
        self.assertEqual(pts[-1], (10.0, 0.0, 0.0))

    def test_bad_part_warns_others_still_shown(self):
        schema = {'family_name': 'T', 'family_category': 'Furniture', 'geometry': [
            {'type': 'Extrusion', 'profile': [{'type': 'Line', 'start': [0, 0, 0], 'end': [1, 0, 0]}]},
            {'type': 'Cylinder', 'start': [0, 0, 0], 'end': [0, 0, 100], 'radius': 5},
            {'type': 'Teleport'}]}
        model = pm.build_preview(schema)
        self.assertEqual(len(model.meshes), 1)
        self.assertEqual(len(model.warnings), 2)
        self.assertIn('#1 (Extrusion)', model.warnings[0])

    def test_materials_voids_and_example(self):
        model = pm.build_preview(contract.EXAMPLE_SCHEMA)
        self.assertEqual(model.material_counts(), {'Oak': 1, 'Black Steel': 2})
        self.assertEqual(model.size_mm(), (500.0, 500.0, 550.0))
        schema = dict(contract.EXAMPLE_SCHEMA)
        schema['geometry'] = list(schema['geometry']) + [
            {'type': 'Cylinder', 'start': [0, 0, -900], 'end': [0, 0, 600], 'radius': 5,
             'is_solid': False, 'material': 'Oak'}]
        model = pm.build_preview(schema)
        self.assertEqual(model.size_mm(), (500.0, 500.0, 550.0))   # voids do not size the box
        self.assertIsNone(model.meshes[-1].material)
        self.assertIn('void', model.warnings[-1])

    def test_camera_frames_box(self):
        position, look, up = pm.camera_pose((0, 0, 0), (1000, 1000, 1000), yaw=0.0, pitch=0.0)
        self.assertEqual(up, (0.0, 0.0, 1.0))
        target = tuple(position[i] + look[i] for i in range(3))
        for i in range(3):
            self.assertAlmostEqual(target[i], 500.0)
        radius = 0.5 * math.sqrt(3) * 1000
        self.assertAlmostEqual(position[0] - 500, radius / math.sin(math.radians(22.5)))
        closer, _, _ = pm.camera_pose((0, 0, 0), (1000, 1000, 1000), 0.0, 0.0, zoom=0.5)
        self.assertLess(closer[0], position[0])
        self.assertEqual(pm.clamp_pitch(9), 1.45)
        pm.camera_pose(None, None)                        # empty scene still has a camera


class ProposalStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.store = proposals.ProposalStore(self.folder)

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_round_trip_and_ids(self):
        pid = self.store.save({'family_name': 'A'}, source='mcp', note='hi')
        self.assertTrue(proposals.is_valid_id(pid))
        record = self.store.load(pid)
        self.assertEqual((record['schema'], record['note'], record['source']),
                         ({'family_name': 'A'}, 'hi', 'mcp'))
        self.assertEqual(self.store.ids(), [pid])

    def test_bad_ids_never_touch_the_disk(self):
        for bad in ('../secret', 'FG-1', None, 42, 'FG-20260101-000000-ABCDEF'):
            self.assertIsNone(self.store.load(bad))

    def test_prune_keeps_latest(self):
        ticks = iter(range(10 ** 6))
        store = proposals.ProposalStore(self.folder, clock=lambda: 1.7e9 + next(ticks))
        ids = [store.save({'n': i}) for i in range(proposals.MAX_RECORDS + 5)]
        self.assertEqual(len(store.ids()), proposals.MAX_RECORDS)
        self.assertIsNone(store.load(ids[0]))
        self.assertEqual(store.load(ids[-1])['schema'], {'n': proposals.MAX_RECORDS + 4})

    def test_active_window_registry(self):
        win, other = object(), object()
        proposals.set_active_window(win)
        proposals.clear_active_window(other)
        self.assertIs(proposals.get_active_window(), win)
        proposals.clear_active_window(win)
        self.assertIsNone(proposals.get_active_window())


if __name__ == '__main__':
    unittest.main()
