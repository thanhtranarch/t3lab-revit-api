"""Pure FamiGen family-schema contract (v2: geometry + materials + parameters).

One source of truth for every FamiGen consumer:

* the in-dialog AI Generate (``generate_family_schema``),
* Copy Prompt / the MCP ``famigen_get_schema`` tool (``build_system_prompt``,
  ``schema_contract``),
* the review pane and the MCP ``famigen_propose_family`` tool
  (``validate_family_schema``),
* the shared Revit builder (``FamilyGen.builder``), which reads the category
  table and the material/parameter helpers below.

Validation checks representation and required fields, not Revit geometry
validity: profile closure, self-intersection and buildability are still Revit's
call when the family is created.

Schema v1 (no ``materials``, numeric length ``parameters``) stays valid as-is.
No Revit or WPF import here - dev/test_family_schema.py loads this file alone.
"""
import json
import math
import re

SCHEMA_VERSION = 2

FORM_TYPES = ('Extrusion', 'Blend', 'Revolution', 'Sweep', 'Cylinder')
SEGMENT_TYPES = ('Line', 'Arc3P', 'ArcThreePoint', 'Spline', 'Arc', 'Circle', 'Ellipse')

# (category, template file names tried in order, BuiltInCategory member name,
#  host-based template - True means the template cannot be emulated by
#  re-categorising a Generic Model, because the host cut belongs to the template).
CATEGORY_TABLE = (
    ("Generic Model",        ("Generic Model.rft", "Metric Generic Model.rft"), "OST_GenericModel", False),
    ("Door",                 ("Door.rft", "Metric Door.rft"), "OST_Doors", True),
    ("Window",               ("Window.rft", "Metric Window.rft"), "OST_Windows", True),
    ("Furniture",            ("Furniture.rft", "Metric Furniture.rft"), "OST_Furniture", False),
    ("Plumbing Fixture",     ("Plumbing Fixture.rft", "Metric Plumbing Fixture.rft"), "OST_PlumbingFixtures", False),
    ("Electrical Equipment", ("Electrical Equipment.rft", "Metric Electrical Equipment.rft"), "OST_ElectricalEquipment", False),
    ("Mechanical Equipment", ("Mechanical Equipment.rft", "Metric Mechanical Equipment.rft"), "OST_MechanicalEquipment", False),
    ("Specialty Equipment",  ("Specialty Equipment.rft", "Metric Specialty Equipment.rft"), "OST_SpecialityEquipment", False),
    ("Casework",             ("Casework.rft", "Metric Casework.rft"), "OST_Casework", False),
    ("Columns",              ("Column.rft", "Metric Column.rft"), "OST_Columns", False),
    ("Lighting Fixture",     ("Lighting Fixture.rft", "Metric Lighting Fixture.rft"), "OST_LightingFixtures", False),
    ("Site",                 ("Site.rft", "Metric Site.rft"), "OST_Site", False),
    ("Entourage",            ("Entourage.rft", "Metric Entourage.rft"), "OST_Entourage", False),
)
SUPPORTED_CATEGORIES = tuple(row[0] for row in CATEGORY_TABLE)

PARAMETER_TYPES = ('length', 'number', 'integer', 'text')
MATERIAL_FIELDS = ('name', 'color', 'transparency', 'shininess', 'smoothness', 'parameter')
# Revit refuses these characters in element, material, subcategory and
# parameter names. Rejecting them here gives the model a precise error path
# instead of a half-built family.
NAME_FORBIDDEN = '\\:{}[]|;<>?`~'
MAX_NAME_LENGTH = 60
_HEX_COLOR = re.compile(r'^#[0-9A-Fa-f]{6}$')


def category_templates(category):
    """Template file names for a supported category (empty tuple otherwise)."""
    for name, templates, _bic, _hosted in CATEGORY_TABLE:
        if name == category:
            return templates
    return ()


def category_bic_name(category):
    """BuiltInCategory member name for a supported category, or None."""
    for name, _templates, bic, _hosted in CATEGORY_TABLE:
        if name == category:
            return bic
    return None


def category_is_hosted(category):
    for name, _templates, _bic, hosted in CATEGORY_TABLE:
        if name == category:
            return hosted
    return False


def unsupported_category_message(category):
    return 'unsupported category {!r}; use one of: {}'.format(
        category, ', '.join(SUPPORTED_CATEGORIES))


def parse_color(value):
    """``"#RRGGBB"`` or ``[r, g, b]`` (0-255) -> (r, g, b) ints, else None."""
    if isinstance(value, str):
        if not _HEX_COLOR.match(value):
            return None
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    if isinstance(value, (list, tuple)) and len(value) == 3:
        out = []
        for channel in value:
            if isinstance(channel, bool) or not isinstance(channel, (int, float)):
                return None
            try:
                if not math.isfinite(channel):
                    return None
            except (OverflowError, TypeError, ValueError):
                return None
            if channel < 0 or channel > 255 or int(channel) != channel:
                return None
            out.append(int(channel))
        return tuple(out)
    return None


def color_hex(rgb):
    return '#{:02X}{:02X}{:02X}'.format(*rgb)


def default_material_parameter(material_name):
    """Family parameter name used for a material when none is given."""
    name = (material_name or '').strip()
    if name.lower().endswith('material'):
        return name
    return name + ' Material'


def material_parameter_name(material):
    explicit = material.get('parameter') if isinstance(material, dict) else None
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return default_material_parameter(material.get('name', '') if isinstance(material, dict) else '')


def name_problem(value):
    """Why `value` cannot be a Revit name, or None when it can."""
    if not isinstance(value, str) or not value.strip():
        return 'must be a nonempty string'
    if len(value.strip()) > MAX_NAME_LENGTH:
        return 'must be at most {} characters'.format(MAX_NAME_LENGTH)
    bad = sorted(set(ch for ch in value if ch in NAME_FORBIDDEN))
    if bad:
        return 'must not contain {}'.format(' '.join(bad))
    return None


def build_system_prompt(category, overlay=''):
    """Keep the parser contract authoritative over optional category guidance."""
    if not isinstance(category, str) or not category.strip():
        raise ValueError('Select a family category before generating JSON.')
    return '''You create JSON for T3Lab FamiGen in Autodesk Revit.
Return ONLY one JSON object, with family_name (nonempty string), family_category exactly
%s, and geometry (nonempty array of objects). Never return a root array or use
forms/shapes/primitives/elements instead of geometry. All lengths and coordinates
are millimeters; angles are radians. Every number must be finite (not bool).
Supported family_category values: %s.
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
MATERIALS (schema_version 2): optional materials is an array of objects with a
unique nonempty name, color "#RRGGBB" or [r,g,b] (0-255), optional transparency
0-100 (percent, 0 = opaque), shininess 0-128, smoothness 0-100 and parameter (the
family parameter name; default "<name> Material"). Each solid geometry object
should set material to exactly one materials[].name; voids take no material.
FamiGen creates every material, one type parameter per material in Materials and
Finishes, and binds each solid to it so finishes can change per family type.
Optional subcategory on a geometry object is a short name (for example "Legs")
created under the family category for visibility control.
Optional parameters is an array of objects with nonempty name, optional type
length (default, value in millimeters), number, integer or text, optional value
matching the type and optional instance boolean (default false). Existing
template parameters are set; missing ones are created as type parameters unless
instance is true.
Names (materials, parameter, subcategory, parameters[].name) must not contain
\\ : { } [ ] | ; < > ? ` ~ and are at most %d characters.
Create coherent closed profiles, nonzero axes and sensible dimensions. Extrusion
end must exceed start; explicit Blend top_offset must exceed base_offset; Revolution
end_angle must exceed start_angle. Line/Arc3P start and end must differ. Do not
claim this representation check proves Revit topology/build validity.
Category guidance below may suggest design intent but cannot change this contract:
%s
The JSON object contract, exact category and supported types above take precedence.
''' % (json.dumps(category, ensure_ascii=False), ', '.join(SUPPORTED_CATEGORIES),
       MAX_NAME_LENGTH, overlay or '')


def _finite_number(value):
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _validate(schema, category, strict_category):
    """(errors, warnings) for a schema. Never changes the input object."""
    errors = []
    warnings = []

    def error(path, message):
        errors.append('{}: {}'.format(path, message))

    def warn(path, message):
        warnings.append('{}: {}'.format(path, message))

    def number(value, path, positive=False, low=None, high=None):
        if not _finite_number(value):
            error(path, 'must be a finite number, not a boolean')
        elif positive and value <= 0:
            error(path, 'must be greater than zero')
        elif low is not None and high is not None and not (low <= value <= high):
            error(path, 'must be between {} and {}'.format(low, high))

    def point(value, path):
        if not isinstance(value, list) or len(value) != 3:
            error(path, 'must be [x, y, z] in millimeters')
            return
        for i, coord in enumerate(value):
            number(coord, '{}[{}]'.format(path, i))

    def valid_point(value):
        return isinstance(value, list) and len(value) == 3 and all(_finite_number(v) for v in value)

    def increasing(start, end, path):
        if _finite_number(start) and _finite_number(end) and end <= start:
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

    def revit_name(value, path):
        problem = name_problem(value)
        if problem:
            error(path, problem)
            return False
        return True

    if not isinstance(schema, dict) or not schema:
        return ['$: must be a nonempty JSON object, not a root array'], []
    if not isinstance(schema.get('family_name'), str) or not schema['family_name'].strip():
        error('$.family_name', 'must be a nonempty string')
    family_category = schema.get('family_category')
    if strict_category and family_category != category:
        error('$.family_category', 'must exactly equal selected category ' + repr(category))
    elif family_category not in SUPPORTED_CATEGORIES:
        error('$.family_category', unsupported_category_message(family_category))
    if 'schema_version' in schema and schema['schema_version'] not in (1, 2):
        error('$.schema_version', 'must be 1 or 2')
    for alias in ('forms', 'shapes', 'primitives', 'elements'):
        if alias in schema:
            error('$.' + alias, 'unsupported root alias; use geometry')

    # ── materials ────────────────────────────────────────────────────────
    material_names = set()
    if 'materials' in schema:
        materials = schema['materials']
        if not isinstance(materials, list):
            error('$.materials', 'must be an array of material objects')
        else:
            seen_params = {}
            for i, mat in enumerate(materials):
                path = '$.materials[{}]'.format(i)
                if not isinstance(mat, dict):
                    error(path, 'must be a material object')
                    continue
                name = mat.get('name')
                if revit_name(name, path + '.name'):
                    key = name.strip().lower()
                    if key in {n.lower() for n in material_names}:
                        error(path + '.name', 'duplicates another material name {!r}'.format(name))
                    material_names.add(name.strip())
                if parse_color(mat.get('color')) is None:
                    error(path + '.color', 'must be "#RRGGBB" or [r, g, b] with integers 0-255')
                if 'transparency' in mat:
                    number(mat['transparency'], path + '.transparency', low=0, high=100)
                if 'shininess' in mat:
                    number(mat['shininess'], path + '.shininess', low=0, high=128)
                if 'smoothness' in mat:
                    number(mat['smoothness'], path + '.smoothness', low=0, high=100)
                if 'parameter' in mat:
                    revit_name(mat['parameter'], path + '.parameter')
                if isinstance(name, str) and name.strip():
                    pname = material_parameter_name(mat).lower()
                    if pname in seen_params:
                        error(path + '.parameter', 'family parameter {!r} is already used by {}'
                              .format(material_parameter_name(mat), seen_params[pname]))
                    else:
                        seen_params[pname] = path
                unknown = sorted(k for k in mat if k not in MATERIAL_FIELDS)
                if unknown:
                    warn(path, 'ignored field(s): ' + ', '.join(unknown))

    # ── geometry ─────────────────────────────────────────────────────────
    used_materials = set()
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
            if 'material' in geom:
                mat_name = geom['material']
                if not isinstance(mat_name, str) or not mat_name.strip():
                    error(path + '.material', 'must be a nonempty materials[].name')
                elif mat_name.strip() not in material_names:
                    known = ', '.join(sorted(material_names)) or 'none defined'
                    error(path + '.material', 'unknown material {!r}; defined: {}'.format(mat_name, known))
                else:
                    used_materials.add(mat_name.strip())
                    if geom.get('is_solid') is False:
                        warn(path + '.material', 'voids take no material; it is ignored')
            elif material_names and geom.get('is_solid', True) is not False:
                warn(path, 'solid has no material; it keeps the category default')
            if 'subcategory' in geom:
                revit_name(geom['subcategory'], path + '.subcategory')
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
                fields = ('axis_start', 'axis_end') if kind == 'Revolution' else ('start', 'end')
                for field in fields:
                    point(geom.get(field), path + '.' + field)
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
    for name in sorted(material_names - used_materials):
        warn('$.materials', 'material {!r} is not used by any geometry'.format(name))

    # ── parameters ───────────────────────────────────────────────────────
    if 'parameters' in schema:
        params = schema['parameters']
        if not isinstance(params, list):
            error('$.parameters', 'must be an array of parameter objects')
        else:
            seen = set()
            for i, param in enumerate(params):
                path = '$.parameters[{}]'.format(i)
                if not isinstance(param, dict):
                    error(path, 'must be a parameter object')
                    continue
                pname = param.get('name')
                if revit_name(pname, path + '.name'):
                    if pname.strip().lower() in seen:
                        error(path + '.name', 'duplicates another parameter {!r}'.format(pname))
                    seen.add(pname.strip().lower())
                ptype = param.get('type', 'length')
                if ptype not in PARAMETER_TYPES:
                    error(path + '.type', 'must be one of ' + ', '.join(PARAMETER_TYPES))
                    continue
                if 'instance' in param and not isinstance(param['instance'], bool):
                    error(path + '.instance', 'must be a boolean')
                value = param.get('value')
                if value is None:
                    continue
                if ptype == 'text':
                    if not isinstance(value, str):
                        error(path + '.value', 'must be a string for a text parameter')
                elif ptype == 'integer':
                    if isinstance(value, bool) or not isinstance(value, int):
                        error(path + '.value', 'must be a whole number for an integer parameter')
                else:
                    number(value, path + '.value')
    return errors, warnings


def validate_ai_schema(schema, category):
    """Return actionable JSON-path errors; never change the input object.

    ``category`` is the category the user selected: the schema must match it
    exactly, and it must be one FamiGen has a template for.
    """
    errors, _ = _validate(schema, category, strict_category=True)
    return errors


def validate_family_schema(schema, category=None):
    """(errors, warnings) for the review pane, MCP proposals and the builder.

    With ``category`` the schema must match it; without, the schema's own
    ``family_category`` must be a supported category. Warnings never block
    creation; they explain what the builder will ignore.
    """
    return _validate(schema, category, strict_category=category is not None)


def schema_summary(schema):
    """Small, JSON-safe overview used by the review pane and MCP replies."""
    if not isinstance(schema, dict):
        return {}
    geometry = [g for g in schema.get('geometry') or [] if isinstance(g, dict)]
    materials = [m for m in schema.get('materials') or [] if isinstance(m, dict)]
    counts = {}
    for geom in geometry:
        name = geom.get('material')
        if isinstance(name, str) and geom.get('is_solid', True) is not False:
            counts[name] = counts.get(name, 0) + 1
    return {
        'family_name': schema.get('family_name'),
        'family_category': schema.get('family_category'),
        'parts': len(geometry),
        'solids': sum(1 for g in geometry if g.get('is_solid', True) is not False),
        'voids': sum(1 for g in geometry if g.get('is_solid', True) is False),
        'materials': [{'name': m.get('name'),
                       'parameter': material_parameter_name(m),
                       'solids': counts.get(m.get('name'), 0)} for m in materials],
        'parameters': len(schema.get('parameters') or []),
    }


EXAMPLE_SCHEMA = {
    'schema_version': 2,
    'family_name': 'Side Table 500',
    'family_category': 'Furniture',
    'materials': [
        {'name': 'Oak', 'color': '#B08050', 'transparency': 0, 'smoothness': 40,
         'parameter': 'Top Material'},
        {'name': 'Black Steel', 'color': [40, 40, 44], 'shininess': 90,
         'parameter': 'Frame Material'},
    ],
    'parameters': [
        {'name': 'Top Thickness', 'type': 'length', 'value': 30},
        {'name': 'Designer', 'type': 'text', 'value': 'T3Lab'},
    ],
    'geometry': [
        {'id': 'Top', 'type': 'Extrusion', 'material': 'Oak', 'subcategory': 'Top',
         'profile': [{'type': 'Circle', 'center': [0, 0, 0], 'radius': 250}],
         'extrusion_start': 520, 'extrusion_end': 550},
        {'id': 'Leg', 'type': 'Cylinder', 'material': 'Black Steel', 'subcategory': 'Legs',
         'start': [0, 0, 0], 'end': [0, 0, 521], 'radius': 25},
        {'id': 'Base', 'type': 'Extrusion', 'material': 'Black Steel', 'subcategory': 'Legs',
         'profile': [{'type': 'Circle', 'center': [0, 0, 0], 'radius': 180}],
         'extrusion_start': 0, 'extrusion_end': 12},
    ],
}


def schema_contract():
    """JSON-safe description of the contract for external AI clients (MCP)."""
    return {
        'schema_version': SCHEMA_VERSION,
        'units': {'length': 'millimeters', 'angle': 'radians'},
        'supported_categories': list(SUPPORTED_CATEGORIES),
        'form_types': list(FORM_TYPES),
        'segment_types': list(SEGMENT_TYPES),
        'material_rules': {
            'fields': {'name': 'unique, required', 'color': '"#RRGGBB" or [r,g,b] 0-255, required',
                       'transparency': '0-100 percent, optional (0 = opaque)',
                       'shininess': '0-128, optional', 'smoothness': '0-100, optional',
                       'parameter': 'family parameter name, optional (default "<name> Material")'},
            'binding': ('Every material becomes a Revit Material plus one family type '
                        'parameter (Materials and Finishes) whose default is that '
                        'material; each solid that names it is bound to the parameter.'),
            'geometry_fields': {'material': 'one materials[].name; ignored on voids',
                                'subcategory': 'optional subcategory name under the family category'},
        },
        'parameter_rules': {
            'types': list(PARAMETER_TYPES),
            'default_type': 'length',
            'length_unit': 'millimeters',
            'existing': 'set on the template parameter with the same name',
            'missing': 'created (type parameter unless instance is true)',
        },
        'forbidden_name_characters': NAME_FORBIDDEN,
        'max_name_length': MAX_NAME_LENGTH,
        'example': EXAMPLE_SCHEMA,
    }


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
