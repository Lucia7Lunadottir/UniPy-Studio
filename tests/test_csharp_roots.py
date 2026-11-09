import os
import tempfile
import unittest
from pathlib import Path

from studio.lsp import find_solution_dir


class SolutionRoot(unittest.TestCase):
    def test_solution_wins_then_project_folder(self):
        root = Path(tempfile.mkdtemp())
        app = root / "App"
        (app / "src").mkdir(parents=True)
        (app / "App.csproj").write_text("<Project/>")
        code = app / "src" / "Program.cs"
        code.write_text("class P {}")
        self.assertEqual(find_solution_dir(str(code)), str(app))      # `dotnet new console` — без .sln
        (root / "All.sln").write_text("")
        self.assertEqual(find_solution_dir(str(code)), str(root))     # решение выше — как в Visual Studio
        lone = Path(tempfile.mkdtemp()) / "x.cs"
        lone.write_text("")
        self.assertIsNone(find_solution_dir(str(lone)))
        self.assertTrue(os.path.isdir(app))


if __name__ == "__main__":
    unittest.main()
