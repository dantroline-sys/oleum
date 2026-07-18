"""Harvester (DST-01 S3): decision sites with candidate sets, keyed identically
to the annotation lane, grouped into item segments.  Needs the pinned toolchain."""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from oleum import ra
from oleum.harvest import harvest_file

WS = REPO / "fixtures" / "join_ws"
HAZARDS = WS / "app" / "src" / "bin" / "hazards.rs"
FAILED = []


def check(label, cond):
    print(("  ok  " if cond else "FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def main():
    s = ra.Session(WS)
    h = harvest_file(s, HAZARDS, cap=15)
    ops, _unkeyed = s.extract_ops(HAZARDS)
    s.shutdown()

    sites = [x for seg in h["segments"] for x in seg["sites"]]
    check("a dozen-plus decision sites harvested", h["sites"] >= 12)
    check("every site keyed (no unkeyed in the hazards fixture)", h["unkeyed"] == 0)
    check("harvester and annotation lane derive the SAME id set",
          {x["op_id"] for x in sites} == set(ops))
    check("site ids are file:line:char", all(
        x["site"].startswith("hazards.rs:") for x in sites))

    unwrap = next(x for x in sites
                  if x["op_id"] == "rust:op:core::option::Option::unwrap#inherent")
    check("author's choice found in the candidate set",
          unwrap["chosen_in_set"] and unwrap["chosen_label"] is not None)
    check("candidate set is the full receiver surface, then capped",
          unwrap["total_candidates"] > 20 and len(unwrap["candidates"]) <= 15)
    check("chosen candidate always survives the cap",
          unwrap["candidates"] and unwrap["candidates"][0]["label"]
          == unwrap["chosen_label"])
    check("candidates carry callable kinds only",
          all(c["kind"] in ("method", "function")
              for x in sites for c in x["candidates"]))
    check("trait attribution captured on some candidates ((as Trait))",
          any(c["trait"] for x in sites for c in x["candidates"]))
    check("v1 candidates are unkeyed placeholders",
          all(c["op_id"] == "unkeyed" for x in sites for c in x["candidates"]))

    check("all hazards.rs sites group under fn main",
          [seg["item"] for seg in h["segments"]] == ["main"])
    rng = h["segments"][0]["range"]
    check("segment range covers its sites (1-based lines)",
          rng and rng[0] <= min(x["line"] for x in sites)
          and rng[1] >= max(x["line"] for x in sites))

    env = dict(os.environ)
    env["PATH"] = str(Path.home() / ".cargo" / "bin") + os.pathsep + env.get("PATH", "")
    r = subprocess.run([sys.executable, "-m", "oleum.harvest", str(HAZARDS),
                        "--cap", "5"], capture_output=True, text=True, cwd=REPO,
                       env=env, timeout=300)
    cli = json.loads(r.stdout)
    check("CLI emits the same shape (segments + capped candidates)",
          r.returncode == 0 and cli["sites"] == h["sites"]
          and all(len(x["candidates"]) <= 5
                  for seg in cli["segments"] for x in seg["sites"]))

    if FAILED:
        print(f"\n{len(FAILED)} FAILED")
        raise SystemExit(1)
    print("\nALL OK")


if __name__ == "__main__":
    main()
