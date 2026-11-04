"""Run with QT_QPA_PLATFORM=offscreen python3 tests/memory_probe.py."""
import gc
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtCore import QCoreApplication, QEvent, QProcess
from PyQt6.QtWidgets import QApplication
from studio.mainwindow import MainWindow
from studio.log_limits import LOG_CHARS, LOG_BLOCKS


def drain():
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    app.processEvents()
    gc.collect()


def rss():
    return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE') / 1048576


app = QApplication([])
win = MainWindow(screenshot=True)
# Measure editor allocations separately from external analyzer processes.
for service in win.language_services:
    service.exe = None
win.jedi.submit = lambda *args, **kwargs: None
print('Editor RSS MiB (analyzers disabled for isolation)', flush=True)
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    projects = [root/'Game A', root/'Game B']
    for project in projects:
        (project/'Assets').mkdir(parents=True)
        (project/'ProjectSettings').mkdir()
        (project/'Assets/Player.cs').write_text('class Player { int value; }\n' * 100)
    win.set_root(str(projects[0]))
    samples = []
    for cycle in range(6):
        # Normal multiline output and 1 MB without newlines per view, per cycle.
        for _ in range(4):
            win.output.write(('x'*99+'\n')*250)
            win.terminal._write(('x'*99+'\n')*250)
        for _ in range(4):
            win.output.write('x'*250000)
            win.terminal._write('x'*250000)
        assert win.output._line_chars <= LOG_CHARS
        for view in (win.output.view, win.terminal.view):
            assert view.document().characterCount() - 1 <= LOG_CHARS
            assert view.blockCount() <= LOG_BLOCKS
        print("logs finished", cycle+1, flush=True)
        # Repeated file and project lifecycle.
        for i in range(4):
            project = projects[i % 2]
            win.set_root(str(project))
            win.open_path(str(project/'Assets/Player.cs'), 12, 3)
            assert win.close_all_tabs()
            drain()
        print("file cycles finished", cycle+1, flush=True)
        # Each run must release the prior QProcess child.
        for _ in range(4):
            win.terminal.input.setText('true')
            win.terminal.run()
            assert win.terminal.proc.waitForFinished(3000)
            drain()
        assert len(win.terminal.findChildren(QProcess)) <= 1
        drain()
        samples.append(round(rss(), 1))
        print('cycle', cycle+1, 'RSS', samples[-1], 'QProcesses', len(win.findChildren(QProcess)), flush=True)
    # Qt/Python can retain high-water allocations; test continuing growth after warmup.
    assert samples[-1] - samples[1] < 32, samples
    print('PASS: final minus second cycle:', round(samples[-1]-samples[1],1), 'MiB', flush=True)
win.jedi.stop()
for service in win.language_services:
    service.stop()
