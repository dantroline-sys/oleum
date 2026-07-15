"""Produce the rust-base knowledge pack (rust-base.kdb) for the vinur host.

Sources, all offline and pinned to the toolchain:
- rustc error index      `rustc --explain E####` probed over the code range
                          -> diagnostic nodes  rust:diag:E####
- Clippy lints           `clippy-driver -W help`, correctness/suspicious/perf
                          groups -> diagnostic nodes  rust:diag:clippy::<lint>
- curated op hazards     producers/op_hazards.toml -> op nodes + hazard cards
                          (ids golden-checked against the extractor in tests)

vinur is used as a library (--vinur / $VINUR_REPO): the pack is built in a
scratch kb and exported with vinur's own bundle closure, so it imports on any
host via `import-bundle` / the Bundles tab.  Timestamps are fixed at 0.0 so a
rebuild from identical sources is byte-stable (content-hash friendly).

Usage: python3 producers/build_pack.py [--out dist] [--max-ecode 1000]
       [--no-clippy] [--no-ecodes] [--vinur /path/to/vinur]
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CURATED = HERE / "op_hazards.toml"
BUNDLE = "rust-base"
DOC_ECODES = "rust-error-index"
DOC_CLIPPY = "rust-clippy-lints"
DOC_CURATED = "rust-oleum-curated"
CLIPPY_GROUPS = {"correctness": "error", "suspicious": "warn", "perf": "warn"}
_ENV = None


def _env():
    global _ENV
    if _ENV is None:
        _ENV = dict(os.environ)
        _ENV["PATH"] = str(Path.home() / ".cargo" / "bin") + os.pathsep \
            + _ENV.get("PATH", "")
    return _ENV


def rustc_version():
    return subprocess.run(["rustc", "--version"], capture_output=True, text=True,
                          env=_env()).stdout.strip()


def ecodes(max_code=1000):
    """{code: first-paragraph summary} for every explainable rustc error code."""
    out = {}
    for i in range(1, max_code):
        code = f"E{i:04d}"
        r = subprocess.run(["rustc", "--explain", code], capture_output=True,
                           text=True, env=_env())
        if r.returncode != 0:
            continue
        para = r.stdout.strip().split("\n\n", 1)[0].replace("\n", " ")
        out[code] = para[:400]
    return out


def clippy_lints():
    """{lint_name(underscored): (group, severity, meaning)} for the hazard groups."""
    r = subprocess.run(["clippy-driver", "-W", "help"], capture_output=True,
                       text=True, env=_env())
    if r.returncode != 0:
        raise RuntimeError("clippy-driver not available (rustup component add clippy)")
    meaning, members = {}, {}
    for line in r.stdout.splitlines():
        m = re.match(r"\s+clippy::([\w-]+)\s{2,}(allow|warn|deny|forbid)\s{2,}(.+)$",
                     line)
        if m:
            meaning[m.group(1).replace("-", "_")] = m.group(3).strip()
            continue
        g = re.match(r"\s+clippy::(\w+)\s{2,}(clippy::.+)$", line)
        if g and g.group(1) in CLIPPY_GROUPS:
            for lint in g.group(2).split(","):
                lint = lint.strip().removeprefix("clippy::").replace("-", "_")
                if lint:
                    members[lint] = g.group(1)
    return {lint: (grp, CLIPPY_GROUPS[grp], meaning.get(lint, ""))
            for lint, grp in members.items()}


def curated():
    return tomllib.loads(CURATED.read_text())["op"]


def _card_hash(title, criteria):
    return hashlib.sha256((title + "\x1f" + criteria).encode()).hexdigest()


def build(out_dir, vinur_repo, max_ecode=1000, with_ecodes=True, with_clippy=True,
          log=print):
    sys.path.insert(0, str(vinur_repo))
    from knowledgehost import bundles, config as khconfig
    from knowledgehost.kb import KB

    ver = rustc_version()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="oleum-pack-")
    cfg = dict(khconfig.DEFAULTS)
    cfg["kb_path"] = str(Path(scratch) / "kb.db")
    cfg["bundle_dir"] = str(out_dir)
    kb = KB(cfg)

    docs = [(DOC_ECODES, f"rustc error index ({ver})"),
            (DOC_CLIPPY, f"Clippy lints, hazard groups ({ver})"),
            (DOC_CURATED, "oleum curated op hazards")]
    for doc_id, title in docs:
        kb.db.execute(
            "INSERT INTO source_registry(doc_id,title,source_type,trust_weight,"
            "regime,status,bundle) VALUES(?,?,?,?,?,?,?)",
            (doc_id, title, "reference", 1.0, "empirical", "active", BUNDLE))

    def node(nid, label, kind, summary, doc_id):
        kb.db.execute(
            "INSERT OR IGNORE INTO nodes(id,label,kind,summary,aliases,support,"
            "status) VALUES(?,?,?,?,?,?,?)",
            (nid, label, kind, summary, "[]",
             json.dumps([{"doc_id": doc_id}]), "active"))

    counts = {"ecodes": 0, "clippy": 0, "ops": 0, "cards": 0}
    if with_ecodes:
        for code, summary in ecodes(max_ecode).items():
            node(f"rust:diag:{code}", code, "diagnostic", summary, DOC_ECODES)
            counts["ecodes"] += 1
    if with_clippy:
        for lint, (grp, _sev, meaning) in sorted(clippy_lints().items()):
            node(f"rust:diag:clippy::{lint}", f"clippy::{lint}", "diagnostic",
                 f"[{grp}] {meaning}", DOC_CLIPPY)
            counts["clippy"] += 1

    for e in curated():
        node(e["id"], e["label"], "fn", e["why"], DOC_CURATED)
        counts["ops"] += 1
        criteria = json.dumps({k: e.get(k) for k in
                               ("severity", "why", "instead", "diag")
                               if e.get(k) is not None}, sort_keys=True)
        kb.db.execute(
            "INSERT INTO procedure_cards(id,node_id,title,card_type,criteria,"
            "support,status,card_hash,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f"rust:hazard:card:{e['label']}", e["id"], e["title"], "hazard",
             criteria, json.dumps([{"doc_id": DOC_CURATED}]), "active",
             _card_hash(e["title"], criteria), 0.0, 0.0))
        counts["cards"] += 1

    kb.db.commit()
    kb.close()
    res = bundles.split(cfg, str(out_dir), only={BUNDLE}, force=True,
                        log_fn=lambda m: None)
    f = res[BUNDLE]["file"]
    log(f"pack: {f}")
    log(f"  {counts}  ({ver})")
    return f, counts


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(HERE.parent / "dist"))
    ap.add_argument("--vinur", default=os.environ.get("VINUR_REPO",
                                                      "/home/user/vinur"))
    ap.add_argument("--max-ecode", type=int, default=1000)
    ap.add_argument("--no-ecodes", action="store_true")
    ap.add_argument("--no-clippy", action="store_true")
    args = ap.parse_args()
    build(args.out, args.vinur, max_ecode=args.max_ecode,
          with_ecodes=not args.no_ecodes, with_clippy=not args.no_clippy)


if __name__ == "__main__":
    main()
