"""SPIKE-0 (AMIGA-RUST-02 §SPIKE-0): which join key maps a rust-analyzer candidate
to a stable op-node id?

Mechanisms measured on the golden fixture workspace, per case:
  A  completion item as served (label / labelDetails / detail)
  B  completionItem/resolve enrichment (what a runtime gets WITHOUT touching the buffer)
  C  shadow-accept + hover on the accepted identifier (canonical container path)
  D  shadow-accept + textDocument/moniker (only if the server advertises it)
  E  shadow-accept + definition (file-path key, needs machine normalization)
  F  shadow-accept + experimental/externalDocs (rustdoc URL as a global key)

Runs two fresh server instances end-to-end and diffs the extracted key tables —
a join key that isn't byte-stable across sessions is no key at all.

Usage: python3 run_spike.py            (writes results/spike0_report.json)
"""
import json
import re
import subprocess
import time
from pathlib import Path

import lsp_client

HERE = Path(__file__).resolve().parent
WS = HERE.parent.parent / "fixtures" / "join_ws"
INCOMPLETE = WS / "app" / "src" / "bin" / "incomplete.rs"
COMPLETED = WS / "app" / "src" / "bin" / "completed.rs"
RESULTS = HERE / "results"

CARET = re.compile(r"/\*caret:([a-z_]+)\*/")
MARKER = re.compile(r"//\s*case:([a-z_]+)\s+target:(\w+)")
FENCE = re.compile(r"```rust\n(.*?)\n```", re.S)

# case -> completion prefix's expected winner (label match, "(" tolerated)
EXPECT = {"assoc_fn": "new", "inherent_method": "push", "deref_method": "trim",
          "trait_method_std": "map", "ext_trait_method": "mean",
          "reexport_method": "label", "macro_generated": "generated",
          "user_trait_method": "greet", "free_fn_generic": "swap"}


def positions_incomplete(text):
    """case -> (line, char) of the caret (start of the /*caret*/ marker)."""
    out = {}
    for ln, line in enumerate(text.splitlines()):
        for m in CARET.finditer(line):
            out[m.group(1)] = (ln, m.start())
    return out


def positions_completed(text):
    """case -> (line, char) pointing into the accepted identifier."""
    out = {}
    for ln, line in enumerate(text.splitlines()):
        m = MARKER.search(line)
        if m:
            code = line.split("//", 1)[0]
            out[m.group(1)] = (ln, code.index(m.group(2) + "(") + 1)
    return out


def pick(items, want):
    for it in items:
        lab = it.get("label", "")
        if lab == want or lab.startswith(want + "("):
            return it
    for it in items:  # fall back to filterText (labels can carry decorations)
        if it.get("filterText") == want:
            return it
    return None


def trim_item(it):
    if not isinstance(it, dict):
        return it
    out = {k: it.get(k) for k in ("label", "labelDetails", "detail", "kind", "data")
           if it.get(k) is not None}
    doc = it.get("documentation")
    if isinstance(doc, dict):
        out["documentation_head"] = doc.get("value", "")[:200]
    return out


def hover_blocks(hov):
    if not isinstance(hov, dict):
        return []
    val = (hov.get("contents") or {}).get("value", "")
    return FENCE.findall(val)


def derive_key(blocks):
    """Mechanism C key: container path from hover block 1, fn name from block 2."""
    if len(blocks) < 2:
        return None
    container = blocks[0].strip().splitlines()[-1].strip()
    m = re.search(r"\bfn\s+(\w+)", blocks[1])
    return f"{container}::{m.group(1)}" if m else None


def synth_op_id(rec):
    """The hybrid recipe under test: externalDocs URL (canonical defining path,
    version-stripped) with hover as the fallback for primitives; disambiguator from
    the completion item's `(as Trait)` labelDetails."""
    det = ((rec.get("item") or {}).get("labelDetails") or {}).get("detail") or ""
    m = re.search(r"\(as\s+([\w:]+)\)", det)
    kind = "as:" + m.group(1) if m else "inherent"
    url = rec.get("external_docs")
    if isinstance(url, str):
        host, _, path = re.sub(r"^https?://", "", url).partition("/")
        if host == "docs.rs":
            path = re.sub(r"^([\w-]+)/[^/]+/", "", path)     # <crate>/<version>/
        else:
            path = re.sub(r"^(stable|nightly|beta)/", "", path)
        path = path.replace(".html", "")
        container, _, anchor = path.partition("#")
        if not anchor and "/fn." in container:
            kind = "free"
        container = re.sub(r"(struct|enum|trait|union|primitive|fn)\.", "",
                           container).replace("/", "::")
        base = container + ("::" + anchor.split(".")[-1] if anchor else "")
    else:
        base = derive_key(rec.get("hover_blocks") or [])
        if base is None:
            return None
    return f"rust:op:{base}#{kind}"


def one_pass(label):
    t0 = time.monotonic()
    c, init = lsp_client.start(str(WS))
    moniker_cap = (init.get("capabilities") or {}).get("monikerProvider")
    inc_text = c.open_doc(INCOMPLETE)
    com_text = c.open_doc(COMPLETED)
    quiescent = c.wait_quiescent()
    idx_s = round(time.monotonic() - t0, 1)
    print(f"[{label}] quiescent={quiescent} after {idx_s}s  monikerProvider={moniker_cap!r}")

    inc_pos = positions_incomplete(inc_text)
    com_pos = positions_completed(com_text)
    inc_uri, com_uri = Path(INCOMPLETE).as_uri(), Path(COMPLETED).as_uri()
    cases, keys = {}, {}

    for case, want in EXPECT.items():
        rec = {}
        # A/B: completion + resolve at the truncated site
        ln, ch = inc_pos[case]
        res = c.request("textDocument/completion", {
            "textDocument": {"uri": inc_uri}, "position": {"line": ln, "character": ch}})
        items = res.get("items", res) if isinstance(res, (dict, list)) else []
        if isinstance(items, dict):
            items = items.get("items", [])
        cand = pick(items or [], want)
        rec["candidates"] = len(items or [])
        rec["item"] = trim_item(cand) if cand else None
        if cand:
            rec["resolved"] = trim_item(c.request("completionItem/resolve", cand))
        # C/D/E: hover / moniker / definition at the accepted identifier
        ln, ch = com_pos[case]
        pos = {"textDocument": {"uri": com_uri}, "position": {"line": ln, "character": ch}}
        blocks = hover_blocks(c.request("textDocument/hover", dict(pos)))
        rec["hover_blocks"] = blocks[:2]
        rec["moniker"] = c.request("textDocument/moniker", dict(pos))
        rec["external_docs"] = c.request("experimental/externalDocs", dict(pos))
        defs = c.request("textDocument/definition", dict(pos))
        if isinstance(defs, list) and defs:
            uri = defs[0].get("targetUri") or defs[0].get("uri") or ""
            rec["definition_tail"] = "/".join(uri.split("/")[-4:])
        keys[case] = {"op_id": synth_op_id(rec), "hover_key": derive_key(blocks),
                      "doc_url": rec["external_docs"]}
        cases[case] = rec
        print(f"[{label}] {case}: candidates={rec['candidates']} "
              f"op_id={keys[case]['op_id']}")

    c.shutdown()
    return {"moniker_capability": moniker_cap, "quiescent": quiescent,
            "index_seconds": idx_s, "cases": cases, "keys": keys}


def main():
    RESULTS.mkdir(exist_ok=True)
    ra = subprocess.run([str(Path.home() / ".cargo" / "bin" / "rust-analyzer"),
                         "--version"], capture_output=True, text=True)
    p1 = one_pass("pass1")
    p2 = one_pass("pass2")
    stable = p1["keys"] == p2["keys"]
    report = {"rust_analyzer": ra.stdout.strip(), "workspace": str(WS),
              "pass1": p1, "keys_stable_across_sessions": stable,
              "keys_pass2": p2["keys"]}
    out = RESULTS / "spike0_report.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nkeys stable across sessions: {stable}")
    print(f"report: {out}")


if __name__ == "__main__":
    main()
