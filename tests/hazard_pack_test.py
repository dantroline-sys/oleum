"""Hazard pack, end to end: curated ids golden-checked against the live extractor,
pack built with the real producers, imported into a fresh vinur master, and
served back through ops_annotate.  Needs the pinned toolchain + the vinur repo."""
import json
import os
import sys
import tempfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VINUR = Path(os.environ.get("VINUR_REPO", "/home/user/vinur"))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "producers"))
sys.path.insert(0, str(VINUR))

import build_pack
from oleum import ra

FAILED = []
UNWRAP = "rust:op:core::option::Option::unwrap#inherent"


def check(label, cond):
    print(("  ok  " if cond else "FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def main():
    curated = tomllib.loads((REPO / "producers" / "op_hazards.toml").read_text())["op"]
    ids = [e["id"] for e in curated]
    check("curated ids are unique", len(ids) == len(set(ids)))
    check("curated ids all live in the op-id region",
          all(i.startswith("rust:op:") for i in ids))

    # ── golden gate: every curated id must be derivable by the live extractor ──
    s = ra.Session(REPO / "fixtures" / "join_ws")
    ops, unkeyed = s.extract_ops(REPO / "fixtures" / "join_ws" / "app" / "src"
                                 / "bin" / "hazards.rs")
    s.shutdown()
    missing = sorted(set(ids) - set(ops))
    check("every curated id is derived from hazards.rs by the extractor",
          missing == [])
    if missing:
        print("   missing:", ", ".join(missing))
    check("no unkeyed tokens in the hazards fixture", unkeyed == 0)

    # ── build the pack (bounded error-code range keeps the test quick) ─────────
    out = Path(tempfile.mkdtemp(prefix="oleum-pack-test-"))
    f, counts = build_pack.build(out, VINUR, max_ecode=520, log=lambda *a: None)
    check("pack built with all three source docs",
          counts["cards"] == len(ids) and counts["ops"] == len(ids)
          and counts["ecodes"] > 200 and counts["clippy"] > 100)
    check("E0502 landed as a diagnostic node", counts["ecodes"] >= 1)

    from knowledgehost import bundles, config as khconfig
    from knowledgehost.kb import KB
    man = bundles.inspect_bundle_file(f)
    check("manifest names the bundle",
          ((man or {}).get("manifest") or {}).get("name") == "rust-base")

    codes = json.loads((out / "codes.json").read_text())
    check("codes.json (DST-01 V3 oracle) pins the toolchain and carries E0502",
          codes["toolchain"].startswith("rustc") and "E0502" in codes["rustc"])
    check("codes.json lists ALL clippy lints, not just hazard groups",
          "clippy::unwrap_used" in codes["clippy"]
          and len(codes["clippy"]) > counts["clippy"])

    # ── import into a fresh master and serve through ops_annotate ─────────────
    tmp2 = Path(tempfile.mkdtemp(prefix="oleum-pack-master-"))
    cfg = dict(khconfig.DEFAULTS)
    cfg["kb_path"] = str(tmp2 / "kb.db")
    cfg["bundle_dir"] = str(tmp2 / "bundles")
    cfg["ops_regions"] = ["rust=rust-coding"]
    KB(cfg).close()
    bundles.import_bundle(cfg, f, name="rust-base", trust="keep")
    kb = KB(cfg)
    res = kb.annotate_ops([UNWRAP, "rust:op:acme::nope#free"])
    a = res["annotations"][UNWRAP]
    check("ops_annotate serves the curated hazard after import",
          a.get("annotated") is True
          and a["caveats"][0]["severity"] == "warn"
          and a["caveats"][0]["title"] == "panics on None")
    check("unknown op stays bare through the same call",
          res["annotations"]["rust:op:acme::nope#free"] == {"annotated": False})
    check("graph_version is a digest", res["graph_version"].startswith("sha256:"))

    row = kb.db.execute("SELECT criteria FROM procedure_cards WHERE node_id=?",
                        (UNWRAP,)).fetchone()
    crit = json.loads(row["criteria"])
    check("card criteria carry why/instead/diag for the guidance layer",
          "instead" in crit and crit.get("diag") == "clippy::unwrap_used")

    kb.facetize()
    check("facetize derives the region domain facet on imported ops",
          kb.get_facets("node", UNWRAP).get("domain") == ["rust-coding"])
    kb.close()

    # ── determinism: identical sources -> identical pack content ──────────────
    outs = []
    for _ in range(2):
        d = Path(tempfile.mkdtemp(prefix="oleum-pack-det-"))
        pf, _c = build_pack.build(d, VINUR, max_ecode=0, with_ecodes=False,
                                  with_clippy=False, log=lambda *a: None)
        m2 = Path(tempfile.mkdtemp(prefix="oleum-pack-det-m-"))
        c2 = dict(khconfig.DEFAULTS)
        c2["kb_path"] = str(m2 / "kb.db")
        c2["bundle_dir"] = str(m2 / "bundles")
        c2["ops_regions"] = ["rust=rust-coding"]
        KB(c2).close()
        bundles.import_bundle(c2, pf, name="rust-base", trust="keep")
        k2 = KB(c2)
        outs.append(k2.region_version())
        k2.close()
    check("rebuild from identical sources -> identical region_version",
          outs[0] == outs[1])

    if FAILED:
        print(f"\n{len(FAILED)} FAILED")
        raise SystemExit(1)
    print("\nALL OK")


if __name__ == "__main__":
    main()
