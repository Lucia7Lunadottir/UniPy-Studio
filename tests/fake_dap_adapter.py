"""Поддельный адаптер DAP для тестов: ведёт себя как debugpy в простом сценарии.
Программа «останавливается» на первой точке останова, шаг (next) переходит на строку ниже,
continue — завершает программу. Пишет полученные команды в файл из аргумента (для проверок порядка)."""
import json
import sys

LOG = open(sys.argv[1], "w", encoding="utf-8") if len(sys.argv) > 1 else None
seq = 0
breakpoints = {}
line = None
path = None


def send(msg):
    global seq
    seq += 1
    msg["seq"] = seq
    body = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    sys.stdout.buffer.flush()


def respond(req, body=None, success=True, message=None):
    msg = {"type": "response", "request_seq": req["seq"], "command": req["command"], "success": success,
           "body": body or {}}
    if message:
        msg["message"] = message
    send(msg)


def event(name, body=None):
    send({"type": "event", "event": name, "body": body or {}})


def read():
    header = b""
    while not header.endswith(b"\r\n\r\n"):
        ch = sys.stdin.buffer.read(1)
        if not ch:
            return None
        header += ch
    length = int(header.split(b"Content-Length:")[1].split(b"\r\n")[0])
    return json.loads(sys.stdin.buffer.read(length))


def stop(reason):
    event("stopped", {"reason": reason, "threadId": 1, "allThreadsStopped": True})


while True:
    req = read()
    if req is None:
        break
    cmd = req["command"]
    if LOG:
        LOG.write(cmd + "\n")
        LOG.flush()
    args = req.get("arguments") or {}
    if cmd == "initialize":
        respond(req, {"supportsConfigurationDoneRequest": True, "supportsTerminateRequest": True,
                      "exceptionBreakpointFilters": [{"filter": "uncaught", "label": "Uncaught", "default": True}]})
    elif cmd == "launch":
        path = args.get("program")
        event("initialized")
        respond(req)
    elif cmd == "setBreakpoints":
        lines = [b["line"] for b in args.get("breakpoints", [])]
        breakpoints[args["source"]["path"]] = lines
        respond(req, {"breakpoints": [{"verified": l != 99, "line": l} for l in lines]})
    elif cmd == "setExceptionBreakpoints":
        respond(req)
    elif cmd == "configurationDone":
        respond(req)
        event("output", {"category": "stdout", "output": "program started\n"})
        hits = breakpoints.get(path) or []
        if hits:
            line = min(hits)
            stop("breakpoint")
        else:
            event("exited", {"exitCode": 0})
            event("terminated")
    elif cmd == "threads":
        respond(req, {"threads": [{"id": 1, "name": "MainThread"}]})
    elif cmd == "stackTrace":
        respond(req, {"stackFrames": [
            {"id": 10, "name": "main", "line": line, "column": 1, "source": {"path": path}},
            {"id": 11, "name": "<module>", "line": 1, "column": 1, "source": {"path": path}}], "totalFrames": 2})
    elif cmd == "scopes":
        respond(req, {"scopes": [{"name": "Locals", "variablesReference": 100, "expensive": False}]})
    elif cmd == "variables":
        if args["variablesReference"] == 100:
            respond(req, {"variables": [{"name": "hp", "value": "42", "type": "int", "variablesReference": 0},
                                        {"name": "player", "value": "Player()", "type": "Player",
                                         "variablesReference": 101}]})
        else:
            respond(req, {"variables": [{"name": "name", "value": "'Hero'", "type": "str", "variablesReference": 0}]})
    elif cmd == "evaluate":
        if args["expression"] == "hp":
            respond(req, {"result": "42", "type": "int", "variablesReference": 0})
        else:
            respond(req, success=False, message="name not defined")
    elif cmd == "next":
        respond(req)
        line += 1
        stop("step")
    elif cmd == "continue":
        respond(req, {"allThreadsContinued": True})
        event("exited", {"exitCode": 3})
        event("terminated")
    elif cmd in ("terminate", "disconnect"):
        respond(req)
        if cmd == "disconnect":
            break
    else:
        respond(req, success=False, message="unknown command " + cmd)
