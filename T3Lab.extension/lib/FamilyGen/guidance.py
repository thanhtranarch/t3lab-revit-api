# -*- coding: utf-8 -*-
"""Optional per-category design guidance for FamiGen prompts (pure Python).

``FamiGen.pushbutton/prompts/<slug>.md`` adds category advice (proportions,
recipes, failure modes) after the authoritative schema contract. The FamiGen
dialog (AI Generate, Copy Prompt) and the MCP tool ``famigen_get_schema`` read
it from the same place through this module.
"""
import io
import os
import re


def _extension_dir():
    # lib/FamilyGen/guidance.py -> lib -> T3Lab.extension
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def category_slug(category):
    """'Plumbing Fixture' -> 'plumbing_fixture' (overlay file stem)."""
    return re.sub(r'[^a-z0-9]+', '_', (category or '').lower()).strip('_')


def prompts_dir():
    from core.extension_paths import find_bundle  # found by name, in any tab
    folder = find_bundle('FamiGen.pushbutton', _extension_dir())
    return os.path.join(folder or _extension_dir(), 'prompts')


def overlay_path(category):
    slug = category_slug(category)
    if not slug:
        return None
    return os.path.join(prompts_dir(), slug + '.md')


def read_overlay(category):
    """The guidance text for `category`, or '' when there is none."""
    path = overlay_path(category)
    if not path or not os.path.isfile(path):
        return ''
    with io.open(path, 'r', encoding='utf-8') as handle:
        return handle.read()
