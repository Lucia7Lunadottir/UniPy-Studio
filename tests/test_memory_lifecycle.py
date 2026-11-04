import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
import gc
import weakref
import tempfile
import sys
import time
from pathlib import Path
from PyQt6.QtCore import QObject, QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication
from studio.lsp import LanguageService, LanguageServer, RoslynServer, JsonRpcConnection
from studio.panels import OutputPanel, TerminalPanel
from studio.log_limits import LOG_CHARS, LOG_BLOCKS, LOG_LINE_CHARS
from studio import theme


class MemoryLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def drain(self):
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        self.app.processEvents()
        gc.collect()

    def test_stopped_servers_are_deleted(self):
        class Server(QObject):
            def stop(self): pass
        service = LanguageService()
        for i in range(40):
            service.servers[str(i)] = Server(service)
            service.stop()
            self.drain()
            self.assertEqual(service.findChildren(Server), [])

    def test_real_server_python_graph_is_collected(self):
        service = LanguageService()
        refs = []
        for i in range(20):
            server = LanguageServer("/tmp", service)
            server._docs["file:///tmp/test.cs"] = "x" * 2_000_000
            refs.append(weakref.ref(server))
            service.servers[str(i)] = server
            del server
            service.stop()
            self.drain()
        self.assertTrue(all(ref() is None for ref in refs))

    def test_diagnostic_replies_do_not_trigger_new_requests(self):
        from studio.mainwindow import MainWindow
        with tempfile.TemporaryDirectory() as tmp, patch("studio.intellisense.jedi", None):
            path = Path(tmp) / "Player.cs"
            path.write_text("class Player {}\n")
            win = MainWindow(screenshot=True)
            for service in win.language_services:
                service.exe = None
            win.set_root(tmp)
            requests = []
            win.csharp.sync = lambda *args: requests.append(args)
            try:
                ed = win.open_path(str(path))
                self.assertEqual(len(requests), 1)
                error = [(1, 1, "Test error", "error")]
                # This is the actual service -> window -> editor reply path.
                for _ in range(100):
                    win._lsp_diagnostics(str(path), error)
                self.assertEqual(ed.diagnostics, error)
                win._lsp_diagnostics(str(path), [])
                self.assertEqual(ed.diagnostics, [])
                self.assertEqual(len(requests), 1, "diagnostic replies must not send more requests")
                ed.run_diagnostics()
                self.assertEqual(len(requests), 2, "explicit checks still request server diagnostics")
            finally:
                win.close_all_tabs()
                win.jedi.stop()
                for service in win.language_services:
                    service.stop()
                win.deleteLater()
                self.drain()

    def test_roslyn_configuration_protocol_returns_scalar_scopes(self):
        server = RoslynServer("/tmp", "/unused")
        replies = []
        server.conn.respond = lambda ident, value: replies.append((ident, value))
        server._on_server_request(42, "workspace/configuration", {"items": [
            {"section": "csharp|background_analysis.dotnet_analyzer_diagnostics_scope"},
            {"section": "unknown-option"},
            {"section": "csharp|background_analysis.dotnet_compiler_diagnostics_scope"},
        ]})
        self.assertEqual(replies, [(42, ["openFiles", None, "openFiles"])])
        server.stop()
        server.deleteLater()
        self.drain()

    def test_cancelled_requests_release_callbacks(self):
        conn = JsonRpcConnection()
        for _ in range(10000):
            ident = conn.request('textDocument/hover', {}, lambda *_: None)
            conn.cancel(ident)
        self.assertEqual(conn._callbacks, {})
        conn.request('shutdown', None, lambda *_: None)
        conn.stop()
        self.assertEqual(conn._callbacks, {})

    @unittest.skipUnless(hasattr(os, "killpg"), "Unix process groups")
    def test_forced_shutdown_removes_owned_descendants(self):
        conn = JsonRpcConnection()
        if not conn._isolated:
            self.skipTest("Qt cannot create a private process session")
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "child.pid"
            program = (
                "import subprocess,sys,time; from pathlib import Path; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)"
            )
            try:
                self.assertTrue(conn.start(sys.executable, ["-c", program, str(pidfile)], tmp))
                deadline = time.monotonic() + 3
                while not pidfile.exists() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.01)
                self.assertTrue(pidfile.exists())
                child = int(pidfile.read_text())
                self.assertNotEqual(conn._process_group, os.getpgrp())
            finally:
                conn.stop()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                proc = Path(f"/proc/{child}/stat")
                if not proc.exists() or proc.read_text().rsplit(")", 1)[1].split()[0] == "Z":
                    break
                time.sleep(.01)
            else:
                self.fail("analyzer descendant survived shutdown")

    def test_log_limits_include_single_long_line(self):
        output = OutputPanel(theme.code_font(11))
        terminal = TerminalPanel(theme.code_font(11))
        for _ in range(8):
            output.write('x' * 250000)
            terminal._write('x' * 250000)
        for view in (output.view, terminal.view):
            self.assertLessEqual(view.document().characterCount()-1, LOG_CHARS)
            self.assertLessEqual(view.blockCount(), LOG_BLOCKS)
            self.assertLessEqual(view.document().lastBlock().length()-1, LOG_LINE_CHARS)
        self.assertLessEqual(output._line_chars, LOG_CHARS)
        output.clear()
        self.assertEqual(output._line_chars, 0)
        output.deleteLater(); terminal.deleteLater()
        self.drain()
