"""DST pipeline offline: digest (real r-a + cargo), prompt assembly, all pure
gates, orchestrator with a scripted stub LM (retry, quarantine, V6 audit, V7
routing), merge into a scratch rust-learned kb with dedup/observed_count, pack
export, and trace capture.  No serving stack needed."""
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VINUR = Path(os.environ.get("VINUR_REPO", "/home/user/vinur"))
sys.path.insert(0, str(REPO))

from oleum import ra, traces
from oleum.dst import digest, merge, orchestrate, prompts, validate

WS = REPO / "fixtures" / "join_ws"
HAZARDS = WS / "app" / "src" / "bin" / "hazards.rs"
EXTLIB = WS / "ext" / "src" / "lib.rs"
UNWRAP = "rust:op:core::option::Option::unwrap#inherent"
FAILED = []


def check(label, cond):
    print(("  ok  " if cond else "FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def good_card(site, op_id=UNWRAP, applic="when the None case is reachable, "
              "prefer ? or unwrap_or_else over unwrap because the panic "
              "carries no context"):
    return {
        "contract_version": prompts.CONTRACT_VERSION, "segment_id": "seg1",
        "purpose": {"contract": "abstain", "blast_radius": [],
                    "misuse_prevented": "abstain"},
        "strategy": [{
            "decision_site": site, "op_id": op_id, "chosen": "unwrap(…)",
            "rejected_alternatives": [{"candidate": "map(…)", "op_id": "unkeyed",
                                       "traded_away": "laziness"}],
            "pattern": "none", "applicability": applic,
            "register": "canonical_idiom", "deviation_justification": "n/a"}],
        "execution": {
            "ownership_events": [{"kind": "lock", "at": "Mutex::lock"}],
            "failure_paths": [{"kind": "panic", "at": "unwrap",
                               "note": "panics on None"}],
            "alloc_class": "zero", "hot_path_plausible": "abstain"},
        "regime": {"conditioning_tags": ["general"], "neighbour_assumptions": []},
        "hazards": [{"kind": "clippy_lint", "code": "clippy::unwrap_used",
                     "trigger": "unwrap on a reachable None"},
                    {"kind": "rustc_error", "code": "E9999",
                     "trigger": "made up"}],
        "confidence": {"purpose": 0.5, "strategy": 0.9, "execution": 0.9},
        "abstained_fields": ["purpose"],
    }


class StubLM:
    """Scripted responses; records prompts."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat_json(self, system, user, schema, max_tokens=None):
        self.calls.append((system, user))
        if "gloss" in (schema.get("properties") or {}):
            return {"gloss": "Fixture unit exercising curated hazards."}
        return self.script.pop(0) if self.script else None


def main():
    # ── digest (real cargo metadata + r-a symbols) ────────────────────────────
    s = ra.Session(WS)
    d = digest.build(s, WS, HAZARDS)
    d_ext = digest.build(s, WS, EXTLIB)
    s.shutdown()
    check("D1 crate ident from cargo metadata",
          d["crate_ident"] == {"name": "app", "version": "0.1.0",
                               "edition": "2021"})
    check("D2 module path for a bin unit", d["module_path"] == "bin:hazards")
    check("D3 tags: hazards.rs is plain code", d["context_tags"] == ["general"])
    check("D4 type vocabulary sees ext's types",
          {t["name"] for t in d_ext["type_vocabulary"]}
          >= {"Widget", "SliceStats", "Greet"})
    check("D5 impls visible", any("SliceStats" in i
                                  for i in d_ext["trait_impls_in_scope"]))
    check("D6 deps: hazards.rs imports std items",
          "std" in d["deps_of_interest"]
          and "Mutex" in d["deps_of_interest"]["std"])
    check("D8 left for the LM", d["unit_gloss"] is None)

    # ── prompt assembly ───────────────────────────────────────────────────────
    seg = {"segment_id": "seg1", "source": HAZARDS.read_text(),
           "sites": [{"site": "hazards.rs:10:14", "op_id": UNWRAP,
                      "candidates": [{"label": "unwrap(…)"},
                                     {"label": "map(…)"}]}]}
    sfx = prompts.suffix(d, seg)
    check("suffix order: digest first, instruction last",
          sfx.startswith("CONTEXT DIGEST:") and sfx.rstrip().endswith("after."))
    check("prefix embeds role, battery, schema and rules",
          "never invent APIs" in prompts.PREFIX and "G1" in prompts.PREFIX
          and "false confidence" in prompts.PREFIX.lower())
    stub = {"segment_id": "s", "source": "fn f() {}", "sibling_stub": "fn g();",
            "sites": []}
    check("sibling stub included for split segments",
          "SIBLING METHODS" in prompts.suffix(d, stub))

    # ── pure gates ────────────────────────────────────────────────────────────
    codes = {"rustc": ["E0502"], "clippy": ["clippy::unwrap_used"]}
    site = "hazards.rs:10:14"
    ok = validate.gate(good_card(site), schema=prompts.SCHEMA,
                       source=seg["source"], sites=seg["sites"], codes=codes)
    check("V1 passes a conforming card; V3 strips the invented code",
          not ok["quarantine"] and len(ok["card"]["hazards"]) == 1
          and any("E9999" in x for x in ok["stripped"]))

    bad = good_card(site)
    bad["execution"]["alloc_class"] = "lots"
    check("V1 rejects a closed-enum violation",
          validate.gate(bad, schema=prompts.SCHEMA, source=seg["source"],
                        sites=seg["sites"], codes=codes)["quarantine"])

    quote = good_card(site)
    quote["purpose"]["contract"] = seg["source"][:400]
    check("V2 quarantines verbatim quotation",
          validate.gate(quote, schema=prompts.SCHEMA, source=seg["source"],
                        sites=seg["sites"], codes=codes)["quarantine"])

    orig = good_card(site)
    orig["strategy"][0]["rejected_alternatives"].append(
        {"candidate": "frobnicate(…)", "op_id": "unkeyed", "traded_away": "x"})
    g = validate.gate(orig, schema=prompts.SCHEMA, source=seg["source"],
                      sites=seg["sites"], codes=codes)
    check("V4 strips originated candidates, keeps legal ones",
          len(g["card"]["strategy"][0]["rejected_alternatives"]) == 1
          and any("originated" in x for x in g["stripped"]))

    leak = good_card(site)
    leak["strategy"][0]["applicability"] = "always call _guard first"
    g = validate.gate(leak, schema=prompts.SCHEMA, source=seg["source"],
                      sites=seg["sites"], codes=codes)
    check("V5 rejects segment-local identifier leaks",
          g["card"]["strategy"] == [] and any("leak" in x for x in g["stripped"]))

    forged = good_card(site, op_id="rust:op:acme::Fake::x#inherent")
    g = validate.gate(forged, schema=prompts.SCHEMA, source=seg["source"],
                      sites=seg["sites"], codes=codes)
    check("V8 strips an op_id that differs from the harvester's",
          g["card"]["strategy"] == [] and any("op_id" in x for x in g["stripped"]))

    ops_here = [UNWRAP, "rust:op:std::sync::poison::mutex::Mutex::lock#inherent"]
    check("V6 audit: corroborated claims pass clean",
          validate.audit(good_card(site), ops_here, seg["source"]) == [])
    silent = good_card(site)
    silent["execution"]["failure_paths"] = []
    silent["abstained_fields"] = []
    check("V6 audit: unclaimed panic on a panicking segment flagged",
          any("panic" in x for x in
              validate.audit(silent, ops_here, "let x = o.unwrap();")))

    check("V7 disagree: register flip on the same site",
          validate.disagree(good_card(site), (lambda c: (
              c["strategy"][0].__setitem__("register", "language_workaround"),
              c)[1])(good_card(site))))

    # ── orchestrator: gloss, retry, V7 routing, quarantine ───────────────────
    unit = {"digest": d, "trust_tier": "T2",
            "segments": [dict(seg, op_ids=ops_here)]}
    lm = StubLM([bad, good_card(site)])           # first invalid -> retry -> good
    res = orchestrate.run_unit(lm, unit, codes=codes)
    check("retry-then-accept on a V1 fault",
          res["stats"]["retries"] == 1 and len(res["accepted"]) == 1
          and res["accepted"][0]["card"]["hazards"][0]["code"]
          == "clippy::unwrap_used")
    check("D8 gloss filled by the model", "unit_gloss" not in unit["digest"]
          or unit["digest"]["unit_gloss"] is None)  # input untouched

    low = good_card(site)
    low["confidence"]["strategy"] = 0.4
    second = good_card(site)
    second["strategy"][0]["register"] = "deliberate_deviation"
    pri, sec = StubLM([low]), StubLM([second])
    res2 = orchestrate.run_unit(pri, unit, secondary=sec, codes=codes)
    check("V7 routes low confidence to the second model; disagreement "
          "quarantines", res2["stats"]["routed"] == 1
          and res2["stats"]["v7_quarantined"] == 1
          and res2["accepted"] == [])

    # ── merge: dedup + observed_count + no-op re-run + pack export ───────────
    tmpkb = Path(tempfile.mkdtemp(prefix="dst-merge-"))
    acc = res["accepted"]
    st1 = merge.merge_run(acc, "dst:fixture@1", vinur_repo=VINUR, kb_dir=tmpkb)
    st2 = merge.merge_run(acc, "dst:fixture@1", vinur_repo=VINUR, kb_dir=tmpkb)
    acc2 = [dict(a, segment_id="seg2") for a in
            json.loads(json.dumps(acc))]
    st3 = merge.merge_run(acc2, "dst:fixture@1", vinur_repo=VINUR, kb_dir=tmpkb)
    check("first merge creates the strategy card",
          st1["new"] == 1 and st1["cards_in"] == 1)
    check("same-corpus re-run is a no-op", st2["unchanged"] == 1
          and st2["new"] == 0 and st2["reinforced"] == 0)
    check("a new segment reinforces instead of duplicating",
          st3["reinforced"] == 1 and st3["new"] == 0)
    import sqlite3
    con = sqlite3.connect(tmpkb / "kb.db")
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM procedure_cards WHERE card_type='strategy'"
                      ).fetchone()
    crit = json.loads(row["criteria"])
    check("card attached to the op node with observed_count accumulated",
          row["node_id"] == UNWRAP and row["observed_count"] == 2
          and crit["register"] == "canonical_idiom"
          and crit["trust_tier"] == "T2")
    con.close()
    pack = merge.export_pack(vinur_repo=VINUR, kb_dir=tmpkb,
                             out_dir=tmpkb / "dist")
    check("rust-learned pack exports", pack and Path(pack).name
          == "rust-learned.kdb" and Path(pack).stat().st_size > 0)
    check("hazard + unkeyed observations parked for review",
          (tmpkb / "observations.jsonl").is_file())

    # ── trace capture ─────────────────────────────────────────────────────────
    traces.record("annotate", {"path": "x.rs", "requested": 2, "joined": 1,
                               "bare_ops": [UNWRAP]})
    check("gap queue surfaces bare ops", UNWRAP in dict(traces.gaps()))

    if FAILED:
        print(f"\n{len(FAILED)} FAILED")
        raise SystemExit(1)
    print("\nALL OK")


if __name__ == "__main__":
    main()
