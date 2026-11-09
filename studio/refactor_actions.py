"""Рефакторинг и ссылки: «Найти все ссылки», переименование (F2), «Перенести тип в файл»,
действия лампочки. Правки открытых файлов идут в редактор (одно Ctrl+Z), закрытых — на диск
в их кодировке и с их переводами строк (`edit_file_on_disk`)."""
import os
import re

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from . import documents, lsp, refactor, refactor_python
from .editor import decode_source
from .i18n import plural, tr
from .theme import C

APP_NAME = "UniPy Studio"


def edit_file_on_disk(path, items):
    """Применить правки LSP к закрытому файлу. None — получилось, иначе текст ошибки для списка."""
    try:
        with open(path, "rb") as f:
            enc, text = decode_source(f.read())
        newline = "\r\n" if "\r\n" in text else "\n"
        text = lsp.apply_text_edits(text.replace("\r\n", "\n"), items)
        documents.atomic_write(path, text.replace("\n", newline).encode(enc))
    except (OSError, UnicodeError) as e:
        return "%s (%s)" % (os.path.basename(path), e)
    return None


# «Перенести в отдельный файл»: язык → метод RefactorActions. Новый язык — свой метод и строка здесь.
MOVERS = {"csharp": "_move_csharp", "python": "_move_python"}


class RefactorActions:
    def __init__(self, window):
        self.win = window
        self.rename_from = ("", "")     # (старое имя, файл, откуда начали) — для итогового сообщения

    def line_text(self, path, line):
        for ed in self.win.editors():
            if ed.path == path:
                block = ed.document().findBlockByNumber(line - 1)
                return block.text() if block.isValid() else ""
        try:
            with open(path, "rb") as f:
                text = decode_source(f.read())[1]
        except OSError:
            return ""
        lines = text.split("\n")
        return lines[line - 1].rstrip("\r") if 0 < line <= len(lines) else ""

    def show_references(self, word, refs):
        if not refs:
            self.win.status(tr("mainwindow.no_references_found") % word)
            return
        self.win.references.show_results(word, refs, self.win.root, self.line_text)
        self.win.show_dock(self.win.d_references)
        self.win.status(tr("mainwindow.references_found") % len(refs))

    def rename_dialog(self):
        ed = self.win.editor()
        if ed is None or ed.isReadOnly():
            return
        word = ed.symbol_under_cursor()
        if not word or not re.fullmatch(r"[A-Za-z_]\w*", word):
            self.win.status(tr("mainwindow.put_cursor_name_needs_renamed"))
            return
        name, ok = QInputDialog.getText(self.win, tr("common.rename"), tr("mainwindow.new_name") % word, text=word)
        name = name.strip()
        if ok and name and name != word:
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                QMessageBox.warning(self.win, APP_NAME, tr("mainwindow.invalid_name") % name)
                return
            self.rename_from = (word, ed.path)
            ed.rename_symbol(name)

    def apply_rename(self, new_name, data):
        """Правки переименования: открытые файлы меняются в редакторе (Ctrl+Z отменит), закрытые —
        пишутся на диск в их же кодировке и с их переводами строк."""
        if data.get("error"):
            QMessageBox.warning(self.win, APP_NAME, tr("mainwindow.rename_failed") % data["error"])
            return
        open_eds = {ed.path: ed for ed in self.win.editors()}
        edits = dict(data.get("edits") or {})
        skipped = []
        for path, new_code in (data.get("files") or {}).items():       # jedi: файл целиком
            ed = open_eds.get(path)
            if ed is not None and path != self.rename_from[1]:
                try:
                    with open(path, "rb") as f:
                        on_disk = decode_source(f.read())[1].replace("\r\n", "\n")
                except OSError:
                    on_disk = None
                if on_disk != ed.toPlainText():         # jedi считал с диска, а в редакторе несохранённые правки
                    skipped.append(path)
                    continue
            base = ed.toPlainText() if ed is not None else None
            if base is None:
                with open(path, "rb") as f:
                    base = decode_source(f.read())[1].replace("\r\n", "\n")
            edits[path] = lsp.line_diff_edits(base, new_code.replace("\r\n", "\n"))
        changed_files, changed_places, failed = 0, 0, []
        for path, items in edits.items():
            if not items:
                continue
            if lsp.is_metadata_path(path):
                continue
            ed = open_eds.get(path)
            if ed is not None:
                ed.apply_edits(items)
            else:
                error = edit_file_on_disk(path, items)
                if error:
                    failed.append(error)
                    continue
            changed_files += 1
            changed_places += len(items)
            self.win.output.write(tr("mainwindow.rename") % (os.path.relpath(path, self.win.root or "/"), len(items)),
                              "common.build", C["dim"])
        msg = tr("mainwindow.updated_occurrences") % (
            self.rename_from[0], new_name, changed_places, changed_files,
            plural(changed_files, "common.file_one", "common.files_few", "common.files_few"))
        self.win.status(msg)
        problems = []
        if skipped:
            problems.append(tr("mainwindow.skipped_unsaved_changes_editor") +
                            "\n".join(os.path.basename(p) for p in skipped))
        if failed:
            problems.append(tr("mainwindow.couldn_t_write") + "\n".join(failed))
        old, src = self.rename_from
        if src.endswith(".cs") and os.path.splitext(os.path.basename(src))[0] == old:
            problems.append(tr("mainwindow.name_matches_file_name_unity") % os.path.basename(src))
        if problems:
            QMessageBox.information(self.win, APP_NAME, msg + "\n\n" + "\n\n".join(problems))
        self.win.refresh_outline()

    def move_type_to_file(self):
        """Ctrl+R, Ctrl+F: тип или функция под курсором — в отдельный файл. Как именно — зависит от языка
        (MOVERS): C# — <Тип>.cs с подбором using через Roslyn, Python — модуль в snake_case с импортами."""
        ed = self.win.editor()
        mover = MOVERS.get(ed.lang) if ed is not None and not ed.isReadOnly() else None
        if mover is None:
            self.win.status(tr("mainwindow.open_your_own_c_file"))
            return
        getattr(self, mover)(ed)

    def _move_python(self, ed):
        """Класс или функция верхнего уровня → новый модуль рядом; импорты разложены, остальные файлы проекта
        переправлены на новый модуль (refactor_python)."""
        win = self.win
        text = ed.toPlainText()
        try:
            plan = refactor_python.move_symbol_plan(text, ed.textCursor().blockNumber() + 1, ed.path)
        except refactor_python.MoveError as exc:
            win.status(str(exc), 12000)
            return
        buffers = {e.path: e.toPlainText() for e in win.editors()}
        files = {}
        for path in getattr(win.explorer, "files", []):
            if path.endswith(".py") and path != ed.path:
                if path in buffers:
                    files[path] = buffers[path]
                else:
                    try:
                        with open(path, "rb") as f:
                            files[path] = decode_source(f.read())[1].replace("\r\n", "\n")
                    except OSError:
                        continue
        importers = refactor_python.update_importers(plan, files)
        try:
            documents.atomic_write(plan["new_path"], plan["new_text"].encode("utf-8"))
        except OSError as exc:
            QMessageBox.warning(win, APP_NAME, tr("mainwindow.could_not_create_file") + str(exc))
            return
        ed.apply_edits(lsp.line_diff_edits(text, plan["rest_text"]))      # одна правка — одно Ctrl+Z
        ed.save()
        failed = []
        open_eds = {e.path: e for e in win.editors()}
        for path, new in importers.items():
            if path in open_eds:
                open_eds[path].apply_edits(lsp.line_diff_edits(files[path], new))
            else:
                error = edit_file_on_disk(path, lsp.line_diff_edits(files[path], new))
                if error:
                    failed.append(error)
        win.tell_analyzers([(plan["new_path"], 1)] + [(p, 2) for p in importers])
        win.open_path(plan["new_path"])
        msg = tr("refactor.python_moved") % (plan["name"], plan["file_name"])
        if importers:
            msg += tr("refactor.python_importers") % len(importers)
        win.status(msg, 12000)
        problems = []
        if plan["circular"]:
            problems.append(tr("refactor.python_circular") % (plan["module"], plan["new_module"]))
        if failed:
            problems.append(tr("mainwindow.couldn_t_write") + "\n".join(failed))
        if problems:
            QMessageBox.information(win, APP_NAME, msg + "\n\n" + "\n\n".join(problems))

    def _move_csharp(self, ed):
        """Тип под курсором → в новый файл <Имя>.cs рядом (как «Move type to …cs» в Visual Studio).
        Если анализатор C# готов, лишние using не попадают в новый файл, а нужные только этому типу
        убираются из старого."""
        text = ed.toPlainText()
        pos = len(text.encode("utf-16-le")[:ed.textCursor().position() * 2].decode("utf-16-le", "ignore"))
        try:
            plan = refactor.move_type_plan(text, pos)
        except refactor.RefactorError as exc:
            self.win.status(str(exc), 12000)
            return
        target = os.path.join(os.path.dirname(ed.path), plan["file_name"])
        if os.path.exists(target):
            QMessageBox.information(self.win, APP_NAME, tr("mainwindow.file_already_exists_type_was") % plan["file_name"])
            return
        svc = self.win.service_for(ed.language)
        srv = svc.ready_for(ed.path) if svc is not None else None
        if srv is None or not hasattr(srv, "usings_report"):
            self.finish_move_type(ed, text, plan, target, None)
            return
        # Анализатор по очереди «видит» два варианта этого файла: только переносимый тип и всё остальное,
        # и говорит, какие using в каждом лишние. Файл на диске и вкладка при этом не меняются.
        revision = ed.document().revision()
        a, b = plan["remove"]
        rest_text = text[:a] + text[b:]
        timeout = QTimer(self.win)
        timeout.setSingleShot(True)
        state = {"done": False}

        def finish(report):
            if state["done"]:
                return
            state["done"] = True
            timeout.stop()
            timeout.deleteLater()
            self.win.busy("")
            svc.sync(ed.path, ed.toPlainText())        # серверу — обратно настоящий текст
            if ed.document().revision() != revision:
                self.win.status(tr("mainwindow.text_changed_during_analysis_repeat"))
                return
            self.finish_move_type(ed, text, plan, target, report, rest_text)

        def after_type(report_type):
            if state["done"]:
                return
            if report_type is None:
                finish(None)
                return
            srv.usings_report(ed.path, rest_text, lambda r: finish({"type": report_type, "rest": r}))

        timeout.timeout.connect(lambda: finish(None))
        timeout.start(12000)
        self.win.busy(tr("mainwindow.checking_which_usings_needed"))
        srv.usings_report(ed.path, plan["new_text"], after_type)

    def finish_move_type(self, ed, text, plan, target, report, rest_text=None):
        a, b = plan["remove"]
        if rest_text is None:
            rest_text = text[:a] + text[b:]
        new_text = plan["new_text"]
        final_text = rest_text
        removed_new = removed_old = 0
        note = ""
        if report is None:
            note = tr("mainwindow.usings_were_not_checked_c")
        else:
            l0, l1 = plan["lines"]
            errors = [d for d in ed.diagnostics if d[3] == "error"] if ed.lsp_diagnostics is not None else []
            base_type = sum(1 for d in errors if l0 <= d[0] - 1 <= l1)      # ошибки, что были в файле и до переноса
            base_rest = len(errors) - base_type
            rt, rr = report["type"], report.get("rest")
            # Если из-за переноса появились новые ошибки (тип ссылается на соседей и т. п.), анализатор мог
            # посчитать нужный using лишним — тогда ничего не убираем, чтобы не сломать сборку.
            if rt["errors"] <= base_type:
                t_map = refactor.using_lines(new_text)
                unused_type = {t for ln, t in t_map if ln in set(rt["unused"])}
                drop_new = {ln for ln, t in t_map if t in unused_type}
                new_text = refactor.remove_usings(new_text, drop_new)
                removed_new = len(drop_new)
                if rr is not None and rr["errors"] <= base_rest:
                    r_map = refactor.using_lines(rest_text)
                    unused_rest = {t for ln, t in r_map if ln in set(rr["unused"])}
                    only_for_type = unused_rest - unused_type       # нужны перенесённому типу, остальному коду — нет
                    drop_old = {ln for ln, t in r_map if t in only_for_type}
                    final_text = refactor.remove_usings(rest_text, drop_old)
                    removed_old = len(drop_old)
                else:
                    note = tr("mainwindow.usings_old_file_left_as")
            else:
                note = tr("mainwindow.usings_left_as_errors_appeared")
        data = new_text.replace("\n", ed.newline)
        try:
            raw = data.encode(ed.encoding)
        except UnicodeEncodeError:
            raw = data.encode("utf-8-sig")
        try:
            documents.atomic_write(target, raw)
        except OSError as exc:
            QMessageBox.warning(self.win, APP_NAME, tr("mainwindow.could_not_create_file") + str(exc))
            return
        ed.apply_edits(lsp.line_diff_edits(text, final_text))       # одна правка — одно Ctrl+Z
        ed.save()       # иначе до сохранения тип лежал бы в двух файлах сразу и Unity показал бы ошибку
        self.win.tell_analyzers([(target, 1)])
        self.win.open_path(target)
        msg = tr("mainwindow.type_moved") % (plan["name"], plan["file_name"])
        if removed_new or removed_old:
            msg += tr("mainwindow.unnecessary_usings_removed_new_file") % (removed_new, removed_old)
        self.win.status(msg + note, 12000)

    def apply_code_action(self, edit, title):
        """Правки выбранного действия лампочки (быстрое исправление или рефакторинг сервера)."""
        if not edit:
            self.win.status(tr("mainwindow.server_returned_no_edits_action"))
            return
        data = lsp.LanguageServer.workspace_edit_files(edit)
        failed = []
        for path in data["creates"]:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if not os.path.exists(path):
                    open(path, "wb").close()
            except OSError as exc:
                failed.append("%s (%s)" % (os.path.basename(path), exc))
        open_eds = {e.path: e for e in self.win.editors()}
        changed = 0
        for path, items in data["edits"].items():
            if not items or lsp.is_metadata_path(path):
                continue
            e = open_eds.get(path)
            if e is not None:
                e.apply_edits(items)            # в открытом файле — одним шагом отмены
            else:
                error = edit_file_on_disk(path, items)
                if error:
                    failed.append(error)
                    continue
            changed += 1
        for path in data["creates"]:
            if os.path.isfile(path):
                self.win.open_path(path)
        self.win.tell_analyzers([(p, 1) for p in data["creates"]])
        self.win.status(tr("mainwindow.done") + title, 8000)
        if failed:
            QMessageBox.warning(self.win, APP_NAME, tr("mainwindow.could_not_apply") + "\n".join(failed))
        self.win.refresh_outline()
