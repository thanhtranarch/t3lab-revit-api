# -*- coding: utf-8 -*-
"""
CPython 3 test for the MCP point cloud tools (2026-10-02):
list_point_clouds / analyze_point_cloud / detect_point_cloud_elements.

The tools are thin handlers in core/server.py over Services/
point_cloud_analysis.py — the same engine the Point Cloud to Model wizard
runs. This drives the pure half end to end on the synthetic room from
dev/test_pointcloud_detect.py (8 x 5 x 3 m, one door, one window), plus the
server's cloud / region pickers with stub Revit objects. GetPoints itself
needs Revit and stays on the in-Revit checklist.

Run:  python3 dev/test_pointcloud_mcp.py      (exit 0 = all pass)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, 'T3Lab.extension', 'lib'))

import test_pointcloud_detect as T   # noqa: E402  synthetic clouds + stubs

FAILURES = []
MM = 304.8


def check(name, cond, detail=''):
    if cond:
        print('  ok    {}'.format(name))
    else:
        FAILURES.append(name)
        print('  FAIL  {}  {}'.format(name, detail))


class _Stub(object):
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _server_cls():
    import core.server as S
    return S.T3LabAIServer


def main():
    ns = T.load_analyzer_module()
    pts = T.synth_room()

    print('[analyze_points]')
    levels = [('Level 1', 0.0), ('Level 2', 3000.0 / MM), ('Level X', 1500.0 / MM)]
    out = ns['analyze_points'](pts, levels=levels, tolerance_mm=25.0)
    check('sampled count reported', out['sampled_points'] == len(pts))
    check('size is about 8 x 5 x 3 m',
          abs(out['size_m'][0] - 8.2) < 0.5 and abs(out['size_m'][1] - 5.2) < 0.5
          and abs(out['size_m'][2] - 3.0) < 0.3, out['size_m'])
    zs = [p['z_mm'] for p in out['horizontal_planes']]
    check('floor plane near 0 mm', any(abs(z) <= 50 for z in zs), zs)
    check('ceiling plane near 3000 mm', any(abs(z - 3000) <= 50 for z in zs), zs)
    by_level = {e['level']: e for e in out['level_check']}
    check('Level 1 matches the scan', by_level['Level 1']['status'] == 'matches scan', by_level['Level 1'])
    check('Level 2 matches the scan', by_level['Level 2']['status'] == 'matches scan', by_level['Level 2'])
    check('a level with no slab is flagged',
          by_level['Level X']['status'] == 'no scan plane within tolerance', by_level['Level X'])
    check('result is JSON-safe', bool(json.dumps(out)))
    check('empty sample says so', ns['analyze_points']([]) == {'sampled_points': 0})

    print('[detect -> summarize]')
    analyzer = ns['PointCloudAnalyzer'](pts)
    wanted = ['walls', 'doors', 'windows', 'floors', 'ceilings']
    results = analyzer.run(ns['detection_settings'](wanted))
    summary = ns['summarize_detections'](results, wanted, limit_per_type=100)
    check('four walls', summary['counts'].get('walls') == 4, summary['counts'])
    check('one door', summary['counts'].get('doors') == 1, summary['counts'])
    check('one window', summary['counts'].get('windows') == 1, summary['counts'])
    wall = summary['detections']['walls'][0]
    for key in ('start_mm', 'end_mm', 'thickness_mm', 'length_mm', 'angle_deg', 'confidence_pct'):
        check('wall carries {}'.format(key), key in wall, sorted(wall))
    check('wall thickness about 200 mm', 120 <= wall['thickness_mm'] <= 280, wall['thickness_mm'])
    door = summary['detections']['doors'][0]
    check('door sits in the y=0 wall at x 3.0-3.9 m',
          2900 <= door['location_mm'][0] <= 4000 and abs(door['location_mm'][1]) <= 300,
          door['location_mm'])
    check('door width about 900 mm', 600 <= door['width_mm'] <= 1200, door['width_mm'])
    check('detections are JSON-safe', bool(json.dumps(summary)))

    print('[element filter]')
    only_doors = ns['summarize_detections'](
        analyzer.run(ns['detection_settings'](['doors'])), ['doors'])
    check('walls run for door context but are not reported',
          list(only_doors['counts']) == ['doors'] and only_doors['counts']['doors'] == 1,
          only_doors['counts'])
    capped = ns['summarize_detections'](results, ['walls'], limit_per_type=2)
    check('limit_per_type caps the list, counts stay full',
          len(capped['detections']['walls']) == 2 and capped['counts']['walls'] == 4
          and capped['truncated'] == ['walls'], capped['counts'])

    print('[budget]')
    clamp = ns['clamp_point_budget']
    check('default budget', clamp(None) == ns['MCP_DEFAULT_POINTS'])
    check('budget capped at 100k', clamp(10 ** 9) == ns['MCP_MAX_POINTS'])
    check('budget floor 1000', clamp(5) == 1000)

    print('[server pickers]')
    S = _server_cls()
    eid = lambda e: e
    clouds = [_Stub(Id=11, Name='Scan A'), _Stub(Id=12, Name='Scan B')]
    pc, err = S._pick_point_cloud(None, [], None, eid)
    check('no cloud -> actionable error', pc is None and 'Insert > Point Cloud' in err['error'])
    pc, err = S._pick_point_cloud(None, clouds[:1], None, eid)
    check('single cloud is picked without an id', pc is clouds[0] and err is None)
    pc, err = S._pick_point_cloud(None, clouds, None, eid)
    check('several clouds -> asks for point_cloud_id', pc is None and len(err['point_clouds']) == 2)
    pc, err = S._pick_point_cloud(None, clouds, 12, eid)
    check('id selects the cloud', pc is clouds[1])

    bb = _Stub(Min=_Stub(X=0.0, Y=0.0, Z=-1.0), Max=_Stub(X=30.0, Y=20.0, Z=30.0))
    cloud = _Stub(get_BoundingBox=lambda view: bb)
    lv = [('Level 1', 0.0), ('Level 2', 10.0)]
    region, err = S._point_cloud_region(None, cloud, {'level_name': 'level 1'}, lv)
    check('level band runs to the next level', region == ((0.0, 0.0, 0.0), (30.0, 20.0, 10.0)), region)
    region, err = S._point_cloud_region(None, cloud, {'level_name': 'Level 2'}, lv)
    check('top level band is +4 m', abs(region[1][2] - (10.0 + 4000.0 / MM)) < 1e-9, region)
    region, err = S._point_cloud_region(None, cloud, {'level_name': 'Roof'}, lv)
    check('unknown level lists the real ones', region is None and err['levels'] == ['Level 1', 'Level 2'])
    region, err = S._point_cloud_region(
        None, cloud, {'region_mm': {'min': [3048, 0, 0], 'max': [0, 3048, 3048]}}, lv)
    check('region_mm is normalised to min/max feet',
          region == ((0.0, 0.0, 0.0), (10.0, 10.0, 10.0)), region)
    region, err = S._point_cloud_region(None, cloud, {'region_mm': {'min': [1, 2]}}, lv)
    check('malformed region_mm is refused', region is None and 'region_mm' in err['error'])

    print('\n{} failed'.format(len(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
