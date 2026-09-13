"""Check UI outcome classification against the shipped join-service stop contract."""
import ast
from pathlib import Path
import unittest


LIB = Path(__file__).resolve().parents[1] / 'T3Lab.extension' / 'lib'
service = ast.parse((LIB / 'Services' / 'join_service.py').read_text(encoding='utf-8'))
cancel_message = next(ast.literal_eval(node.value) for node in service.body
                      if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name)
                              and target.id == 'JOIN_CANCELLED_MESSAGE'
                              for target in node.targets))
dialog_path = LIB / 'GUI' / 'AutoJoinDialog.py'
dialog = ast.parse(dialog_path.read_text(encoding='utf-8'))
dialog.body = [node for node in dialog.body
               if isinstance(node, ast.FunctionDef) and node.name == '_join_result_text']
scope = {'JOIN_CANCELLED_MESSAGE': cancel_message}
exec(compile(dialog, str(dialog_path), 'exec'), scope)
describe = scope['_join_result_text']


class JoinResultTests(unittest.TestCase):
    def test_cancelled_commit_failure_is_not_a_successful_partial_stop(self):
        failure = 'Cancelled run commit returned Pending.'
        status, message = describe('Join', 2, 0, 3, 1, failure)
        self.assertIn('Needs attention', status)
        self.assertIn(failure, message)
        self.assertIn('Resolve any Revit failure dialog', message)
        self.assertNotIn('were committed', message)

    def test_successfully_committed_stop_discloses_partial_changes(self):
        status, message = describe('Join', 2, 4, 3, 0, cancel_message)
        self.assertIn('4 pair(s) committed', status)
        self.assertIn('stopped by request', message)
        self.assertIn('before the stop were committed', message)

    def test_item_errors_are_not_reported_as_clean_completion(self):
        status, message = describe('Unjoin', 2, 4, 3, 2, None)
        self.assertIn('Completed with errors', status)
        self.assertIn('Confirmed unjoined pairs: 4', message)
        self.assertIn('Errors: 2', message)

    def test_quick_run_failure_remains_visible(self):
        status, message = describe('Join', 2, 0, 0, 1, 'Commit rolled back.', quick=True)
        self.assertIn('Needs attention', status)
        self.assertIn('Quick Auto Join stopped with an error', message)
        self.assertIn('Commit rolled back.', message)

    def test_normal_completion_keeps_confirmed_counts(self):
        status, message = describe('Join', 2, 4, 3, 0, None)
        self.assertIn('Done', status)
        self.assertIn('Confirmed joined pairs: 4', message)
        self.assertIn('Skipped pairs: 3', message)


if __name__ == '__main__':
    unittest.main()
