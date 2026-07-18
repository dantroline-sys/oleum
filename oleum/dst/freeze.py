"""Frozen-set bootstrap (OLEUM-DST-01 §8 regression suite).

Harvests files from a workspace into annotation templates: one JSONL row per
segment carrying everything the distiller would see (digest, source, sites)
plus an empty `expected` block for hand-annotation.  Annotated rows become
the frozen set the gates must hold 100% on before any prompt/model change
ships.

CLI: python3 -m oleum.dst.freeze <workspace> <file.rs...> [--out tests/frozen/pending.jsonl]
"""
import argparse
import json
from pathlib import Path

from .. import ra
from ..harvest import harvest_file
from . import digest as digest_mod

REPO = Path(__file__).resolve().parent.parent.parent


def freeze_files(ws, files, cap=20):
    session = ra.Session(ws)
    rows = []
    try:
        for f in files:
            d = digest_mod.build(session, ws, f)
            h = harvest_file(session, f, cap=cap)
            src_lines = Path(f).read_text().splitlines()
            for seg in h["segments"]:
                a, b = (seg["range"] or [1, len(src_lines)])
                rows.append({
                    "segment_id": f"{Path(f).name}:{seg['item']}",
                    "digest": d,
                    "source": "\n".join(src_lines[a - 1:b]),
                    "sites": seg["sites"],
                    "expected": {"register_by_site": {}, "pattern": "",
                                 "notes": "", "annotated_by": ""},
                })
    finally:
        session.shutdown()
    return rows


def main():
    ap = argparse.ArgumentParser(description="emit frozen-set annotation templates")
    ap.add_argument("workspace")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default=str(REPO / "tests" / "frozen" / "pending.jsonl"))
    ap.add_argument("--cap", type=int, default=20)
    args = ap.parse_args()
    rows = freeze_files(args.workspace, args.files, cap=args.cap)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} segment template(s) appended to {out}")


if __name__ == "__main__":
    main()
