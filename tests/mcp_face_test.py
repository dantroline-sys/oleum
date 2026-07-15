"""oleum MCP face, end to end: real rust-analyzer over the golden fixture
workspace, stub vinur host, the server driven through its actual stdio
transport.  Stdlib only; needs the pinned toolchain (rust-analyzer on PATH or
in ~/.cargo/bin)."""
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WS = REPO / "fixtures" / "join_ws"
COMPLETED = WS / "app" / "src" / "bin" / "completed.rs"
KNOWN = "rust:op:alloc::vec::Vec::push#inherent"
FAILED = []


def check(label, cond):
    print(("  ok  " if cond else "FAIL  ") + label)
    if not cond:
        FAILED.append(label)


# ── stub vinur host: /call answering ops_annotate + kb_ask, OPS-01-shaped ──────
class StubVinur(BaseHTTPRequestHandler):
    seen = {"auth": None, "ops": None}

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        StubVinur.seen["auth"] = self.headers.get("Authorization")
        if req["name"] == "ops_annotate":
            ops = req["arguments"]["ops"]
            StubVinur.seen["ops"] = ops
            ann = {o: ({"annotated": True, "display": "Vec::push",
                        "caveats": [{"card_id": "rust:diag:card:E0502",
                                     "severity": "error",
                                     "title": "double mutable borrow"}],
                        "rank": None, "rank_specificity": None,
                        "anti_pattern_of": []}
                       if o == KNOWN else {"annotated": False})
                   for o in ops}
            res = {"annotations": ann, "graph_version": "sha256:stub",
                   "requested": len(ops),
                   "joined": sum(1 for o in ops if o == KNOWN)}
            out = {"ok": True, "result": json.dumps(res, sort_keys=True)}
        elif req["name"] == "kb_ask":
            out = {"ok": True, "result": json.dumps(
                {"answer": "prefer Vec::with_capacity when the size is known",
                 "facets": req["arguments"].get("facets")})}
        else:
            out = {"ok": False, "error": "unknown tool"}
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


# ── newline-delimited JSON-RPC over the child's pipes ──────────────────────────
class Mcp:
    def __init__(self, cfg_path, errlog):
        env = dict(os.environ)
        env["PATH"] = str(Path.home() / ".cargo" / "bin") + os.pathsep + env.get("PATH", "")
        self.p = subprocess.Popen([sys.executable, "-m", "oleum", "--config",
                                   str(cfg_path)], cwd=REPO, env=env,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=open(errlog, "wb"))
        self.next_id = 1

    def rpc(self, method, params=None, timeout=180.0):
        rid = self.next_id
        self.next_id += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                       "method": method,
                                       "params": params or {}}).encode() + b"\n")
        self.p.stdin.flush()
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while time.monotonic() < deadline:
            r, _, _ = select.select([self.p.stdout], [], [], 1.0)
            if not r:
                continue
            chunk = os.read(self.p.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("server closed stdout")
            buf += chunk
            while b"\n" in buf:
                line, _, rest = bytes(buf).partition(b"\n")
                buf = bytearray(rest)
                msg = json.loads(line)
                if msg.get("id") == rid:
                    return msg
        raise TimeoutError(method)

    def notify(self, method, params=None):
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method,
                                       "params": params or {}}).encode() + b"\n")
        self.p.stdin.flush()

    def tool(self, name, arguments, timeout=180.0):
        msg = self.rpc("tools/call", {"name": name, "arguments": arguments}, timeout)
        res = msg.get("result") or {}
        text = (res.get("content") or [{}])[0].get("text", "")
        try:
            payload = json.loads(text)
        except ValueError:
            payload = text
        return res.get("isError"), payload


def main():
    have_ra = bool(shutil.which("rust-analyzer",
                                path=str(Path.home() / ".cargo" / "bin")
                                + os.pathsep + os.environ.get("PATH", "")))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubVinur)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    tmp = Path(tempfile.mkdtemp(prefix="oleum-test-"))
    cfg = tmp / "oleum.toml"
    cfg.write_text(f'vinur_url = "http://127.0.0.1:{httpd.server_address[1]}"\n'
                   f'vinur_token = "tok123"\n')
    m = Mcp(cfg, tmp / "stderr.log")

    init = m.rpc("initialize", {"protocolVersion": "2025-06-18",
                                "capabilities": {}, "clientInfo": {"name": "t"}})
    r = init.get("result") or {}
    check("initialize echoes protocol + names server",
          r.get("protocolVersion") == "2025-06-18"
          and (r.get("serverInfo") or {}).get("name") == "oleum")
    m.notify("notifications/initialized")
    check("ping", m.rpc("ping").get("result") == {})
    tools = [t["name"] for t in (m.rpc("tools/list").get("result") or {}).get("tools", [])]
    check("three tools advertised",
          tools == ["rust_annotate", "rust_hazards", "rust_practice"])
    check("unknown tool -> protocol error",
          "error" in m.rpc("tools/call", {"name": "nope", "arguments": {}}))

    err, pay = m.tool("rust_practice", {"question": "when to preallocate a Vec?"})
    ans = json.loads(pay["answer"])
    check("rust_practice relays the domain-scoped answer",
          not err and pay["knowledge"] == "ok"
          and ans["facets"] == {"domain": ["rust-coding"]}
          and "with_capacity" in ans["answer"])
    check("bearer token forwarded", StubVinur.seen["auth"] == "Bearer tok123")

    err, pay = m.tool("rust_hazards", {"ops": [KNOWN, "rust:op:acme::x#free"]})
    check("rust_hazards keys the response by exactly the requested ids",
          not err and set(pay["annotations"]) == {KNOWN, "rust:op:acme::x#free"}
          and pay["annotations"][KNOWN]["caveats"][0]["severity"] == "error")

    if not have_ra:
        print("  (rust-analyzer missing — annotate checks skipped)")
    else:
        err, pay = m.tool("rust_annotate", {"path": str(COMPLETED)})
        ids = {o["id"]: o for o in pay.get("ops", [])} if not err else {}
        check("annotate joins the known op with its hazard",
              not err and KNOWN in ids
              and ids[KNOWN]["annotation"]["caveats"][0]["card_id"]
              == "rust:diag:card:E0502")
        check("free fn keyed", "rust:op:core::mem::swap#free" in ids)
        check("trait method keyed on the trait's defining path",
              "rust:op:ext::SliceStats::mean#trait" in ids)
        check("primitive method keyed via hover fallback",
              "rust:op:core::str::trim#inherent" in ids)
        check("spans are 1-based [line, char] on the known op",
              ids.get(KNOWN, {}).get("spans")
              and all(len(s) == 2 and s[0] > 0 for s in ids[KNOWN]["spans"]))
        check("knowledge ok + graph_version relayed",
              pay["knowledge"] == "ok" and pay["graph_version"] == "sha256:stub")
        check("vinur got sorted unique ids", StubVinur.seen["ops"]
              == sorted(set(StubVinur.seen["ops"])))

        code = COMPLETED.read_text().replace("v.push(1);", "v.push(1);\n    v.pop();")
        err, pay2 = m.tool("rust_annotate", {"path": str(COMPLETED), "code": code})
        ids2 = {o["id"] for o in pay2.get("ops", [])} if not err else set()
        check("unsaved buffer override annotates the overlay (warm session)",
              not err and "rust:op:alloc::vec::Vec::pop#inherent" in ids2)

        err, _ = m.tool("rust_annotate", {"path": "/nonexistent/x.rs"})
        check("file outside any workspace -> in-band tool error", err is True)

    m.p.stdin.close()
    m.p.wait(timeout=30)
    check("clean exit on stdin close", m.p.returncode == 0)
    httpd.shutdown()

    if FAILED:
        print(f"\n{len(FAILED)} FAILED  (stderr: {tmp / 'stderr.log'})")
        raise SystemExit(1)
    print("\nALL OK")


if __name__ == "__main__":
    main()
