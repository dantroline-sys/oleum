"""Adjudication probe harness: rustc verdicts, r-a native diagnostics, divergence
records.  Needs the pinned toolchain."""
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from oleum import probe

FAILED = []

E0502 = """fn main() {
    let mut v = vec![1];
    let a = &v[0];
    v.push(2);
    println!("{a}");
}
"""

CLEAN = """fn main() {
    let v = [1, 2, 3];
    let s: i32 = v.iter().sum();
    println!("{s}");
}
"""


def check(label, cond):
    print(("  ok  " if cond else "FAIL  ") + label)
    if not cond:
        FAILED.append(label)


def main():
    before = 0
    if probe.DB.is_file():
        con = sqlite3.connect(probe.DB)
        before = con.execute("SELECT count(*) FROM probe_runs").fetchone()[0]
        con.close()

    r = probe.run_probe(E0502)
    codes = {d["code"] for d in r["rustc"]}
    check("borrow-conflict snippet does not compile", r["compiles"] is False)
    check("rustc reports E0502 with a primary span line",
          "E0502" in codes and any(d["code"] == "E0502" and d["line"]
                                   for d in r["rustc"]))
    check("divergence buckets partition the codes",
          set(r["divergence"]["rustc_only"]) | set(r["divergence"]["agree"])
          >= {"E0502"})
    check("ra_only is the watched direction (list present)",
          isinstance(r["divergence"]["ra_only"], list))

    c = probe.run_probe(CLEAN)
    check("clean snippet compiles", c["compiles"] is True)
    check("clean snippet: no rustc errors",
          not any(d["level"] == "error" for d in c["rustc"]))
    check("clean snippet: no divergence",
          c["divergence"]["rustc_only"] == [] and c["divergence"]["ra_only"] == [])

    con = sqlite3.connect(probe.DB)
    rows = con.execute("SELECT compiles, divergence FROM probe_runs ORDER BY id "
                       "DESC LIMIT 2").fetchall()
    total = con.execute("SELECT count(*) FROM probe_runs").fetchone()[0]
    con.close()
    check("both runs recorded in var/probes.db", total == before + 2)
    check("recorded verdicts match", sorted(x[0] for x in rows) == [0, 1])

    if FAILED:
        print(f"\n{len(FAILED)} FAILED")
        raise SystemExit(1)
    print("\nALL OK")


if __name__ == "__main__":
    main()
