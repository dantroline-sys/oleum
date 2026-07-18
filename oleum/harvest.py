"""Corpus harvester — OLEUM-DST-01 §4 S3, the AMIGA-RUST-03 inversion.

For every function/method use in a file, capture the decision the author made
and the alternatives rust-analyzer would have served at that exact site:

- the op id of the author's choice, via the same `key_at` path the annotation
  lane uses (identical ids by construction, per DST-01 S3);
- the candidate set from textDocument/completion at the identifier's start
  (empty typed-prefix, so the full legal set for the receiver/scope), filtered
  to callables, sortText-ranked, capped — the chosen candidate is always kept;
- segment grouping at item granularity via textDocument/documentSymbol
  (DST-01 §4 S1; the S2 split / S4 caps are the distiller orchestrator's job,
  so segments carry their line ranges for it).

Candidate op ids are "unkeyed" in v1 except the author's choice: keying a
candidate requires shadow-accepting it.  DST-01 tolerates this ("where
derivable"); V4 candidate closure operates on labels.

CLI: python3 -m oleum.harvest <file.rs> [--workspace DIR] [--cap 30]
"""
import json
import re
from pathlib import Path

from . import opkey

_CALLABLE_KINDS = {2: "method", 3: "function"}     # LSP CompletionItemKind
_AS_TRAIT = re.compile(r"\(as\s+([\w:]+)\)")


def _symbol_segments(symbols, path=()):
    """Flatten the DocumentSymbol tree -> [(item_path, kind, start_line, end_line)]."""
    out = []
    for s in symbols or []:
        p = path + (s.get("name", "?"),)
        r = s.get("range") or {}
        out.append(("::".join(p), s.get("kind"),
                    (r.get("start") or {}).get("line", 0),
                    (r.get("end") or {}).get("line", 0)))
        out.extend(_symbol_segments(s.get("children"), p))
    return out


def _enclosing(segs, line):
    """Innermost item whose range contains the line."""
    best = None
    for seg in segs:
        _p, _k, a, b = seg
        if a <= line <= b and (best is None or (b - a) < (best[3] - best[2])):
            best = seg
    return best


def _candidates(session, uri, line, char, token, cap):
    comp = session.request("textDocument/completion", {
        "textDocument": {"uri": uri},
        "position": {"line": line, "character": char}})
    items = comp.get("items") if isinstance(comp, dict) else comp
    cands, chosen = [], None
    for it in items or []:
        kind = _CALLABLE_KINDS.get(it.get("kind"))
        if not kind:
            continue
        lab = it.get("label", "")
        det = ((it.get("labelDetails") or {}).get("detail")) or ""
        m = _AS_TRAIT.search(det)
        cands.append({"label": lab, "kind": kind,
                      "trait": m.group(1) if m else None,
                      "sort": it.get("sortText") or lab})
        if chosen is None and (lab == token or lab.startswith(token + "(")):
            chosen = lab
    total = len(cands)
    cands.sort(key=lambda c: c["sort"])
    kept = [c for c in cands if c["label"] == chosen][:1] \
        + [c for c in cands if c["label"] != chosen][:max(0, cap - 1)]
    for c in kept:
        c.pop("sort", None)
        c["op_id"] = "unkeyed"                 # v1: only the author's choice is keyed
    return kept, chosen, total


def harvest_file(session, path, code=None, cap=30):
    """Segment-grouped decision-site records for one file (DST-01 S3 shape)."""
    path = Path(path).resolve()
    uri, text = session.open_overlay(path, code)
    lines = text.splitlines()
    try:
        symbols = session.request("textDocument/documentSymbol",
                                  {"textDocument": {"uri": uri}})
        segs = _symbol_segments(symbols if isinstance(symbols, list) else [])
        sites = []
        for line, char, length in session._use_tokens(uri):
            token = lines[line][char:char + length] if line < len(lines) else ""
            op_id, hover_md = session.key_at(uri, line, char)
            blocks = opkey._FENCE.findall(hover_md)
            kept, chosen, total = _candidates(session, uri, line, char, token, cap)
            enc = _enclosing(segs, line)
            sites.append({
                "site": f"{path.name}:{line + 1}:{char}",
                "line": line + 1, "char": char, "text": token,
                "op_id": op_id or "unkeyed",
                "hint": blocks[0].strip().splitlines()[-1].strip() if blocks else None,
                "chosen_label": chosen, "chosen_in_set": chosen is not None,
                "total_candidates": total, "candidates": kept,
                "item": enc[0] if enc else None,
            })
        grouped = {}
        for s in sites:
            grouped.setdefault(s["item"], []).append(s)
        segments = []
        for item, ss in grouped.items():
            meta = next((g for g in segs if g[0] == item), None)
            segments.append({"item": item,
                             "range": [meta[2] + 1, meta[3] + 1] if meta else None,
                             "sites": ss})
        segments.sort(key=lambda g: (g["range"] or [1 << 30])[0])
        return {"file": str(path), "segments": segments, "sites": len(sites),
                "unkeyed": sum(1 for s in sites if s["op_id"] == "unkeyed")}
    finally:
        session.close_overlay(uri)


def main():
    import argparse
    from . import ra
    ap = argparse.ArgumentParser(description="harvest decision sites from a Rust file")
    ap.add_argument("file")
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--cap", type=int, default=30)
    args = ap.parse_args()
    root = args.workspace
    if root is None:
        for parent in Path(args.file).resolve().parents:
            if (parent / "Cargo.toml").is_file():
                root = parent                   # outermost wins
    if root is None:
        raise SystemExit(f"no Cargo.toml above {args.file}")
    s = ra.Session(root)
    try:
        print(json.dumps(harvest_file(s, args.file, cap=args.cap),
                         ensure_ascii=False, indent=1))
    finally:
        s.shutdown()


if __name__ == "__main__":
    main()
