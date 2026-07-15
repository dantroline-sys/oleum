"""rustc adjudication probes (AMIGA-RUST-02): rustc is the judge, always.

run_probe(snippet) compiles a snippet in a throwaway pinned crate, collects
rustc's diagnostics (cargo check) and rust-analyzer's native diagnostics for
the same code, and records where they diverge:

- rustc_only  r-a blind spots (expected — e.g. full borrowck is rustc-only);
              harmless for legality because rustc always adjudicates
- ra_only     r-a false positives — the dangerous direction, since the
              annotation lane trusts r-a's semantic model

Divergence rows accumulate in var/probes.db (oleum-side store; never the kb).
This is also the researcher lane's trust gate: a snippet that doesn't compile
under the pinned toolchain must never become a card.

CLI: python3 -m oleum.probe <file.rs> [--keep]
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from . import ra


def _env():
    env = dict(os.environ)
    env["PATH"] = str(Path.home() / ".cargo" / "bin") + os.pathsep + env.get("PATH", "")
    return env

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "var" / "probes.db"
_SEV = {1: "error", 2: "warning", 3: "info", 4: "hint"}


def _toolchain():
    pin = REPO / "rust-toolchain.toml"
    for line in pin.read_text().splitlines() if pin.is_file() else []:
        if line.strip().startswith("channel"):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def _crate(dirpath, snippet):
    d = Path(dirpath)
    (d / "src").mkdir(parents=True, exist_ok=True)
    (d / "Cargo.toml").write_text(
        '[package]\nname = "probe"\nversion = "0.0.0"\nedition = "2021"\n')
    ch = _toolchain()
    if ch:                       # carry the pin into the throwaway crate
        (d / "rust-toolchain.toml").write_text(f'[toolchain]\nchannel = "{ch}"\n')
    (d / "src" / "main.rs").write_text(snippet)
    return d


def rustc_diags(crate_dir):
    """Diagnostics per cargo check; the compile verdict is level=='error' absence."""
    r = subprocess.run(["cargo", "check", "--message-format=json", "--quiet"],
                       capture_output=True, text=True, cwd=crate_dir, env=_env())
    out = []
    for line in r.stdout.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("reason") != "compiler-message":
            continue
        msg = obj.get("message") or {}
        spans = msg.get("spans") or []
        if msg.get("level") not in ("error", "warning") or not spans:
            continue
        primary = next((s for s in spans if s.get("is_primary")), spans[0])
        out.append({"code": (msg.get("code") or {}).get("code"),
                    "level": msg["level"], "line": primary.get("line_start"),
                    "message": (msg.get("message") or "")[:200]})
    return out


def ra_diags(crate_dir, rel="src/main.rs", settle=3.0):
    s = ra.Session(crate_dir)
    try:
        path = Path(crate_dir) / rel
        uri = path.resolve().as_uri()
        s.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": "rust", "version": 0,
            "text": path.read_text()}})
        s.wait_quiescent(60)
        s.pump(settle)
        out = []
        for d in s.diagnostics.get(uri, []):
            code = d.get("code")
            if isinstance(code, dict):
                code = code.get("value")
            out.append({"code": str(code) if code is not None else None,
                        "level": _SEV.get(d.get("severity"), "info"),
                        "line": (d.get("range") or {}).get("start", {})
                        .get("line", -1) + 1,
                        "message": (d.get("message") or "")[:200]})
        return out
    finally:
        s.shutdown()


def divergence(rustc, rustan):
    """Compare by diagnostic code set (line-insensitive v1)."""
    rc = {d["code"] for d in rustc if d["code"]}
    rr = {d["code"] for d in rustan if d["code"]}
    return {"rustc_only": sorted(rc - rr), "ra_only": sorted(rr - rc),
            "agree": sorted(rc & rr)}


def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS probe_runs(
        id INTEGER PRIMARY KEY, ts REAL, toolchain TEXT, snippet_hash TEXT,
        compiles INTEGER, rustc_diags TEXT, ra_diags TEXT, divergence TEXT)""")
    return con


def run_probe(snippet, keep=False, record=True):
    tmp = tempfile.mkdtemp(prefix="oleum-probe-")
    crate = _crate(tmp, snippet)
    rustc = rustc_diags(crate)
    compiles = not any(d["level"] == "error" for d in rustc)
    rustan = ra_diags(crate)
    div = divergence(rustc, rustan)
    res = {"compiles": compiles, "rustc": rustc, "ra": rustan,
           "divergence": div, "crate": str(crate) if keep else None}
    if record:
        con = _db()
        con.execute("INSERT INTO probe_runs(ts,toolchain,snippet_hash,compiles,"
                    "rustc_diags,ra_diags,divergence) VALUES(?,?,?,?,?,?,?)",
                    (time.time(), _toolchain() or "",
                     hashlib.sha256(snippet.encode()).hexdigest(),
                     int(compiles), json.dumps(rustc), json.dumps(rustan),
                     json.dumps(div)))
        con.commit()
        con.close()
    return res


def main():
    import argparse
    ap = argparse.ArgumentParser(description="rustc adjudication probe")
    ap.add_argument("file", help="Rust snippet (a main.rs)")
    ap.add_argument("--keep", action="store_true", help="keep the probe crate")
    args = ap.parse_args()
    res = run_probe(Path(args.file).read_text(), keep=args.keep)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
