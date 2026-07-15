# OLEUM-SPIKE-0 — join key: rust-analyzer candidate → op-node id

Status: **DECIDED** 2026-07-15.  This was the blocking item of AMIGA-RUST-02
(§SPIKE-0).  Evidence: `spikes/spike0/results/spike0_report.json`, produced by
`spikes/spike0/run_spike.py` against `fixtures/join_ws` under the pinned
toolchain (rustc / rust-analyzer 1.97.0, `rust-toolchain.toml`).

## 1. Question

RUST-02 rents legality from rust-analyzer.  The oleum runtime must map each
candidate rust-analyzer serves (and each op appearing in agent-written code) to
a stable op-node id in vinur's op-id region — machine-independent, session-
stable, and identical for every syntactic route to the same operation
(re-export, macro expansion, Deref, extension trait).

## 2. Mechanisms evaluated

Nine fixture cases: inherent method, associated fn, Deref method on a
primitive, std trait method, extension trait on a std type, re-exported type,
macro-generated method, user trait method, generic free fn.

| | Mechanism | Verdict |
|---|---|---|
| A | completion item as served | No paths at all — but carries the **`(as Trait)`** attribution (`labelDetails.detail`) that every other mechanism drops.  Contributes the disambiguator. |
| B | `completionItem/resolve` | Adds documentation only; `data.hash` is an opaque per-position value, not a symbol id.  **Cannot key.** |
| C | shadow-accept + `hover` | Container path is **impl-site-relative**, not canonical: the same type showed as `ext::Widget` or `ext::inner::Widget` depending on where the impl block sits.  Drops trait attribution on impl'd methods.  Fallback only. |
| D | `textDocument/moniker` | **Not implemented** by rust-analyzer 1.97 (`monikerProvider` absent, request → "unknown request").  The "monikers first" question is answered by the capability handshake: dead. |
| E | `definition` target path | Machine- and toolchain-path-specific.  Tiebreak evidence only. |
| F | `experimental/externalDocs` | **Canonical defining-crate path**, receiver-anchored; normalizes re-exports *and* macro-generated impl sites (all three Widget routes → `ext::inner::Widget`); extension-trait method anchors on the receiver (`Vec::mean`).  Null for primitive-type methods (`str::trim`) — the one hole, which C fills. |

## 3. Decision

```
op_id = rust:op:<defining-path>#<disambig>
```

- `<defining-path>` — the version-stripped `experimental/externalDocs` URL path
  (host-branched normalization: docs.rs embeds `<crate>/<version>/`,
  doc.rust-lang.org embeds a channel segment); **fallback** to hover container +
  fn name where externalDocs is null (primitives).
- `<disambig>` — `inherent` | `free` | `as:<Trait>`, the trait taken from the
  completion item's `(as Trait)` labelDetails (mechanism A).

Result on the fixture: **9/9 cases produce correct canonical ids, byte-stable
across two fresh server sessions**:

```
rust:op:alloc::vec::Vec::new#inherent
rust:op:alloc::vec::Vec::push#inherent
rust:op:core::str::trim#inherent                         (hover fallback)
rust:op:core::iter::traits::iterator::Iterator::map#as:Iterator
rust:op:alloc::vec::Vec::mean#as:SliceStats               (receiver-anchored)
rust:op:ext::inner::Widget::label#inherent                (re-export normalized)
rust:op:ext::inner::Widget::generated#inherent            (macro-generated)
rust:op:ext::inner::Widget::greet#as:Greet
rust:op:core::mem::swap#free
```

## 4. Consequences

1. **Keys are defining paths, not facade paths** — `alloc::vec::Vec`, not
   `std::vec::Vec`; `core::iter::traits::iterator::Iterator`, not
   `std::iter::Iterator`.  This is the stable choice (it is what the toolchain
   itself reports everywhere).  A std-facade alias table is a *display/UX*
   layer and MUST NOT be key material.  The `std::vec::Vec::push` id used in
   vinur's `ops_annotate_test.py` fixture is a placeholder, not normative:
   importers/harvesters MUST emit defining paths.
2. **Keying requires accepted-form code.**  A/B at the truncated site cannot
   key; externalDocs/hover need the identifier present.  Runtime shapes that
   satisfy this: (a) MCP lane — annotate code the agent has already written
   (diff/commit granularity), which is accepted-form by definition; (b) LSP
   completion lane — shadow-accept the visible top-N candidates in an unsent
   buffer copy and annotate asynchronously.  (a) ships first.
3. **`as:<Trait>` is a trait *name*, not a full path** (`SliceStats`,
   `Iterator`).  Two same-named traits in scope could collide.  Mitigation:
   at harvest/import time resolve the trait's own defining path (definition +
   externalDocs on the trait ident) and record the full form; runtime keys
   with the name form and the probe harness adjudicates collisions.  OPEN,
   tracked for the runtime contract.
4. **`experimental/*` protocol surface** — no stability guarantee upstream.
   Acceptable because the toolchain is pinned by contract policy (a bump is a
   deliberate event that invalidates probe verdicts and forces a re-run of
   this spike; the report is the regression baseline).
5. docs.rs URLs embed the crate **version** — stripped during normalization,
   so a dependency bump does not churn ids.  Crate identity + version live in
   node metadata, not in the id.

## 5. Non-decisions

Containment / ordering / non-suppression enforcement stays oleum-side per
AMIGA-RUST-02; vinur's map-keyed `ops_annotate` (VINUR-OPS-01, built) is the
join surface.  Nothing in this decision touches vinur.
