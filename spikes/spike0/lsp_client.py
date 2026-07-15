"""Minimal stdio LSP client, stdlib only.

Just enough protocol to drive rust-analyzer for SPIKE-0: framing, initialize,
didOpen, requests interleaved with server->client traffic, and quiescence via
rust-analyzer's experimental serverStatus notification.  Not a general client.
"""
import json
import os
import select
import subprocess
import time
from pathlib import Path


class Lsp:
    def __init__(self, cmd, root):
        env = dict(os.environ)
        env["PATH"] = str(Path.home() / ".cargo" / "bin") + os.pathsep + env.get("PATH", "")
        self.p = subprocess.Popen(cmd, cwd=root, env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.buf = bytearray()
        self.next_id = 1
        self.server_status = {}

    def _send(self, msg):
        raw = json.dumps(msg).encode()
        self.p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(raw) + raw)
        self.p.stdin.flush()

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _read_frame(self, deadline):
        while True:
            i = self.buf.find(b"\r\n\r\n")
            if i >= 0:
                n = 0
                for line in bytes(self.buf[:i]).decode("ascii", "replace").split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        n = int(line.split(":", 1)[1])
                if len(self.buf) >= i + 4 + n:
                    body = bytes(self.buf[i + 4:i + 4 + n])
                    del self.buf[:i + 4 + n]
                    return json.loads(body)
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("lsp read timeout")
            r, _, _ = select.select([self.p.stdout], [], [], min(left, 1.0))
            if r:
                chunk = os.read(self.p.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError("rust-analyzer closed stdout")
                self.buf += chunk
            elif self.p.poll() is not None:
                raise RuntimeError("rust-analyzer exited rc=%s" % self.p.returncode)

    def _dispatch(self, msg):
        if "method" in msg and "id" in msg:  # server->client request: answer, never stall
            if msg["method"] == "workspace/configuration":
                result = [{} for _ in msg["params"]["items"]]
            else:
                result = None
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        elif "method" in msg:
            if msg["method"] == "experimental/serverStatus":
                self.server_status = msg.get("params") or {}
        return msg

    def request(self, method, params, timeout=120.0):
        rid = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            msg = self._dispatch(self._read_frame(deadline))
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    return {"__error__": msg["error"]}
                return msg.get("result")

    def wait_quiescent(self, timeout=600.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server_status.get("quiescent"):
                return True
            try:
                self._dispatch(self._read_frame(time.monotonic() + 5))
            except TimeoutError:
                pass
        return False

    def open_doc(self, path):
        text = Path(path).read_text()
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": Path(path).as_uri(), "languageId": "rust", "version": 0, "text": text}})
        return text

    def shutdown(self):
        try:
            self.request("shutdown", None, timeout=10)
            self.notify("exit")
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def start(root):
    c = Lsp(["rust-analyzer"], root)
    caps = {
        "textDocument": {
            "completion": {"completionItem": {
                "resolveSupport": {"properties": ["documentation", "detail",
                                                  "additionalTextEdits"]},
                "labelDetailsSupport": True,
                "documentationFormat": ["markdown", "plaintext"],
                "snippetSupport": True,
            }},
            "hover": {"contentFormat": ["markdown", "plaintext"]},
            "definition": {"linkSupport": True},
            "moniker": {},
        },
        "window": {"workDoneProgress": True},
        "workspace": {"configuration": True},
        "experimental": {"serverStatusNotification": True},
    }
    init = c.request("initialize", {
        "processId": os.getpid(),
        "rootUri": Path(root).as_uri(),
        "capabilities": caps,
        "workspaceFolders": [{"uri": Path(root).as_uri(), "name": Path(root).name}],
    }, timeout=60)
    c.notify("initialized", {})
    return c, init
