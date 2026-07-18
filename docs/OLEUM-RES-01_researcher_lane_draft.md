# OLEUM-RES-01 — Researcher Lane (Rust) — Draft 1 for review

**Status:** Draft 1 (skeleton for Dan's review; not yet normative)
**Depends on:** AMIGA-RUST-02 (probe adjudication — built), OLEUM-DST-01
(card shapes, gates), VINUR-OPS-01 (rust-learned surface), trace capture
(`oleum/traces.py` — built)
**Key words:** RFC 2119.

## 1. Purpose

Continual adaptation: the system learns what its users actually stumble on.
Gaps observed in real usage become framed research questions; answers become
cards only after deterministic adjudication.  Stricter than the distiller
class: **no probe pass, no card** — research text is the least trusted input
in the whole system.

## 2. Gap sources (priority order)

1. `traces.gaps()` — ops repeatedly annotated bare (used in real code, no
   knowledge).  Frequency-ranked; the queue head.
2. Probe divergences — `ra_only` records from `var/probes.db` (r-a false
   positives are product-threatening and research-worthy).
3. Quarantine clusters — DST segments repeatedly quarantined for the same
   op/pattern (the distiller couldn't say; maybe the web can).
4. Agent-explicit — a `rust_practice` question that produced an abstention.

## 3. Sources (keyless, in trust order)

Stack Exchange API (Stack Overflow, users.rust-lang.org is Discourse — has a
JSON API), docs.rs / doc.rust-lang.org prose, the rustc error-index long
form, Clippy lint pages, rustsec advisories.  No general web crawling; the
Mac-host research posture (LM-routed keyless tools) applies.

## 4. The trust gate (the point of the contract)

- Every candidate practice MUST be reduced to (a) a claim in conditional-rule
  form and (b) a **probe snippet** exercising it.
- `probe.run_probe` MUST pass (compiles under the pinned toolchain; if the
  claim names a diagnostic, the probe MUST reproduce that diagnostic in the
  negative twin).
- Passing claims become cards in `rust-learned` at research-tier trust (below
  distilled), criteria carrying claim + probe hash + source URL; failing
  claims are logged, never carded.
- V3/V5/V8 gates from DST-01 apply unchanged (codes.json, identifier leak,
  op-id grammar).

## 5. Cadence

Idle lane on the oleum daemon (not vinkona's researcher — separate process,
separate queue), budget-capped per day; every run appends to
`var/research.jsonl` (question, sources consulted, verdicts) for audit.

## 6. Open questions

1. Negative-twin probes: required for every claim, or only diagnostic-naming
   claims?  (Lean: only diagnostic-naming; general claims get compile-only.)
2. Licence posture for SO snippets (CC BY-SA): probe snippets are
   re-authored minimal reproductions, never copied text — confirm this
   satisfies the AMIGA-RUST-03 verbatim cap by construction.
3. Whether answers feed conditional rank (RUST-03) or stay rank-neutral
   until validated by traces.
