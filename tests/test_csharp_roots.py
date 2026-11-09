import os
import tempfile
import unittest
from pathlib import Path

from studio.lsp import RoslynServer, find_solution_dir, pick_solution
from studio.solution import load_solution


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

    def test_newest_of_sln_and_slnx_and_no_autoload(self):
        """Unity пишет то .sln, то .slnx: берём свежий, и Roslyn открывает именно его (без автозагрузки, которая
        при двух решениях грузит все .csproj папки — у неё было 376 и подсказки ждали минутами)."""
        root = Path(tempfile.mkdtemp()) / "Game"
        root.mkdir()
        sln, slnx = root / "Game.sln", root / "Game.slnx"
        sln.write_text("")
        slnx.write_text("<Solution />")
        os.utime(slnx, (1_700_000_000, 1_700_000_000))                    # .slnx старее
        self.assertEqual(pick_solution(str(root)), str(sln))
        os.utime(sln, (1_600_000_000, 1_600_000_000))
        self.assertEqual(pick_solution(str(root)), str(slnx))
        (root / "A.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk" />')
        slnx.write_text('<Solution><Project Path="A.csproj" /></Solution>')
        os.utime(slnx, (1_700_000_000, 1_700_000_000))
        self.assertEqual(load_solution(str(root)).name, "Game")            # обозреватель — то же решение
        srv = RoslynServer(str(root), "roslyn")
        self.assertNotIn("--autoLoadProjects", srv.command()[1])
        sent = []
        srv.conn.notify = lambda method, params: sent.append((method, params))
        srv.after_initialized()
        self.assertEqual(sent[0][0], "solution/open")
        self.assertTrue(sent[0][1]["solution"].endswith("Game.slnx"))
        lone = Path(tempfile.mkdtemp())
        self.assertIn("--autoLoadProjects", RoslynServer(str(lone), "roslyn").command()[1])   # только .csproj


    def test_warming_until_first_answer(self):
        """После загрузки решения сервер может ещё минуты собирать компиляции: «подготовка подсказок»,
        пока он не ответит на первый запрос, и только потом «готово»."""
        srv = RoslynServer(tempfile.mkdtemp(), "roslyn")
        srv._docs["file:///tmp/a.cs"] = (1, "class A {}")
        sent, states = [], []
        srv.conn.request = lambda method, params, callback=None: sent.append((method, callback))
        srv.warming_changed.connect(states.append)
        srv._set_state("ready")
        self.assertEqual(states, [True])
        self.assertEqual(sent[0][0], "textDocument/completion")
        sent[0][1]({"items": []}, None)                                  # сервер наконец ответил
        self.assertEqual(states, [True, False])


if __name__ == "__main__":
    unittest.main()
