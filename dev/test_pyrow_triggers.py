# -*- coding: utf-8 -*-
"""Luật 28 · Trigger trên dòng Python đọc qua string bridge (thêm 2026-10-02).

pythonnet hands WPF every attribute of a Python row as a PyObject. A string
property (TextBlock.Text) converts it to text, which is why every column shows
its value; a DataTrigger compares the PyObject itself with its Value and never
matches — Model Auditor painted every HEALTH cell grey, and the amber
pending-edit cells of Sheet / View / Group / Location Manager never showed.

  - the audit rule (dev/audit_t3.py → pyrow_trigger_bindings, P1)
  - every shipped tool XAML is clean, and the fixed tools read their row fields
    through a bridge that really exists (hidden TextBlock in the same template,
    or a Setter on the styled element's own string property)

Run: python3 dev/test_pyrow_triggers.py
"""
import importlib.util
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "T3Lab.extension", "lib", "GUI", "Tools")
_spec = importlib.util.spec_from_file_location(
    "audit_t3", os.path.join(REPO, "dev", "audit_t3.py"))
audit_t3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_t3)

KEYS = audit_t3.stylesheet_keys()
P = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
X = "{http://schemas.microsoft.com/winfx/2006/xaml}"
NS = ('xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" '
      'xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"')
BRIDGE_PROP = "AutomationProperties.ItemStatus"

# The nine tools fixed 2026-10-02 and how many row-field triggers each reads
# through a bridge now (hidden TextBlock / own Text / own ItemStatus).
FIXED = {
    "BatchLink.xaml": 8,
    "ManaSheets.xaml": 6,
    "ManaViews.xaml": 6,
    "ManaLoca.xaml": 6,
    "ManaGroup.xaml": 1,
    "ManaFami.xaml": 1,
    "DWGManagement.xaml": 2,
    "ExportManager.xaml": 1,
    "ExportManagerTest.xaml": 1,
}


def xaml(body):
    return '<Grid %s>%s</Grid>' % (NS, body)


def rule28(src, base="Tool.xaml"):
    issues, _, _, _ = audit_t3.audit(src, base, KEYS)
    return [i for i in issues if "so PyObject" in i[1]]


def template(trigger_attrs, extra=""):
    return ('<ItemsControl><ItemsControl.ItemTemplate><DataTemplate>'
            '<Border x:Name="pill">%s</Border>'
            '<DataTemplate.Triggers><DataTrigger %s Value="Danger">'
            '<Setter TargetName="pill" Property="Opacity" Value="0.5"/>'
            '</DataTrigger></DataTemplate.Triggers>'
            '</DataTemplate></ItemsControl.ItemTemplate></ItemsControl>'
            % (extra, trigger_attrs))


class AuditRule(unittest.TestCase):
    def test_bare_field_is_p1(self):
        found = rule28(xaml(template('Binding="{Binding severity}"')))
        self.assertEqual([s for s, _ in found], ["P1"])
        self.assertIn("{Binding severity}", found[0][1])
        self.assertIn("ElementName", found[0][1])   # the message names the fix

    def test_path_and_mode_forms_are_still_bare(self):
        for b in ('{Binding Path=severity}', '{Binding severity, Mode=OneWay}',
                  '{Binding row.severity}', '{Binding}'):
            self.assertEqual(len(rule28(xaml(template('Binding="%s"' % b)))), 1, b)

    def test_bridge_and_wpf_sources_are_clean(self):
        for b in ('{Binding Text, ElementName=sev_text}',
                  '{Binding ElementName=grid, Path=HasItems}',
                  '{Binding HasItems, ElementName=grid}',
                  '{Binding IsMouseOver, RelativeSource={RelativeSource AncestorType=Border}}',
                  '{Binding Text, RelativeSource={RelativeSource Self}}',
                  '{Binding Source={x:Static SystemParameters.HighContrast}}'):
            self.assertEqual(rule28(xaml(template('Binding="%s"' % b))), [], b)

    def test_multidatatrigger_condition_is_checked(self):
        body = ('<Border><Border.Style><Style TargetType="Border"><Style.Triggers>'
                '<MultiDataTrigger><MultiDataTrigger.Conditions>'
                '<Condition Binding="{Binding dirty_name}" Value="True"/>'
                '<Condition Binding="{Binding IsMouseOver, RelativeSource={RelativeSource Self}}" Value="True"/>'
                '</MultiDataTrigger.Conditions>'
                '<Setter Property="Opacity" Value="0.5"/></MultiDataTrigger>'
                '</Style.Triggers></Style></Border.Style></Border>')
        found = rule28(xaml(body))
        self.assertEqual(len(found), 1)
        self.assertIn("dirty_name", found[0][1])

    def test_element_form_binding_is_checked(self):
        bare = ('<DataTrigger Value="True"><DataTrigger.Binding><Binding Path="dirty_name"/>'
                '</DataTrigger.Binding><Setter Property="Opacity" Value="0.5"/></DataTrigger>')
        named = bare.replace('<Binding Path="dirty_name"/>',
                             '<Binding Path="Text" ElementName="name_text"/>')
        wrap = ('<Border><Border.Style><Style TargetType="Border"><Style.Triggers>%s'
                '</Style.Triggers></Style></Border.Style></Border>')
        relative = bare.replace(
            '<Binding Path="dirty_name"/>',
            '<Binding Path="IsMouseOver"><Binding.RelativeSource>'
            '<RelativeSource Mode="Self"/></Binding.RelativeSource></Binding>')
        self.assertEqual(len(rule28(xaml(wrap % bare))), 1)
        self.assertEqual(rule28(xaml(wrap % named)), [])
        self.assertEqual(rule28(xaml(wrap % relative)), [])

    def test_cell_bridge_property_trigger_is_clean(self):
        body = ('<DataGrid><DataGrid.Columns><DataGridTextColumn Binding="{Binding name}">'
                '<DataGridTextColumn.CellStyle><Style TargetType="DataGridCell">'
                '<Setter Property="%s" Value="{Binding dirty_name}"/>'
                '<Style.Triggers><Trigger Property="%s" Value="True">'
                '<Setter Property="Opacity" Value="0.5"/></Trigger></Style.Triggers>'
                '</Style></DataGridTextColumn.CellStyle></DataGridTextColumn>'
                '</DataGrid.Columns></DataGrid>' % (BRIDGE_PROP, BRIDGE_PROP))
        self.assertEqual(rule28(xaml(body)), [])

    def test_trigger_inside_synced_style_block_is_ignored(self):
        block = ('%s -->\n<Style x:Key="T3.X" TargetType="Border"><Style.Triggers>'
                 '<DataTrigger Binding="{Binding Severity}" Value="Danger">'
                 '<Setter Property="Opacity" Value="0.5"/></DataTrigger>'
                 '</Style.Triggers></Style>\n%s'
                 % (audit_t3.SYNC_BEGIN, audit_t3.SYNC_END))
        src = '<Grid %s><Grid.Resources>%s</Grid.Resources></Grid>' % (NS, block)
        self.assertEqual(rule28(src), [])

    def test_exempt_file_is_skipped(self):
        src = xaml(template('Binding="{Binding severity}"'))
        for base in audit_t3.PYROW_TRIGGER_EXEMPT:
            self.assertEqual(rule28(src, base), [], base)
        self.assertIn("ManaAnno.xaml", audit_t3.PYROW_TRIGGER_EXEMPT)


class ShippedXaml(unittest.TestCase):
    """No tool XAML compares a Python row field in a trigger any more."""

    @staticmethod
    def _body_root(fname):
        with open(os.path.join(TOOLS, fname), encoding="utf-8-sig") as fh:
            src = fh.read()
        if audit_t3.SYNC_BEGIN in src and audit_t3.SYNC_END in src:
            i = src.index(audit_t3.SYNC_BEGIN)
            j = src.index(audit_t3.SYNC_END, i) + len(audit_t3.SYNC_END)
            src = src[:i] + src[j:]
        return ET.fromstring(src)

    def test_no_tool_has_a_bare_row_trigger(self):
        bad = []
        for fname in sorted(os.listdir(TOOLS)):
            if not fname.endswith(".xaml") or fname in audit_t3.PYROW_TRIGGER_EXEMPT:
                continue
            for b in audit_t3.pyrow_trigger_bindings(self._body_root(fname)):
                bad.append("%s %s" % (fname, b))
        self.assertEqual(bad, [])

    @staticmethod
    def _template_of(el, parent):
        while el in parent and el.tag != P + "DataTemplate":
            el = parent[el]
        return el if el.tag == P + "DataTemplate" else None

    def _bridged_fields(self, fname):
        """[(kind, row field)] for every trigger of `fname` that reads a row field
        through a bridge: a hidden TextBlock in its DataTemplate ("textblock"),
        the styled element's own ItemStatus ("itemstatus") or own Text ("text")."""
        root = self._body_root(fname)
        parent = {c: p for p in root.iter() for c in p}
        out = []
        for trig in root.iter(P + "DataTrigger"):
            m = re.match(r"^\{Binding Text, ElementName=(\w+)\}$", trig.get("Binding") or "")
            scope = self._template_of(trig, parent)
            if not m or scope is None:
                continue                      # window-level element bindings
            named = [e for e in scope.iter() if e.get(X + "Name") == m.group(1)]
            self.assertEqual(len(named), 1, "%s: %s not in its template" % (fname, m.group(1)))
            bridge = named[0]
            self.assertEqual(bridge.tag, P + "TextBlock", fname)
            self.assertEqual(bridge.get("Visibility"), "Collapsed", fname)
            field = re.match(r"^\{Binding (\w+)\}$", bridge.get("Text") or "")
            self.assertIsNotNone(field, "%s: %s is not bound to a plain field" % (fname, m.group(1)))
            out.append(("textblock", field.group(1)))
        for style in root.iter(P + "Style"):
            for trig in style.iter(P + "Trigger"):
                if trig.get("Property") == BRIDGE_PROP:
                    setters = [x for x in style.findall(P + "Setter")
                               if x.get("Property") == BRIDGE_PROP]
                    self.assertEqual(len(setters), 1, fname)
                    field = re.match(r"^\{Binding (\w+)\}$", setters[0].get("Value") or "")
                    self.assertIsNotNone(field, fname)
                    out.append(("itemstatus", field.group(1)))
                elif trig.get("Property") == "Text":
                    holder = parent.get(style)                 # <TextBlock.Style>
                    owner = parent.get(holder) if holder is not None else None
                    if owner is None or owner.tag != P + "TextBlock" or \
                            self._template_of(owner, parent) is None:
                        continue                               # not a row element
                    field = re.match(r"^\{Binding (\w+)\}$", owner.get("Text") or "")
                    self.assertIsNotNone(field, fname)
                    out.append(("text", field.group(1)))
        return out

    def test_each_fixed_tool_reads_its_fields_through_a_bridge(self):
        for fname, want in sorted(FIXED.items()):
            self.assertEqual(len(self._bridged_fields(fname)), want, fname)

    def test_bridges_read_the_fields_the_old_triggers_read(self):
        fields = lambda f: sorted({fld for _, fld in self._bridged_fields(f)})
        self.assertEqual(fields("BatchLink.xaml"), ["WorksetEditable", "row_state"])
        self.assertEqual(fields("ManaFami.xaml"), ["IsChecked"])
        self.assertEqual(fields("DWGManagement.xaml"), ["DWGType"])
        self.assertEqual(fields("ExportManager.xaml"), ["Status"])
        self.assertEqual(fields("ManaGroup.xaml"), ["dirty_NewName"])
        self.assertEqual(fields("ManaLoca.xaml"),
                         sorted(p + a + "_mm" for p in ("dirty_", "odd_") for a in "xyz"))
        self.assertEqual(fields("ManaSheets.xaml"),
                         sorted("dirty_" + f for f in (
                             "sheet_number", "sheet_name", "designed_by",
                             "checked_by", "approved_by", "drawn_by")))
        self.assertEqual(fields("ManaViews.xaml"),
                         sorted("dirty_" + f for f in (
                             "name", "view_template", "scale", "detail_level",
                             "title_on_sheet")))

    def test_status_text_hides_on_its_own_text(self):
        """BatchOut queue: the Status line collapses while empty — read from the
        TextBlock's own Text, which is the bound Status as a real string."""
        for fname in ("ExportManager.xaml", "ExportManagerTest.xaml"):
            root = self._body_root(fname)
            hits = [tb for tb in root.iter(P + "TextBlock")
                    if tb.get("Text") == "{Binding Status}"]
            self.assertEqual(len(hits), 1, fname)
            tb = hits[0]
            # rule 18: Style attribute AND <TextBlock.Style> crash at parse
            self.assertIsNone(tb.get("Style"), fname)
            trig = next(tb.iter(P + "Trigger"), None)
            self.assertIsNotNone(trig, fname + ": Status line has no Text trigger")
            self.assertEqual((trig.get("Property"), trig.get("Value")), ("Text", ""), fname)

    def test_dwg_type_badge_reads_its_own_text(self):
        root = self._body_root("DWGManagement.xaml")
        badge = [tb for tb in root.iter(P + "TextBlock") if tb.get("Text") == "{Binding DWGType}"]
        self.assertEqual(len(badge), 1)
        values = sorted(t.get("Value") for t in badge[0].iter(P + "Trigger")
                        if t.get("Property") == "Text")
        self.assertEqual(values, ["Import", "Link"])


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
