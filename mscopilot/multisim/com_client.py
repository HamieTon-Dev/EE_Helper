"""Client for the 32-bit Multisim COM worker process."""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading


class MultisimCOMError(RuntimeError):
    pass


class MultisimCOM:
    def __init__(self, command: list[str], timeout: float = 180):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=flags,
        )
        self.timeout = timeout
        self._next = 0
        self._lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put("")

    def request(self, cmd: str, timeout: float | None = None, **args):
        self._next += 1
        req = {"id": self._next, "cmd": cmd, "args": args}
        if self.proc.poll() is not None:
            raise MultisimCOMError(f"Multisim worker exited: {self._stderr()}")
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        while True:
            try:
                line = self._lines.get(timeout=timeout or self.timeout)
            except queue.Empty as e:
                raise MultisimCOMError(f"Multisim did not answer '{cmd}' in time") from e
            if not line:
                raise MultisimCOMError(f"Multisim worker exited: {self._stderr()}")
            try:
                resp = json.loads(line)
            except ValueError:
                continue  # stray output from COM libraries
            if resp.get("id") != req["id"]:
                continue
            if not resp.get("ok"):
                extra = f" (Multisim: {resp['multisim_error']})" if resp.get("multisim_error") else ""
                raise MultisimCOMError(resp.get("error", "unknown error") + extra)
            return resp.get("result")

    def _stderr(self) -> str:
        try:
            return (self.proc.stderr.read() or "").strip()[-2000:] if self.proc.stderr else ""
        except Exception:
            return ""

    # convenience wrappers
    def connect(self):
        return self.request("connect", timeout=300)

    def open(self, path: str):
        return self.request("open", path=path, timeout=300)

    def get_rlc(self, name: str) -> float:
        return self.request("get_rlc", name=name)

    def set_rlc(self, name: str, value: float) -> float:
        return self.request("set_rlc", name=name, value=value)

    def call(self, target: str, method: str, args=None, put: bool = False):
        return self.request("call", target=target, method=method, args=args or [], put=put, timeout=900)

    def introspect(self) -> dict:
        return self.request("introspect")

    def close(self):
        try:
            if self.proc.poll() is None:
                self.request("quit", timeout=30)
        except Exception:
            pass
        finally:
            if self.proc.poll() is None:
                self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def format_api_listing(api: dict) -> str:
    lines = ["Multisim Automation API members exposed by this installation", ""]
    for obj, members in api.items():
        lines.append(f"[{obj}]")
        for m in members:
            lines.append(f"  {m['kind']:<7} {m['name']}({', '.join(m['params'])})  dispid={m['dispid']}")
        lines.append("")
    return "\n".join(lines)
