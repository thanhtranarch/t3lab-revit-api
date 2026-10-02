# -*- coding: utf-8 -*-
"""CAD to Elements — pure geometry rules (`lib/Snippets/_cad_geometry.py`).

Everything that decides WHAT the tool creates runs here without Revit:
collinear merge, wall/beam pairing, outline chaining (rooms sharing a wall
line must stay two rooms), openings, column footprints, grid naming.

Run: python3 dev/test_cad_to_elements_geometry.py
"""
import math
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "T3Lab.extension", "lib"))

from Snippets import _cad_geometry as g  # noqa: E402
from Snippets import _units as U  # noqa: E402

MM = g.FT_PER_MM
IN = 1.0 / 12.0


def seg(x0, y0, x1, y1, layer="A"):
    return (x0, y0, x1, y1, layer)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def rect_edges(x0, y0, x1, y1):
    p = rect(x0, y0, x1, y1)
    return [("L", p[i], p[(i + 1) % 4], None) for i in range(4)]


class MergeCollinear(unittest.TestCase):
    def test_touching_and_overlapping_pieces_become_one(self):
        out = g.merge_collinear([seg(0, 0, 5, 0), seg(5, 0, 8, 0), seg(7, 0, 12, 0)])
        self.assertEqual(len(out), 1)
        xs = sorted((out[0][0], out[0][2]))
        self.assertAlmostEqual(xs[0], 0.0)
        self.assertAlmostEqual(xs[1], 12.0)

    def test_reversed_piece_merges(self):
        out = g.merge_collinear([seg(0, 0, 5, 0), seg(9, 0, 5.05, 0)])
        self.assertEqual(len(out), 1)

    def test_far_gap_parallel_offset_and_other_layer_stay_apart(self):
        out = g.merge_collinear([seg(0, 0, 5, 0), seg(6, 0, 9, 0),        # 1 ft gap
                                 seg(0, 1, 5, 1),                          # offset
                                 seg(5, 0, 7, 0, layer="B")])              # other layer
        self.assertEqual(len(out), 4)


class ParallelPairs(unittest.TestCase):
    def test_wall_pair_gives_centerline_and_thickness(self):
        t = 200 * MM
        cls, unpaired = g.find_parallel_pairs([seg(0, 0, 10, 0), seg(0, t, 10, t)])
        self.assertEqual(len(cls), 1)
        self.assertEqual(unpaired, [])
        x0, y0, x1, y1, sep, layer = cls[0]
        self.assertAlmostEqual(sep, t)
        self.assertAlmostEqual(y0, t / 2)
        self.assertAlmostEqual(y1, t / 2)
        self.assertEqual(g.round_to(g.to_mm(sep), 1), 200)

    def test_angled_pair(self):
        t = 0.5
        a = math.radians(30)
        dx, dy = math.cos(a), math.sin(a)
        nx, ny = -dy * t, dx * t
        cls, _ = g.find_parallel_pairs([seg(0, 0, 10 * dx, 10 * dy),
                                        seg(nx, ny, nx + 10 * dx, ny + 10 * dy)])
        self.assertEqual(len(cls), 1)
        self.assertAlmostEqual(cls[0][4], t, places=6)

    def test_too_wide_or_lonely_line_is_unpaired(self):
        cls, unpaired = g.find_parallel_pairs([seg(0, 0, 10, 0), seg(0, 5, 10, 5),
                                               seg(20, 0, 20, 10)])
        self.assertEqual(cls, [])
        self.assertEqual(len(unpaired), 3)

    def test_beam_window_respects_min_separation(self):
        cls, _ = g.find_parallel_pairs([seg(0, 0, 10, 0), seg(0, 20 * MM, 10, 20 * MM)],
                                       max_sep=1500 * MM, min_sep=50 * MM, min_overlap=0.7)
        self.assertEqual(cls, [])

    def test_beam_height_table(self):
        self.assertEqual(g.beam_height_for_width(200), 500)
        self.assertEqual(g.beam_height_for_width(300), 600)
        self.assertEqual(g.beam_height_for_width(450), 1000)
        self.assertEqual(g.beam_height_for_width(600), 1200)


class Outlines(unittest.TestCase):
    def test_square_from_loose_lines(self):
        loops = g.chain_loops(rect_edges(0, 0, 4, 3))
        self.assertEqual(len(loops), 1)
        self.assertAlmostEqual(g.polygon_area(g.loop_polygon(loops[0])), 12.0)

    def test_two_rooms_sharing_a_wall_line_stay_two_loops(self):
        edges = [("L", (0, 0), (2, 0), None), ("L", (2, 0), (4, 0), None),
                 ("L", (4, 0), (4, 3), None), ("L", (4, 3), (2, 3), None),
                 ("L", (2, 3), (0, 3), None), ("L", (0, 3), (0, 0), None),
                 ("L", (2, 0), (2, 3), None)]
        loops = g.chain_loops(edges)
        areas = sorted(round(g.polygon_area(g.loop_polygon(lp)), 6) for lp in loops)
        self.assertEqual(areas, [6.0, 6.0])

    def test_dangling_lines_are_ignored(self):
        edges = rect_edges(0, 0, 1, 1) + [("L", (1, 1), (5, 5), None),
                                          ("L", (10, 0), (11, 0), None)]
        self.assertEqual(len(g.chain_loops(edges)), 1)

    def test_endpoints_within_tolerance_snap(self):
        e = 0.002
        edges = [("L", (0, 0), (1, 0), None), ("L", (1 + e, 0), (1, 1), None),
                 ("L", (1, 1 - e), (0, 1), None), ("L", (0, 1), (0, e), None)]
        loops = g.chain_loops(edges)
        self.assertEqual(len(loops), 1)
        for a, b in zip(loops[0], loops[0][1:] + loops[0][:1]):
            self.assertEqual(a[2], b[1])        # consecutive edges meet exactly

    def test_d_shape_with_an_arc(self):
        edges = [("L", (0, -1), (0, 1), None), ("A", (0, 1), (0, -1), (1, 0))]
        loops = g.chain_loops(edges)
        self.assertEqual(len(loops), 1)
        self.assertAlmostEqual(abs(g.polygon_area(g.loop_polygon(loops[0], 64))),
                               math.pi / 2, places=2)

    def test_duplicate_line_is_drawn_once(self):
        edges = rect_edges(0, 0, 2, 2) + [("L", (2, 0), (0, 0), None)]
        self.assertEqual(len(g.chain_loops(edges)), 1)

    def test_circle_and_polygon_loops(self):
        self.assertEqual(len(g.loop_from_circle(0, 0, 1)), 2)
        self.assertIsNone(g.loop_from_polygon([(0, 0), (1, 0), (0, 0)]))
        lp = g.loop_from_polygon(rect(0, 0, 2, 2) + [(0, 0)])
        self.assertEqual(len(lp), 4)


class Nesting(unittest.TestCase):
    def test_opening_and_island(self):
        polys = [rect(0, 0, 10, 10), rect(2, 2, 8, 8), rect(4, 4, 6, 6), rect(20, 0, 21, 1)]
        groups = dict(g.nest_loops(polys))
        self.assertEqual(groups[0], [1])          # slab with one opening
        self.assertEqual(groups[2], [])           # island inside the opening
        self.assertEqual(groups[3], [])
        self.assertNotIn(1, groups)

    def test_interior_point_of_l_shape_avoids_notch(self):
        l_shape = [(0, 0), (10, 0), (10, 2), (2, 2), (2, 10), (0, 10)]
        p = g.interior_point(l_shape)
        self.assertTrue(g.point_in_polygon(p, l_shape))

    def test_interior_point_avoids_holes(self):
        outer, hole = rect(0, 0, 10, 10), rect(3, 3, 7, 7)
        p = g.interior_point(outer, [hole])
        self.assertTrue(g.point_in_polygon(p, outer))
        self.assertFalse(g.point_in_polygon(p, hole))

    def test_room_points_nested_rooms_and_wall_strips(self):
        hall, office = rect(0, 0, 40, 30), rect(5, 5, 15, 15)
        strip = rect(0, 40, 20, 40 + 200 * MM)            # space between two wall faces
        closet = rect(50, 0, 52, 2)                          # 4 sq ft < 1 m2
        pts, small = g.room_points([hall, office, strip, closet])
        self.assertEqual(len(pts), 2)
        self.assertEqual(small, 2)
        inside_office = [p for p in pts if g.point_in_polygon(p, office)]
        self.assertEqual(len(inside_office), 1)               # hall point is outside the office

    def test_dedupe_polygons(self):
        polys = [rect(0, 0, 2, 2), rect(0, 0, 2, 2)[::-1], rect(5, 5, 6, 6)]
        self.assertEqual(g.dedupe_polygons(polys), [0, 2])


class Footprints(unittest.TestCase):
    def test_axis_rectangle(self):
        fp = g.rectangle_footprint(rect(0, 0, 400 * MM, 600 * MM))
        self.assertEqual(fp["shape"], "rect")
        self.assertAlmostEqual(fp["angle"], 0.0)
        self.assertEqual(g.footprint_type_name(fp, 10 * MM), "400x600mm")
        self.assertAlmostEqual(fp["cx"], 200 * MM)

    def test_rotated_rectangle_and_collinear_vertex(self):
        a = math.radians(30)
        w, d = 2.0, 1.0
        pts = [(0, 0), (w * math.cos(a), w * math.sin(a)),
               (w * math.cos(a) - d * math.sin(a), w * math.sin(a) + d * math.cos(a)),
               (-d * math.sin(a), d * math.cos(a))]
        mid = ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2)
        fp = g.rectangle_footprint([pts[0], mid] + pts[1:])
        self.assertAlmostEqual(math.degrees(fp["angle"]), 30.0, places=4)
        self.assertAlmostEqual(fp["width"], w)

    def test_steep_rectangle_folds_to_small_angle(self):
        fp = g.rectangle_footprint([(0, 0), (0, 3), (-1, 3), (-1, 0)])
        self.assertAlmostEqual(fp["angle"], 0.0)
        self.assertAlmostEqual(fp["width"], 1.0)
        self.assertAlmostEqual(fp["depth"], 3.0)

    def test_non_rectangle_rejected(self):
        self.assertIsNone(g.rectangle_footprint([(0, 0), (2, 0), (3, 1), (0, 1)]))
        self.assertIsNone(g.rectangle_footprint([(0, 0), (2, 0), (1, 1)]))

    def test_filter_keeps_largest_per_centre_and_size_window(self):
        fps = [g.rectangle_footprint(rect(0, 0, 400 * MM, 400 * MM)),
               g.rectangle_footprint(rect(25 * MM, 25 * MM, 375 * MM, 375 * MM)),
               g.circle_footprint(10, 10, 250 * MM),
               g.rectangle_footprint(rect(30, 30, 30 + 20 * MM, 30 + 20 * MM)),
               g.rectangle_footprint(rect(50, 50, 60, 60))]
        kept = g.filter_footprints(fps)
        self.assertEqual(len(kept), 2)
        self.assertAlmostEqual(g.to_mm(kept[0]["width"]), 500.0)   # the circle
        self.assertEqual(g.footprint_type_name(kept[0], 10 * MM), "D500mm")

    def test_size_step_is_in_feet_and_never_truncates_to_whole_mm(self):
        # A 1/2" step keeps a 16" x 24" column exact (round_to would give 406 mm).
        fp = g.rectangle_footprint(rect(0, 0, 16.1 * IN, 23.9 * IN))
        w, d = g.footprint_size(fp, 0.5 * IN)
        self.assertAlmostEqual(w, 16 * IN, places=9)
        self.assertAlmostEqual(d, 24 * IN, places=9)
        self.assertAlmostEqual(g.snap(1.23, 0), 1.23)          # no step: unchanged

    def test_type_names_follow_the_size_unit(self):
        inches = U.paper_unit(U.LengthUnit("feetFractionalInches"))
        fp = g.rectangle_footprint(rect(0, 0, 16.1 * IN, 23.9 * IN))
        self.assertEqual(g.footprint_type_name(fp, 0.5 * IN, inches), '16"x24"')
        self.assertEqual(g.footprint_type_name(g.circle_footprint(0, 0, 9 * IN), 0.5 * IN, inches),
                         'D18"')
        metric = U.paper_unit(U.LengthUnit("meters"))           # sizes stay in mm, never m
        self.assertEqual(g.footprint_type_name(fp, 10 * MM, metric), "410x610mm")
        self.assertEqual(g.footprint_type_name(fp, 10 * MM), "410x610mm")


class Grids(unittest.TestCase):
    def test_dashed_axis_collapses_to_one_line(self):
        out = g.collapse_axis_lines([seg(0, 0, 0, 3), seg(0, 4, 0, 7), seg(0, 8, 0, 20),
                                     seg(5, 0, 5, 20)])
        self.assertEqual(len(out), 2)
        lengths = sorted(round(math.hypot(o[2] - o[0], o[3] - o[1]), 6) for o in out)
        self.assertEqual(lengths, [20.0, 20.0])

    def test_min_length_drops_short_axes(self):
        self.assertEqual(g.collapse_axis_lines([seg(0, 0, 1, 0)], min_len=2.0), [])

    def test_names_numbers_left_to_right_letters_bottom_to_top(self):
        lines = [(10, 0, 10, 20), (0, 0, 0, 20), (0, 15, 30, 15), (0, 5, 30, 5)]
        self.assertEqual(g.name_grids(lines), ["2", "1", "B", "A"])
        self.assertEqual(g.name_grids(lines, start_number=5, start_letter="C"),
                         ["6", "5", "D", "C"])

    def test_letters_skip_i_and_o_and_roll_over(self):
        seq = [g.grid_letter(i) for i in range(26)]
        self.assertNotIn("I", seq)
        self.assertNotIn("O", seq)
        self.assertEqual(seq[23], "Z")
        self.assertEqual(seq[24], "AA")
        self.assertEqual(g.letter_index("AA"), 24)
        self.assertEqual(g.letter_index("i"), 0)

    def test_same_axis_and_extend(self):
        self.assertTrue(g.same_axis((0, 0, 0, 10), (0, 20, 0, 30)))
        self.assertFalse(g.same_axis((0, 0, 0, 10), (1, 0, 1, 10)))
        self.assertEqual(g.extend_line(0, 0, 10, 0, 1), (-1, 0, 11, 0))


class Helpers(unittest.TestCase):
    def test_parse_number(self):
        self.assertEqual(g.parse_number("3000", 1), (3000.0, True))
        self.assertEqual(g.parse_number("12,5", 1), (12.5, True))
        self.assertEqual(g.parse_number("", 7), (7, False))
        self.assertEqual(g.parse_number("abc", 7), (7, False))
        self.assertEqual(g.parse_number("-5", 7, minimum=0), (7, False))

    def test_group_by_size(self):
        cls = [(0, 0, 1, 0, 200 * MM, "A"), (0, 0, 1, 0, 200.4 * MM, "A"),
               (0, 0, 1, 0, 250 * MM, "A")]
        groups = g.group_by_size(cls, lambda c: c[4])
        self.assertEqual(sorted((k, len(v)) for k, v in groups.items()), [(200, 2), (250, 1)])

    def test_mode_table(self):
        self.assertEqual(len(g.MODE_KEYS), len(set(g.MODE_KEYS)))
        for m in g.MODES:
            self.assertIn(m["count_kind"], g.COUNT_HEADERS)
            self.assertTrue(m["verb"].startswith("Create "))
        self.assertEqual([c["key"] for c in g.MEP_CATEGORIES],
                         ["duct", "pipe", "tray", "conduit"])
        # The dialog adds the project unit to the label ("WIDTH (FT-IN)").
        for c in g.MEP_CATEGORIES:
            self.assertNotIn("(", c["width_label"], c["key"])
            self.assertEqual(c["width_label"], c["width_label"].upper(), c["key"])


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
