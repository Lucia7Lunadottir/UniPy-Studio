"""Подсказки кода: Python/PyQt через jedi (в фоне), C# Unity — по справочнику."""
import html
import re
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from . import unity_api
from .theme import C
from .i18n import tr

try:
    import jedi
    JEDI_VERSION = jedi.__version__
except Exception:  # jedi не установлен — останутся подсказки по словам
    jedi = None
    JEDI_VERSION = None


class Item:
    __slots__ = ("name", "kind", "detail", "doc", "data")

    def __init__(self, name, kind, detail="", doc="", data=None):
        self.name, self.kind, self.detail, self.doc = name, kind, detail, doc
        self.data = data        # исходный элемент языкового сервера (для completionItem/resolve)


KIND_RU = {
    "function": "функция", "class": "класс", "instance": "переменная", "statement": "переменная",
    "param": "параметр", "module": "модуль", "keyword": "ключевое слово", "property": "свойство",
    "path": "путь", "method": "метод", "const": "значение", "event": "сообщение Unity",
    "field": "поле", "type": "тип", "word": "слово",
}


# ====================================================================== Python
class JediWorker(QObject):
    """Один фоновый поток; из очереди берётся только самый свежий запрос каждого вида."""
    finished = pyqtSignal(object)
    ready = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._cv = threading.Condition()
        self._pending = {}
        self.project_path = None
        self._project = None
        self.available = jedi is not None
        self._stopped = False
        self._thread = None     # поток (и загрузка модулей PyQt6, ~100 МБ) — только при первом запросе

    def _ensure_started(self):
        """Запуск по первому запросу: при работе только с C# (Unity) jedi не тратит память."""
        if self._thread is None and self.available and not self._stopped:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        """Остановить поток до удаления объекта: иначе он может послать сигнал уже удалённому QObject."""
        with self._cv:
            self._stopped = True
            self._pending.clear()
            self._cv.notify()
        if self._thread is not None:
            self._thread.join(1.0)

    def set_project(self, path):
        with self._cv:
            self.project_path = path
            self._project = None

    def submit(self, kind, **kw):
        if not self.available:
            return
        self._ensure_started()
        with self._cv:
            self._pending[kind] = kw
            self._cv.notify()

    def _loop(self):
        try:
            jedi.settings.case_insensitive_completion = True
            jedi.preload_module("PyQt6.QtWidgets", "PyQt6.QtCore", "PyQt6.QtGui", "sys", "os")
            msg = tr("jedi %s — модули PyQt6 загружены") % JEDI_VERSION
        except Exception as e:  # noqa
            msg = tr("jedi %s (PyQt6 не загружен: %s)") % (JEDI_VERSION, e)
        with self._cv:
            if self._stopped:
                return
            self.ready.emit(msg)
        while True:
            with self._cv:
                while not self._pending and not self._stopped:
                    self._cv.wait()
                if self._stopped:
                    return
                kind, kw = self._pending.popitem()
                if self._project is None and self.project_path:
                    try:
                        self._project = jedi.Project(self.project_path)
                    except Exception:
                        self._project = None
                project = self._project
            try:
                script = jedi.Script(kw["code"], path=kw.get("path"), project=project)
                data = getattr(self, "_" + kind)(script, kw["line"], kw["col"], **kw.get("extra", {}))
            except Exception as e:  # jedi иногда падает на недописанном коде
                data = None
            with self._cv:
                if self._stopped:
                    return
                self.finished.emit({"kind": kind, "req": kw["req"], "editor": kw["editor"], "data": data})

    @staticmethod
    def _complete(script, line, col, **_):
        items = []
        for c in script.complete(line, col)[:400]:
            detail = tr(KIND_RU.get(c.type, c.type))
            mod = c.module_name or ""
            if mod and mod not in ("builtins", "__main__") and c.type != "keyword":
                detail = "%s · %s" % (detail, mod.replace("PyQt6.", ""))
            items.append(Item(c.name, c.type, detail))
        return items

    @staticmethod
    def _signatures(script, line, col, **_):
        out = []
        for s in script.get_signatures(line, col)[:6]:
            params = [p.to_string() for p in s.params]
            out.append((s.name, params, s.index))
        return out

    @staticmethod
    def _hover(script, line, col, **_):
        names = script.help(line, col) or script.infer(line, col)
        if not names:
            return None
        n = names[0]
        head = n.full_name or n.name
        sigs = []
        try:
            sigs = [s.to_string() for s in n.get_signatures()][:3]
        except Exception:
            pass
        doc = ""
        try:
            doc = n.docstring(raw=True)
        except Exception:
            pass
        return {"title": head, "type": tr(KIND_RU.get(n.type, n.type)), "sigs": sigs, "doc": doc[:900]}

    @staticmethod
    def _references(script, line, col, **_):
        out = []
        for n in script.get_references(line, col):
            if n.module_path and n.line:
                out.append({"path": str(n.module_path), "line": n.line, "col": n.column + 1})
        return out

    @staticmethod
    def _rename(script, line, col, new_name="", **_):
        """Новый текст каждого изменённого файла (jedi сам находит все места в проекте)."""
        ref = script.rename(line, col, new_name=new_name)
        return {"files": {str(path): changed.get_new_code() for path, changed in ref.get_changed_files().items()}}

    @staticmethod
    def _definition(script, line, col, **_):
        for n in script.goto(line, col, follow_imports=True):
            if n.module_path and n.line:
                return {"path": str(n.module_path), "line": n.line}
        return None


def signature_html(name, params, index):
    parts = []
    for i, p in enumerate(params):
        p = html.escape(p)
        parts.append("<b><span style='color:%s'>%s</span></b>" % (C["bright"], p) if i == index
                     else "<span style='color:%s'>%s</span>" % (C["dim"], p))
    return "<span style='color:%s'>%s</span>(%s)" % (C["function"], html.escape(name), ", ".join(parts))


def hover_html(info):
    if not info:
        return ""
    out = ["<b>%s</b> <span style='color:%s'>(%s)</span>" % (html.escape(info["title"]), C["hint"],
                                                                html.escape(info["type"] or ""))]
    for s in info.get("sigs") or []:
        out.append("<code style='color:%s'>%s</code>" % (C["function"], html.escape(s)))
    doc = (info.get("doc") or "").strip()
    if doc:
        doc = "\n".join(doc.splitlines()[:12])
        out.append("<span style='color:%s'>%s</span>" % (C["tip_doc"], html.escape(doc).replace("\n", "<br>")))
    return "<br>".join(out)


# ====================================================================== C# / Unity
_DECL = re.compile(r"\b([A-Z]\w*|int|float|double|bool|string)(?:<(\w+)>)?(\[\])?\s+(\w+)\s*(?=[;=,)]|$)",
                   re.M)
_VAR_NEW = re.compile(r"\bvar\s+(\w+)\s*=\s*new\s+(\w+)")
_VAR_GENERIC = re.compile(r"\bvar\s+(\w+)\s*=\s*[\w.]*?\w+<(\w+)>\s*\(")
_CLASS = re.compile(r"\bclass\s+(\w+)\s*(?::\s*([\w.]+))?")
_IDENT = re.compile(r"\b[A-Za-z_]\w{2,}\b")

INHERITED_FIELDS = {"transform": "Transform", "gameObject": "GameObject"}


def _strip_type(t):
    t = re.sub(r"<.*", "", t or "")
    return t.replace("[]", "")


class CSharpProvider:
    def _locals(self, text):
        types = {}
        for m in _DECL.finditer(text):
            t, gen, arr, name = m.groups()
            if name in unity_api.CS_KEYWORDS:
                continue
            types[name] = (gen if t in ("List", "IEnumerable") and gen else t) + ("[]" if arr else "")
        for m in _VAR_NEW.finditer(text):
            types[m.group(1)] = m.group(2)
        for m in _VAR_GENERIC.finditer(text):
            types[m.group(1)] = m.group(2)
        return types

    def _own_class(self, text):
        m = _CLASS.search(text)
        if m:
            return m.group(1), (m.group(2) or "").split(".")[-1]
        return None, None

    def _resolve_chain(self, chain, text):
        """chain — список частей до точки, напр. ['GetComponent<Rigidbody>()', 'velocity'].
        Возвращает (класс, статический ли доступ)."""
        local = self._locals(text)
        own, base = self._own_class(text)
        cur, static = None, False
        for i, part in enumerate(chain):
            part = part.strip()
            gm = re.match(r"(\w+)\s*<(\w+)>\s*\(.*\)$", part)
            call = re.match(r"(\w+)\s*\(.*\)$", part)
            if i == 0:
                if gm:
                    cur, static = gm.group(2), False
                elif call:
                    mem = unity_api.find_members(base or "MonoBehaviour", call.group(1))
                    cur, static = (_strip_type(mem[0].type) if mem else None), False
                elif part == "this":
                    cur, static = own if own in unity_api.CLASSES else (base or "MonoBehaviour"), False
                elif part == "base":
                    cur, static = base or "MonoBehaviour", False
                elif part in local:
                    cur, static = _strip_type(local[part]), False
                elif part in INHERITED_FIELDS:
                    cur, static = INHERITED_FIELDS[part], False
                elif part in unity_api.CLASSES:
                    cur, static = part, True
                else:
                    return None, False
            else:
                if cur is None:
                    return None, False
                if gm:
                    cur, static = gm.group(2), False
                    continue
                name = call.group(1) if call else part
                mem = unity_api.find_members(cur, name)
                if not mem:
                    return None, False
                cur, static = _strip_type(mem[0].type), False
        return cur, static

    @staticmethod
    def split_chain(expr):
        parts, depth, buf = [], 0, ""
        for ch in expr:
            if ch in "(<[":
                depth += 1
            elif ch in ")>]":
                depth -= 1
            if ch == "." and depth == 0:
                parts.append(buf)
                buf = ""
            else:
                buf += ch
        parts.append(buf)
        return parts

    @staticmethod
    def chain_before(line_text):
        """Выражение перед последней точкой: 'transform.position.' → ['transform', 'position']."""
        m = re.search(r"((?:[\w]+(?:\s*<\w+>)?(?:\([^()]*\))?\s*\.\s*)+)(\w*)$", line_text)
        if not m:
            return None, re.search(r"(\w*)$", line_text).group(1)
        expr = m.group(1).rstrip().rstrip(".")
        return CSharpProvider.split_chain(expr), m.group(2)

    def complete(self, text, pos):
        line_start = text.rfind("\n", 0, pos) + 1
        line = text[line_start:pos]
        if self._in_comment_or_string(line):
            return []
        chain, prefix = self.chain_before(line)
        low = prefix.lower()
        items = []
        if chain:
            cls, static = self._resolve_chain(chain, text)
            if cls is None:
                return []
            seen = set()
            for m in unity_api.members_of(cls, static=static if cls in unity_api.CLASSES else None):
                if m.name in seen or not m.name.lower().startswith(low):
                    continue
                seen.add(m.name)
                items.append(Item(m.name, m.kind, m.sig, tr(m.doc)))
            return items
        # без точки: ключевые слова, классы Unity, члены MonoBehaviour, сообщения, локальные имена
        attr_ctx = re.search(r"\[\s*\w*$", line) is not None
        seen = set()

        def add(name, kind, detail, doc=""):
            if name not in seen and name.lower().startswith(low):
                seen.add(name)
                items.append(Item(name, kind, detail, doc))

        if attr_ctx:
            for name, doc in unity_api.ATTRIBUTES:
                add(name, "class", tr("атрибут"), tr(doc))
            return items
        own, base = self._own_class(text)
        for name, typ in sorted(self._locals(text).items()):
            add(name, "variable", typ)
        for m in unity_api.members_of(base or "MonoBehaviour"):
            add(m.name, m.kind, m.sig, tr(m.doc))
        for name, sig, doc in unity_api.MESSAGES:
            add(name, "event", sig, tr(doc))
        for cls, info in sorted(unity_api.CLASSES.items()):
            add(cls, "class", tr("класс Unity"), tr(info["doc"]))
        for kw in unity_api.CS_KEYWORDS:
            add(kw, "keyword", tr("ключевое слово"))
        for w in sorted(set(_IDENT.findall(text))):
            if w != prefix:
                add(w, "word", tr("из этого файла"))
        items.sort(key=lambda it: (not it.name.startswith(prefix), it.kind == "word", it.name.lower()))
        return items

    @staticmethod
    def _in_comment_or_string(line):
        stripped = line.lstrip()
        if stripped.startswith("//") or stripped.startswith("*"):
            return True
        return line.count('"') % 2 == 1

    def call_context(self, text, pos):
        """Ищет незакрытую скобку перед курсором: (имя-цепочка, номер параметра)."""
        depth, commas, i = 0, 0, pos - 1
        limit = max(0, pos - 800)
        while i >= limit:
            ch = text[i]
            if ch in ")]":
                depth += 1
            elif ch in "([":
                if depth == 0:
                    if ch == "[":
                        return None
                    head = text[max(0, i - 200):i]
                    m = re.search(r"((?:\w+(?:<\w+>)?(?:\([^()]*\))?\.)*)(\w+)\s*(?:<\w+>)?\s*$", head)
                    if not m:
                        return None
                    return m.group(1).rstrip("."), m.group(2), commas
                depth -= 1
            elif ch == "," and depth == 0:
                commas += 1
            elif ch in ";{}":
                return None
            i -= 1
        return None

    def signatures(self, text, pos):
        ctx = self.call_context(text, pos)
        if not ctx:
            return []
        receiver, name, index = ctx
        candidates = []
        if receiver:
            cls, static = self._resolve_chain(self.split_chain(receiver), text)
            if cls:
                candidates = unity_api.find_members(cls, name)
        else:
            own, base = self._own_class(text)
            candidates = unity_api.find_members(base or "MonoBehaviour", name)
            if not candidates and name in unity_api.CLASSES:
                candidates = []
            for msg, sig, doc in unity_api.MESSAGES:
                if msg == name:
                    return [(name, self._params(sig), index)]
        out = []
        for m in candidates[:6]:
            if m.kind == "method":
                out.append((m.name, self._params(m.sig), index))
        return out

    @staticmethod
    def _params(sig):
        inner = sig[sig.find("(") + 1:sig.rfind(")")].strip()
        return [p.strip() for p in inner.split(",")] if inner else []

    def hover(self, text, pos, word):
        if not word:
            return ""
        if word in unity_api.CLASSES:
            return "<b>class %s</b><br><span style='color:%s'>%s</span>" % (
                word, C["tip_doc"], html.escape(tr(unity_api.CLASSES[word]["doc"])))
        for msg, sig, doc in unity_api.MESSAGES:
            if msg == word:
                return ("<b>%s</b> <span style='color:" + C["hint"] + tr("'>(сообщение Unity)</span><br>%s")) % (
                    html.escape(sig), html.escape(tr(doc)))
        # член через точку: найдём выражение перед словом
        line_start = text.rfind("\n", 0, pos) + 1
        line = text[line_start:pos]
        chain, _ = self.chain_before(line)
        cls = None
        if chain:
            cls, _ = self._resolve_chain(chain, text)
        own, base = self._own_class(text)
        mems = unity_api.find_members(cls or base or "MonoBehaviour", word)
        if mems:
            lines = ["<code style='color:" + C["function"] + "'>%s</code>" % html.escape(
                ("static " if m.static else "") + m.sig) for m in mems[:4]]
            return ("%s<br><span style='color:" + C["tip_doc"] + "'>%s.%s — %s</span>") % (
                "<br>".join(lines), mems[0].owner, word, html.escape(tr(mems[0].doc)))
        local = self._locals(text)
        if word in local:
            return ("<b>%s</b> %s <span style='color:" + C["hint"] + tr("'>(переменная)</span>")) % (
                html.escape(local[word]), word)
        return ""


# ====================================================================== прочее
def word_items(text, prefix):
    low = prefix.lower()
    words = sorted(set(re.findall(r"\b[A-Za-z_][\w-]{2,}\b", text)))
    return [Item(w, "word", tr("из этого файла")) for w in words if w.lower().startswith(low) and w != prefix]
