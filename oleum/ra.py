"""rust-analyzer session: stdio LSP client (stdlib only) + op extraction.

One Session per cargo workspace root, kept warm across calls.  Extraction walks
the file's semantic tokens (function/method uses), then derives each op id via
opkey from externalDocs / declaration / hover — see the SPIKE-0 decision record.
"""
import json
import os
import select
import subprocess
import time
from pathlib import Path

from . import opkey

_TOKEN_TYPES = {"function", "method"}
_SKIP_MODIFIERS = {"declaration", "definition", "documentation"}


class Session:
    def __init__(self, root, ra_bin="rust-analyzer", quiesce_timeout=300.0,
                 request_timeout=120.0):
        self.root = Path(root)
        if not (self.root / "Cargo.toml").is_file():
            raise FileNotFoundError(f"no Cargo.toml under {root}")
        self.request_timeout = request_timeout
        env = dict(os.environ)
        env["PATH"] = str(Path.home() / ".cargo" / "bin") + os.pathsep + env.get("PATH", "")
        self.p = subprocess.Popen([ra_bin], cwd=root, env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.buf = bytearray()
        self.next_id = 1
        self.server_status = {}
        self.doc_version = 0
        init = self.request("initialize", {
            "processId": os.getpid(),
            "rootUri": self.root.as_uri(),
            "capabilities": {
                "textDocument": {
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "declaration": {"linkSupport": True},
                    "definition": {"linkSupport": True},
                    "semanticTokens": {
                        "requests": {"full": True},
                        "tokenTypes": sorted(_TOKEN_TYPES),
                        "tokenModifiers": sorted(_SKIP_MODIFIERS),
                        "formats": ["relative"],
                    },
                },
                "window": {"workDoneProgress": True},
                "workspace": {"configuration": True},
                "experimental": {"serverStatusNotification": True},
            },
            "workspaceFolders": [{"uri": self.root.as_uri(), "name": self.root.name}],
        }, timeout=60)
        legend = ((init.get("capabilities") or {}).get("semanticTokensProvider")
                  or {}).get("legend") or {}
        self.token_types = legend.get("tokenTypes") or []
        self.token_modifiers = legend.get("tokenModifiers") or []
        self.notify("initialized", {})
        if not self.wait_quiescent(quiesce_timeout):
            raise TimeoutError(f"rust-analyzer never quiesced for {root}")

    # ── transport ────────────────────────────────────────────────────────────
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
                raise RuntimeError(f"rust-analyzer exited rc={self.p.returncode}")

    def _dispatch(self, msg):
        if "method" in msg and "id" in msg:      # server->client request: never stall
            result = ([{} for _ in msg["params"]["items"]]
                      if msg["method"] == "workspace/configuration" else None)
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        elif msg.get("method") == "experimental/serverStatus":
            self.server_status = msg.get("params") or {}
        return msg

    def request(self, method, params, timeout=None):
        rid = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + (timeout or self.request_timeout)
        while True:
            msg = self._dispatch(self._read_frame(deadline))
            if msg.get("id") == rid and "method" not in msg:
                return {"__error__": msg["error"]} if "error" in msg else msg.get("result")

    def wait_quiescent(self, timeout=300.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server_status.get("quiescent"):
                return True
            try:
                self._dispatch(self._read_frame(time.monotonic() + 5))
            except TimeoutError:
                pass
        return False

    def alive(self):
        return self.p.poll() is None

    def shutdown(self):
        try:
            self.request("shutdown", None, timeout=10)
            self.notify("exit")
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()

    # ── extraction ───────────────────────────────────────────────────────────
    def _use_tokens(self, uri):
        """(line, char, length) of function/method USE tokens (defs/docs skipped)."""
        res = self.request("textDocument/semanticTokens/full",
                           {"textDocument": {"uri": uri}})
        data = (res or {}).get("data") or []
        skip_mask = sum(1 << i for i, m in enumerate(self.token_modifiers)
                        if m in _SKIP_MODIFIERS)
        out, line, char = [], 0, 0
        for j in range(0, len(data), 5):
            dl, dc, length, ttype, mods = data[j:j + 5]
            line += dl
            char = (char + dc) if dl == 0 else dc
            if (0 <= ttype < len(self.token_types)
                    and self.token_types[ttype] in _TOKEN_TYPES
                    and not (mods & skip_mask)):
                out.append((line, char, length))
        return out

    def extract_ops(self, path, code=None):
        """Op ids for every keyable function/method use in the file.

        Returns (ops, unkeyed) where ops is {op_id: {"spans": [[line, char], …],
        "hint": container}} with 1-based lines, and unkeyed counts uses no
        mechanism could key (fail-open: absence of knowledge, never an error).
        """
        path = Path(path)
        text = code if code is not None else path.read_text()
        uri = path.as_uri()
        self.doc_version += 1
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": "rust", "version": self.doc_version,
            "text": text}})
        ops, unkeyed = {}, 0
        try:
            for line, char, _length in self._use_tokens(uri):
                pos = {"textDocument": {"uri": uri},
                       "position": {"line": line, "character": char}}
                use_url = self.request("experimental/externalDocs", dict(pos))
                if isinstance(use_url, dict):                # error / unsupported
                    use_url = None
                decl_url = None
                decl = self.request("textDocument/declaration", dict(pos))
                if isinstance(decl, list) and decl:
                    t = decl[0]
                    tpos = (t.get("targetSelectionRange") or t.get("range") or {}).get("start")
                    turi = t.get("targetUri") or t.get("uri")
                    if turi and tpos:
                        decl_url = self.request("experimental/externalDocs", {
                            "textDocument": {"uri": turi}, "position": tpos})
                        if isinstance(decl_url, dict):
                            decl_url = None
                hov = self.request("textDocument/hover", dict(pos))
                hover_md = (hov.get("contents") or {}).get("value", "") \
                    if isinstance(hov, dict) else ""
                op_id = opkey.synth(use_url, decl_url, hover_md)
                if op_id is None:
                    unkeyed += 1
                    continue
                rec = ops.setdefault(op_id, {"spans": [], "hint": None})
                rec["spans"].append([line + 1, char])
                if rec["hint"] is None:
                    blocks = opkey._FENCE.findall(hover_md)
                    if blocks:
                        rec["hint"] = blocks[0].strip().splitlines()[-1].strip()
        finally:
            self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        return ops, unkeyed
