"""Native BatchOut file-verification regressions using real temporary files."""
import ast
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'T3Lab.extension/lib/GUI/BatchOutDialog.py'
tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
window = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ExportManagerWindow')
methods = [n for n in window.body if isinstance(n, ast.FunctionDef)
           and n.name in ('_wait_for_export_file', '_verify_export')]
namespace = {'os': os, 'logger': Mock()}
exec(compile(ast.Module(body=methods, type_ignores=[]), str(SOURCE), 'exec'), namespace)


class ExportFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'expected.pdf'
        self.dialog = SimpleNamespace(_used_filenames=set(), _verify_misses=0)
        self.wait = lambda prev=None: namespace['_wait_for_export_file'](self.dialog, str(self.path), prev, 0)
        self.dialog._wait_for_export_file = lambda path, prev, timeout: namespace['_wait_for_export_file'](
            self.dialog, path, prev, 0)

    def test_stale_file_is_still_failure_after_timeout(self):
        self.path.write_bytes(b'old PDF')
        self.assertFalse(self.wait(self.path.stat().st_mtime))

    def test_empty_new_file_is_not_an_output(self):
        self.path.touch()
        self.assertFalse(self.wait())

    def test_directory_with_extension_is_not_an_output(self):
        self.path.mkdir()
        self.assertFalse(self.wait())

    def test_new_nonempty_file_is_verified_without_waiting(self):
        self.path.write_bytes(b'new PDF')
        self.assertTrue(self.wait())

    def test_updated_nonempty_file_is_verified(self):
        self.path.write_bytes(b'PDF')
        os.utime(self.path, (100, 100))
        old = self.path.stat().st_mtime
        os.utime(self.path, (200, 200))
        self.assertTrue(self.wait(old))

    def verify(self, started=200, expected=None, prev=None):
        return namespace['_verify_export'](self.dialog, str(expected or self.path), prev, started, '.pdf')

    def test_renamed_output_is_consumed_only_once(self):
        alt = self.folder / 'decorated.pdf'
        alt.write_bytes(b'new PDF')
        os.utime(alt, (201, 201))
        self.assertTrue(self.verify())
        self.assertFalse(self.verify(expected=self.folder / 'second.pdf'))

    def test_recent_but_preexisting_alternate_is_not_new(self):
        alt = self.folder / 'old.pdf'
        alt.write_bytes(b'old PDF')
        os.utime(alt, (199.5, 199.5))
        self.assertFalse(self.verify(started=200))

    def test_empty_alternate_is_not_accepted(self):
        alt = self.folder / 'empty.pdf'
        alt.touch()
        os.utime(alt, (201, 201))
        self.assertFalse(self.verify())

    def test_unchanged_expected_file_cannot_reenter_through_fallback(self):
        self.path.write_bytes(b'old PDF')
        os.utime(self.path, (200, 200))
        self.assertFalse(self.verify(started=200, prev=200))


if __name__ == '__main__':
    unittest.main()
