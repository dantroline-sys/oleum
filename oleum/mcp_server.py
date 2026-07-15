"""oleum's MCP face: a stdio Model Context Protocol server (newline-delimited
JSON-RPC 2.0) any MCP host can mount — Claude Code, Cursor, Zed, JetBrains,
VS Code agent mode.

Three tools:
- rust_annotate   ops in a Rust file -> hazards/caveats/rank from vinur
- rust_hazards    direct op-id batch -> vinur ops_annotate passthrough
- rust_practice   prose question -> domain-scoped kb_ask

Protocol channel is stdout: NOTHING else may write there; logs go to stderr.
"""
import json
import sys
from pathlib import Path

from . import __version__, ra
from .vinur_client import Vinur

PROTOCOL_FALLBACK = "2025-06-18"

TOOLS = [
    {
        "name": "rust_annotate",
        "description": (
            "Annotate every function/method operation used in a Rust file with "
            "known hazards, caveats and learned rankings from the knowledge "
            "graph.  Call this on each changed file BEFORE finalizing Rust code. "
            "Absence of annotations means no recorded knowledge, not approval; "
            "compiler/rust-analyzer verdicts always win."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "Rust file to annotate (absolute path)"},
                "code": {"type": "string",
                         "description": "Current buffer content, if unsaved"},
                "workspace": {"type": "string",
                              "description": "Cargo workspace root (inferred from "
                                             "path when omitted)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "rust_hazards",
        "description": (
            "Look up hazards/annotations for explicit op ids "
            "(rust:op:<defining-path>#<inherent|trait|free>).  Response is a map "
            "keyed by exactly the requested ids; unknown ids come back bare."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ops": {"type": "array", "items": {"type": "string"},
                        "minItems": 1, "maxItems": 500},
            },
            "required": ["ops"],
        },
    },
    {
        "name": "rust_practice",
        "description": (
            "Ask the knowledge graph a prose question about Rust practice "
            "(idioms, pitfalls, API choices).  Grounded in curated cards; "
            "abstains rather than guessing."),
        "inputSchema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
]


class Server:
    def __init__(self, cfg):
        self.cfg = cfg
        self.vinur = Vinur(cfg["vinur_url"], cfg["vinur_token"])
        self.sessions = {}

    # ── rust-analyzer session pool ───────────────────────────────────────────
    def _workspace_for(self, path, explicit=None):
        if explicit:
            return Path(explicit)
        best = None
        for parent in Path(path).resolve().parents:
            if (parent / "Cargo.toml").is_file():
                best = parent                     # outermost wins (workspace root)
        if best:
            return best
        for w in self.cfg.get("workspaces") or []:
            if str(Path(path).resolve()).startswith(str(Path(w).resolve())):
                return Path(w)
        raise FileNotFoundError(f"no Cargo.toml above {path}")

    def _session(self, root):
        key = str(root)
        s = self.sessions.get(key)
        if s is None or not s.alive():
            log(f"spawning rust-analyzer for {key}")
            s = ra.Session(root, ra_bin=self.cfg["ra_bin"],
                           quiesce_timeout=self.cfg["ra_quiesce_timeout"],
                           request_timeout=self.cfg["request_timeout"])
            self.sessions[key] = s
        return s

    # ── tools ────────────────────────────────────────────────────────────────
    def t_rust_annotate(self, args):
        path = args["path"]
        root = self._workspace_for(path, args.get("workspace"))
        ops, unkeyed = self._session(root).extract_ops(path, args.get("code"))
        joined = self.vinur.ops_annotate(sorted(ops)) if ops else None
        out = []
        for op_id, rec in ops.items():
            ann = (joined or {}).get("annotations", {}).get(op_id)
            out.append({"id": op_id, "spans": rec["spans"], "hint": rec["hint"],
                        "annotation": ann})
        out.sort(key=lambda o: o["spans"][0])
        return {"ops": out, "unkeyed": unkeyed,
                "graph_version": (joined or {}).get("graph_version"),
                "knowledge": "ok" if joined is not None else "unavailable"}

    def t_rust_hazards(self, args):
        joined = self.vinur.ops_annotate(args["ops"])
        if joined is None:
            return {"knowledge": "unavailable"}
        joined["knowledge"] = "ok"
        return joined

    def t_rust_practice(self, args):
        res = self.vinur.kb_ask(args["question"], self.cfg["domain_facet"])
        if not res.get("ok"):
            return {"knowledge": "unavailable", "error": res.get("error")}
        return {"knowledge": "ok", "answer": res.get("result")}

    # ── MCP protocol ─────────────────────────────────────────────────────────
    def handle(self, msg):
        method, mid = msg.get("method"), msg.get("id")
        if method == "initialize":
            ver = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL_FALLBACK
            return _ok(mid, {
                "protocolVersion": ver,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "oleum", "version": __version__},
                "instructions": (
                    "Rust practice oracle.  Call rust_annotate on each changed "
                    ".rs file before finalizing; heed caveats with severity "
                    "'error'.  rust_practice answers prose questions from the "
                    "same curated knowledge."),
            })
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            return _ok(mid, {"tools": TOOLS})
        if method == "tools/call":
            p = msg.get("params") or {}
            fn = getattr(self, "t_" + str(p.get("name")), None)
            if fn is None or not any(t["name"] == p.get("name") for t in TOOLS):
                return _err(mid, -32602, f"unknown tool: {p.get('name')}")
            try:
                payload = fn(p.get("arguments") or {})
                return _ok(mid, {"content": [{"type": "text", "text": json.dumps(
                    payload, ensure_ascii=False, sort_keys=True)}],
                    "isError": False})
            except Exception as e:                       # tool errors stay in-band
                log(f"tool {p.get('name')} failed: {e!r}")
                return _ok(mid, {"content": [{"type": "text", "text": str(e)}],
                                 "isError": True})
        if mid is None:                                  # unhandled notification
            return None
        return _err(mid, -32601, f"method not found: {method}")

    def serve(self, stdin=None, stdout=None):
        fin = stdin or sys.stdin.buffer
        fout = stdout or sys.stdout.buffer
        log(f"oleum {__version__} MCP server on stdio (vinur: {self.cfg['vinur_url']})")
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                resp = _err(None, -32700, "parse error")
            else:
                resp = self.handle(msg)
            if resp is not None:
                fout.write(json.dumps(resp, ensure_ascii=False).encode() + b"\n")
                fout.flush()
        for s in self.sessions.values():
            s.shutdown()


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def log(text):
    print(f"[oleum] {text}", file=sys.stderr, flush=True)
