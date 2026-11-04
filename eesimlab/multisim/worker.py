"""Multisim COM worker (run with 32-bit Python, or as the bundled MultisimWorker.exe).

Multisim's Automation API ("MultisimInterface.MultisimApp") is a 32-bit COM server, so
it cannot be loaded into a 64-bit process. The main app starts this worker and talks to
it with one JSON object per line on stdin/stdout:

    -> {"id": 1, "cmd": "open", "args": {"path": "C:/x/amp.ms14"}}
    <- {"id": 1, "ok": true, "result": "..."}

Commands: ping, connect, open, get_rlc, set_rlc, call, introspect, disconnect, quit.
Only standard-library modules and pywin32 are used so the worker stays small.
"""
from __future__ import annotations

import json
import sys
import traceback

PROGID = "MultisimInterface.MultisimApp"
DISPATCH_METHOD = 0x1
DISPATCH_PROPERTYGET = 0x2
DISPATCH_PROPERTYPUT = 0x4
VT_BSTR, VT_R8, VT_VARIANT, VT_VOID = 8, 5, 12, 24
INVKIND = {1: "method", 2: "get", 4: "put", 8: "putref"}


class Worker:
    def __init__(self):
        self.app = None
        self.circuit = None

    # -- helpers -------------------------------------------------------
    def _target(self, name: str):
        obj = self.app if name == "app" else self.circuit
        if obj is None:
            raise RuntimeError(f"{name} is not available (connect/open first)")
        return obj

    def _last_error(self) -> str:
        for obj in (self.circuit, self.app):
            try:
                if obj is not None:
                    msg = obj.LastErrorMessage
                    if msg:
                        return str(msg)
            except Exception:
                pass
        return ""

    # -- commands ------------------------------------------------------
    def ping(self):
        return {"python": sys.version, "bits": 64 if sys.maxsize > 2**32 else 32}

    def connect(self):
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self.app = win32com.client.Dispatch(PROGID)
        self.app.Connect()
        return "connected"

    def open(self, path: str):
        self.circuit = self._target("app").OpenFile(path)
        return "opened"

    def get_rlc(self, name: str):
        return float(self._target("circuit").RLCValue(name))

    def set_rlc(self, name: str, value: float):
        # RLCValue is a parameterised property; pywin32's late binding cannot "put" it, so
        # invoke the property-put directly (DISPID looked up by name, not hard-coded).
        c = self._target("circuit")
        ole = c._oleobj_
        dispid = ole.GetIDsOfNames("RLCValue")
        ole.InvokeTypes(dispid, 0, DISPATCH_PROPERTYPUT, (VT_VOID, 0), ((VT_BSTR, 1), (VT_R8, 1)), name, float(value))
        return self.get_rlc(name)

    def call(self, target: str, method: str, args: list | None = None, put: bool = False):
        """Generic late-bound call, for API methods discovered with ``introspect``."""
        obj = self._target(target)
        args = list(args or [])
        if put:
            ole = obj._oleobj_
            dispid = ole.GetIDsOfNames(method)
            ole.Invoke(dispid, 0, DISPATCH_PROPERTYPUT, False, *args)
            return None
        attr = getattr(obj, method)
        res = attr(*args) if callable(attr) else attr
        return _jsonable(res)

    def introspect(self):
        """List every method/property the installed Multisim exposes, with parameter names."""
        out = {}
        for name in ("app", "circuit"):
            obj = self.app if name == "app" else self.circuit
            if obj is None:
                continue
            ti = obj._oleobj_.GetTypeInfo()
            attr = ti.GetTypeAttr()
            members = []
            for i in range(attr.cFuncs):
                fd = ti.GetFuncDesc(i)
                names = ti.GetNames(fd.memid)
                members.append({"name": names[0], "kind": INVKIND.get(fd.invkind, str(fd.invkind)), "dispid": fd.memid, "params": list(names[1:])})
            out[name] = sorted(members, key=lambda m: m["name"].lower())
        return out

    def disconnect(self):
        if self.app is not None:
            try:
                self.app.Disconnect()
            finally:
                self.app = None
                self.circuit = None
        return "disconnected"


def _jsonable(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return str(v)


def main() -> int:
    w = Worker()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req_id = None
        try:
            req = json.loads(line)
            req_id = req.get("id")
            cmd = req["cmd"]
            if cmd == "quit":
                w.disconnect()
                _reply({"id": req_id, "ok": True, "result": "bye"})
                return 0
            if cmd.startswith("_") or not hasattr(w, cmd):
                raise ValueError(f"unknown command {cmd}")
            result = getattr(w, cmd)(**(req.get("args") or {}))
            _reply({"id": req_id, "ok": True, "result": result})
        except Exception as e:
            _reply({"id": req_id, "ok": False, "error": f"{type(e).__name__}: {e}", "multisim_error": w._last_error(), "trace": traceback.format_exc(limit=3)})
    w.disconnect()
    return 0


def _reply(obj) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
