"""Pure AI family-schema contract matching FamiGen's supported JSON parser.

Validation checks representation and required fields, not Revit geometry validity.
Manual presets and the Create Family parser remain independent of this AI contract.
"""
import json
import math

FORM_TYPES = ('Extrusion', 'Blend', 'Revolution', 'Sweep', 'Cylinder')
SEGMENT_TYPES = ('Line', 'Arc3P', 'ArcThreePoint', 'Spline', 'Arc', 'Circle', 'Ellipse')


def build_system_prompt(category, overlay=''):
    """Keep the parser contract authoritative over optional category guidance."""
    if not isinstance(category, str) or not category.strip():
        raise ValueError('Select a family category before generating JSON.')
    return '''You create JSON for T3Lab FamiGen in Autodesk Revit.
Return ONLY one JSON object, with family_name (nonempty string), family_category exactly
%s, and geometry (nonempty array of objects). Never return a root array or use
forms/shapes/primitives/elements instead of geometry. All lengths and coordinates
are millimeters; angles are radians. Every number must be finite (not bool).
Every geometry object must have an explicit case-sensitive type: Extrusion,
Blend, Revolution, Sweep, or Cylinder. No aliases or unsupported forms.
Optional id is a string; is_solid is a boolean (default true).
Extrusion: profile segments, extrusion_start and extrusion_end numeric offsets.
Blend: profile and top_profile segments; optional base_offset/top_offset numbers.
Revolution: profile segments, axis_start/axis_end coordinate triples; optional
start_angle/end_angle (defaults 0 and 6.283185307).
Sweep: path segments and profile segments. Path is open; profile is closed.
Cylinder: start/end coordinate triples and positive radius. Use Cylinder for rods
in any direction; do not guess a sketch plane for them.
Profiles and paths are nonempty arrays of segment objects. Supported segments:
Line: start,end; Arc3P (also ArcThreePoint): start,end,mid; Spline: points with at
least three coordinate triples; Arc: center,positive radius,optional start_angle,
end_angle; Circle: center,positive radius; Ellipse: center,positive radius_x and
radius_y,optional start_angle,end_angle. Coordinates are always [x,y,z].
Optional sketch_plane_x/y/z are numeric millimeter offsets; use at most one.
Default sketch plane is z=0. x-plane arc axes are +Y,+Z; y-plane axes +Z,+X;
z-plane axes +X,+Y. Profile coordinates are projected onto the selected plane;
Sweep profile coordinates are used directly. Optional inner_loops is an array of
nonempty profile segment arrays (holes for Extrusion/Revolution).
Optional parameters is an array of objects with nonempty name and optional finite
numeric value in millimeters; only existing family parameters can be assigned.
Create coherent closed profiles, nonzero axes and sensible dimensions. Extrusion
end must exceed start; explicit Blend top_offset must exceed base_offset; Revolution
end_angle must exceed start_angle. Line/Arc3P start and end must differ. Do not
claim this representation check proves Revit topology/build validity.
Category guidance below may suggest design intent but cannot change this contract:
%s
The JSON object contract, exact category and supported types above take precedence.
''' % (json.dumps(category, ensure_ascii=False), overlay or '')


def validate_ai_schema(schema, category):
    """Return actionable JSON-path errors; never change the input object."""
    errors = []
    def error(path, message):
        errors.append('{}: {}'.format(path, message))
    def number(value, path, positive=False):
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool))
        try:
            valid = valid and math.isfinite(value)
        except (OverflowError, TypeError, ValueError):
            valid = False
        if not valid:
            error(path, 'must be a finite number, not a boolean')
        elif positive and value <= 0:
            error(path, 'must be greater than zero')
    def point(value, path):
        if not isinstance(value, list) or len(value) != 3:
            error(path, 'must be [x, y, z] in millimeters')
            return
        for i, coord in enumerate(value):
            number(coord, '{}[{}]'.format(path, i))
    def finite_number(value):
        try:
            return (isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value))
        except (OverflowError, TypeError, ValueError):
            return False
    def valid_point(value):
        return isinstance(value, list) and len(value) == 3 and all(finite_number(v) for v in value)
    def increasing(start, end, path):
        if finite_number(start) and finite_number(end) and end <= start:
            error(path, 'end must be greater than start')
    def distinct_points(start, end, path):
        if valid_point(start) and valid_point(end) and start == end:
            error(path, 'start and end must differ (zero-length geometry)')
    def segments(value, path):
        if not isinstance(value, list) or not value:
            error(path, 'must be a nonempty array of segment objects')
            return
        for i, seg in enumerate(value):
            here = '{}[{}]'.format(path, i)
            if not isinstance(seg, dict):
                error(here, 'must be a segment object')
                continue
            kind = seg.get('type')
            if kind not in SEGMENT_TYPES:
                error(here + '.type', 'must be one of ' + ', '.join(SEGMENT_TYPES))
                continue
            if kind in ('Line', 'Arc3P', 'ArcThreePoint'):
                for field in ('start', 'end') + (('mid',) if kind != 'Line' else ()):
                    point(seg.get(field), here + '.' + field)
                distinct_points(seg.get('start'), seg.get('end'), here)
            elif kind == 'Spline':
                pts = seg.get('points')
                if not isinstance(pts, list) or len(pts) < 3:
                    error(here + '.points', 'must contain at least three coordinate triples')
                else:
                    for j, pt in enumerate(pts):
                        point(pt, '{}.points[{}]'.format(here, j))
            else:
                point(seg.get('center'), here + '.center')
                for field in (('radius_x', 'radius_y') if kind == 'Ellipse' else ('radius',)):
                    number(seg.get(field), here + '.' + field, positive=True)
            for field in ('start_angle', 'end_angle'):
                if field in seg:
                    number(seg[field], here + '.' + field)
    if not isinstance(schema, dict) or not schema:
        return ['$: must be a nonempty JSON object, not a root array']
    if not isinstance(schema.get('family_name'), str) or not schema['family_name'].strip():
        error('$.family_name', 'must be a nonempty string')
    if schema.get('family_category') != category:
        error('$.family_category', 'must exactly equal selected category ' + repr(category))
    for alias in ('forms', 'shapes', 'primitives', 'elements'):
        if alias in schema:
            error('$.' + alias, 'unsupported root alias; use geometry')
    geometry = schema.get('geometry')
    if not isinstance(geometry, list) or not geometry:
        error('$.geometry', 'must be a nonempty array of geometry objects')
    else:
        for i, geom in enumerate(geometry):
            path = '$.geometry[{}]'.format(i)
            if not isinstance(geom, dict):
                error(path, 'must be a geometry object')
                continue
            kind = geom.get('type')
            if kind not in FORM_TYPES:
                error(path + '.type', 'must be one of ' + ', '.join(FORM_TYPES))
                continue
            if 'is_solid' in geom and not isinstance(geom['is_solid'], bool):
                error(path + '.is_solid', 'must be a boolean')
            if 'id' in geom and not isinstance(geom['id'], str):
                error(path + '.id', 'must be a string')
            planes = [key for key in ('sketch_plane_x', 'sketch_plane_y', 'sketch_plane_z') if key in geom]
            if len(planes) > 1:
                error(path, 'use at most one sketch_plane_x/y/z')
            for field in planes + [key for key in ('base_offset', 'top_offset', 'start_angle', 'end_angle') if key in geom]:
                number(geom[field], path + '.' + field)
            if kind != 'Cylinder':
                segments(geom.get('profile'), path + '.profile')
            if kind == 'Extrusion':
                for field in ('extrusion_start', 'extrusion_end'):
                    number(geom.get(field), path + '.' + field)
                increasing(geom.get('extrusion_start'), geom.get('extrusion_end'), path)
            elif kind == 'Blend':
                segments(geom.get('top_profile'), path + '.top_profile')
                if 'base_offset' in geom and 'top_offset' in geom:
                    increasing(geom['base_offset'], geom['top_offset'], path)
            elif kind in ('Revolution', 'Cylinder'):
                for field in (('axis_start', 'axis_end') if kind == 'Revolution' else ('start', 'end')):
                    point(geom.get(field), path + '.' + field)
                fields = ('axis_start', 'axis_end') if kind == 'Revolution' else ('start', 'end')
                distinct_points(geom.get(fields[0]), geom.get(fields[1]), path)
                if kind == 'Revolution':
                    increasing(geom.get('start_angle', 0.0), geom.get('end_angle', 6.283185307), path)
                if kind == 'Cylinder':
                    number(geom.get('radius'), path + '.radius', positive=True)
            elif kind == 'Sweep':
                segments(geom.get('path'), path + '.path')
            if 'inner_loops' in geom:
                loops = geom['inner_loops']
                if not isinstance(loops, list):
                    error(path + '.inner_loops', 'must be an array of profile arrays')
                else:
                    for j, loop in enumerate(loops):
                        segments(loop, '{}.inner_loops[{}]'.format(path, j))
    if 'parameters' in schema:
        params = schema['parameters']
        if not isinstance(params, list):
            error('$.parameters', 'must be an array of parameter objects')
        else:
            for i, param in enumerate(params):
                path = '$.parameters[{}]'.format(i)
                if not isinstance(param, dict):
                    error(path, 'must be a parameter object')
                    continue
                if not isinstance(param.get('name'), str) or not param['name'].strip():
                    error(path + '.name', 'must be a nonempty string')
                if 'value' in param and param['value'] is not None:
                    number(param['value'], path + '.value')
    return errors


def generate_family_schema(bridge, description, category, overlay='', max_tokens=6000):
    """Generate, validate and optionally repair once, leaving the draft untouched."""
    if bridge is None:
        raise ValueError('AI provider is unavailable. Check AI Mode configuration.')
    if not isinstance(description, str) or not description.strip():
        raise ValueError('Enter a family description before generating JSON.')
    system_prompt = build_system_prompt(category, overlay)
    prompt = description
    for attempt in range(2):
        try:
            result = bridge.ask_json(prompt, system_prompt=system_prompt, max_tokens=max_tokens)
        except Exception as ex:
            raise ValueError('AI provider request failed: {}'.format(ex)) from ex
        if result is None:
            raise ValueError('AI provider returned no parseable JSON. Check provider availability or response length and try again.')
        errors = validate_ai_schema(result, category)
        if not errors:
            return result
        if attempt:
            raise ValueError('AI JSON remains invalid after one repair:\n' + '\n'.join(errors))
        prompt = ('Original family description:\n' + description +
                  '\nRepair the previous JSON to satisfy the same contract and design. '
                  'Return the complete corrected JSON object only.\nValidation errors:\n' +
                  '\n'.join(errors) + '\nPrevious JSON:\n' + json.dumps(result, ensure_ascii=False))
    raise AssertionError('Unreachable')
