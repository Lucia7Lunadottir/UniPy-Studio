import tempfile
import unittest
from pathlib import Path
from main import parse_args
from studio.launch import resolve_request, apply_request


class LaunchTests(unittest.TestCase):
    def test_unity_root_with_spaces_without_solution(self):
        with tempfile.TemporaryDirectory(prefix='Unity project ') as tmp:
            root = Path(tmp)
            (root / 'ProjectSettings').mkdir()
            script = root / 'Assets/Scripts/Player.cs'
            script.parent.mkdir(parents=True)
            script.write_text('class Player {}')
            request = resolve_request(parse_args([str(script)]))
            self.assertEqual(request['root'], str(root))
            self.assertEqual(request['file'], str(script))
            request = resolve_request(parse_args(['--project', tmp, '--file', str(script), '--line', '12', '--column', '3']))
            self.assertEqual((request['line'], request['column']), (12, 3))

    def test_solution_opens_root_without_editing_solution(self):
        with tempfile.TemporaryDirectory() as tmp:
            sln = Path(tmp) / 'Game.sln'
            sln.touch()
            request = resolve_request(parse_args([str(sln)]))
            self.assertEqual(request['root'], tmp)
            self.assertIsNone(request['file'])

    def test_invalid_project_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_request(parse_args(['--project', '/nonexistent-unipy-project']))

    def test_same_project_preserves_tabs_and_cancel_stops_switch(self):
        class Window:
            root = '/project'
            opened = []
            def set_root(self, root):
                raise AssertionError('must not reset current project')
            def open_path(self, *args): self.opened.append(args)
            def showNormal(self): pass
            def raise_(self): pass
            def activateWindow(self): pass
        win = Window()
        apply_request(win, dict(root='/project', file='/project/a.cs', line=5, column=2))
        self.assertEqual(win.opened, [('/project/a.cs', 5, 2)])
        win.set_root = lambda root: False
        apply_request(win, dict(root='/other', file='/other/a.cs', line=1, column=1))
        self.assertEqual(len(win.opened), 1)


if __name__ == '__main__': unittest.main()
