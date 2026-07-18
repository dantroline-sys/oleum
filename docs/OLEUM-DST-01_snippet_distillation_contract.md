# OLEUM-DST-01 — Snippet Distillation Prompt Contract (Rust)

**Status:** Draft 2 (amendments adopted 2026-07-15)
**Applies to:** Oleum distiller pipeline (harvester → distiller → card merge)
**Depends on:** AMIGA-RUST-02 (legality rented from rust-analyzer), AMIGA-RUST-03
(learned layer, trust tiers, context tags), OLEUM-SPIKE-0 §6 (op-id grammar),
VINUR-OPS-01 (reserved learned fields, `rust-learned` bundle surface)
**Key words:** MUST, MUST NOT, SHOULD, SHOULD NOT, MAY are to be interpreted per RFC 2119.

**Changelog Draft 1 → Draft 2**

- §4 S3 / §7: harvested decision sites and strategy entries now carry the
  **op-id join** (SPIKE-0 §6 grammar); new gate V8 enforces it.
- §7: `hot_path_plausible` admits `"abstain"`; `confidence` range declared.
- §8 V3: oracle named — the `codes.json` emitted by the rust-base pack producer.
- §8 V6: v1 audit scope pinned to failure paths + lock/clone/alloc events against
  the deterministic op walk; MIR walk deferred to v2.
- §8 V7: routing primary is two-model **disagreement**; confidence is secondary.
- §4 S2 / §6.2: split segments get a signatures-only sibling stub (Draft 1 open
  question 2, resolved yes).
- §6.1: unit-consecutive batching made normative (digest rides the prefix cache).
- New §9: merge & dedup policy — normalized-rule dedup, `observed_count`
  accumulation, `rust-learned` bundle, learned-tier trust.
- §10: D8 kept by default, served by the small model, A/B later.

---

## 1. Purpose

This spec defines the contract between the orchestration layer and the distiller
LM for extracting strategic, purpose, and execution annotations from Rust code
segments. It covers:

1. **Contextualisation** — how a compilation unit is digested before
   segmentation, so that no segment is ever presented to the LM context-free.
2. **Segmentation** — deterministic division of the unit into analysis segments.
3. **Prompting** — the fixed prompt skeleton (cacheable prefix) and the
   per-segment variable suffix.
4. **Output schema** — the JSON card the LM MUST emit, in conditional-rule form.
5. **Validation gates** — what the merge layer accepts, quarantines, or rejects.
6. **Merge policy** — how accepted cards enter the knowledge graph.

The distiller MAY annotate; it MUST NOT originate. Every claim it emits is
either (a) auditable by a deterministic oracle (rustc, Clippy, rust-analyzer),
in which case it is audited, or (b) not auditable, in which case it is tagged as
such and subject to adjudication routing (§8).

---

## 2. Pipeline overview

```
source file(s)
  → Phase A: context digest        (deterministic extraction + one LM gloss call)
  → Phase B: segmentation          (deterministic, syntax-tree driven, no LM)
  → Phase C: per-segment distill   (LM, batched, cached prefix)
  → Phase D: validation + merge    (deterministic schema/verbatim/audit gates,
                                    then merge into the rust-learned bundle §9)
```

Phases A and B run once per compilation unit. Phase C runs once per segment.
Phase C prompts share a byte-identical prefix within a batch (§6.1) to exploit
vLLM prefix caching where the serving stack supports it.

---

## 3. Phase A — Contextualisation

### 3.1 Rationale

A Rust segment's meaning is mostly determined by its surroundings: the trait
vocabulary in scope, the ownership discipline of neighbouring types, the crate's
regime (async, no_std, kernel, FFI). Segments MUST NOT be distilled bare.
Because full-module context would blow the per-segment token budget at batch
scale, context is compressed once into a **digest** and reused across every
segment of the unit.

### 3.2 Digest content

The digest is a JSON object built per compilation unit. Fields D1–D7 are
extracted **deterministically** (syn / rust-analyzer / cargo metadata). Field D8
is the only LM-produced field.

| Field | Source | Content |
|---|---|---|
| D1 `crate_ident` | cargo metadata | crate name, version, edition |
| D2 `module_path` | syntax tree | full module path of the unit |
| D3 `context_tags` | AMIGA-RUST-03 tagger | one or more of `general`, `async`, `no_std`, `kernel`, `ffi`, `unsafe_heavy`, `test` |
| D4 `type_vocabulary` | rust-analyzer | locally defined types/enums/traits with their signatures (names + generics + bounds; no bodies) |
| D5 `trait_impls_in_scope` | rust-analyzer | trait impls visible at the unit's items |
| D6 `deps_of_interest` | use-tree walk | external crates actually imported, with the items used |
| D7 `feature_flags` | cfg attribute scan | active cfg/feature gates on the unit |
| D8 `unit_gloss` | LM (one call) | ≤120-word abstract of what this unit is *for* within the crate |

**D8 requirements.** The gloss call receives the file plus D1–D7 and MUST
answer only: role of this unit in the crate, the invariants it appears
responsible for, and its principal collaborators. The gloss MUST NOT quote
source; it is prose about the unit, capped at 120 words. The gloss prompt uses
the same skeleton discipline as Phase C. The gloss SHOULD be served by the
small (secondary) model; it is amortised once per unit.

### 3.3 Trust tier propagation

The unit's corpus trust tier (T0–T3 per AMIGA-RUST-03) MUST be carried in the
digest and MUST NOT be visible to the LM (the distiller does not get to know it
is reading std — it should reason from the code, not from provenance
authority). The tier is re-attached at merge.

---

## 4. Phase B — Segmentation

Segmentation is deterministic. The LM MUST NOT choose segment boundaries.

- **S1.** Segments are produced at Rust *item* granularity: `fn`, `impl` block,
  `trait` definition, `macro_rules!`, top-level `const`/`static` with
  non-trivial initialisers. Statements and expressions are never segments on
  their own.
- **S2.** An `impl` block exceeding the size cap (S4) MUST be split at method
  boundaries, each method carrying a synthetic header line
  `// impl <Type> (split k/n)`. Each split segment's prompt suffix MUST include
  a **sibling stub**: the signatures (plus first doc line, no bodies) of the
  other methods of the same impl block, so intra-impl invariants remain visible
  (§6.2).
- **S3.** Each segment record MUST carry: byte span in source, item path, the
  rust-analyzer candidate sets for each call site within it, and the author's
  actual choice at each site (the harvester inversion from AMIGA-RUST-03).
  For the author's choice, and for each candidate where derivable, the
  harvester MUST attach the **op id** under the OLEUM-SPIKE-0 §6 grammar
  (`rust:op:<defining-path>#<free|inherent|trait>`), derived by the same
  extractor the annotation lane uses (`oleum/opkey.py`). Sites whose choice
  cannot be keyed are marked `"op_id": "unkeyed"`; they are still interrogated
  (G1) but their strategy entries do not attach to op nodes at merge (§9) and
  are logged.
- **S4.** Size cap: a segment MUST NOT exceed 400 lines or 6,000 tokens of
  source, whichever is hit first. Items exceeding both after S2 splitting are
  quarantined for manual review rather than truncated — a truncated segment
  produces confidently wrong strategy cards.
- **S5.** Doc comments and attributes attached to the item are part of the
  segment. Free-floating comments between items belong to the following item.

---

## 5. The interrogation set

Phase C asks the LM a fixed question battery. Questions are grouped by axis;
each maps to a schema field (§7). The contrastive form is mandatory wherever a
rust-analyzer candidate set exists: the question is never "what does this do"
but "why this, and not the adjacent legal alternatives."

### 5.1 Purpose (contract of the item)
- **P1.** What contract does this item fulfil — preconditions, postconditions,
  invariants maintained?
- **P2.** What breaks, and where, if this item is deleted? (Blast radius within
  the digest's type vocabulary.)
- **P3.** What misuse does the *shape* of the signature make unrepresentable?

### 5.2 Strategy (the choice made)
- **G1.** At each harvested decision point: which candidates from the r-a set
  were plausible, and what did the author's choice trade away?
- **G2.** Is a named pattern instantiated (newtype, typestate, builder,
  interior mutability, RAII guard, sealed trait, …)? State its **applicability
  conditions**, not its presence.
- **G3.** Register classification: `canonical_idiom` | `deliberate_deviation` |
  `language_workaround`. Deviations MUST carry the justification evident in
  code or docs, or the flag `justification_absent`.

### 5.3 Execution (mechanics)
- **E1.** Ownership flow: where values move, where they are borrowed, where
  cloned/allocated, where locks are acquired and released.
- **E2.** Failure paths: every `?`, every possible panic (including indexing,
  unwrap, arithmetic), every silent truncation or lossy cast.
- **E3.** Cost profile: allocation count class (`zero`, `bounded`,
  `per-element`, `unbounded`), and whether the item is plausibly hot-path given
  its callers in D5/D6.

### 5.4 Regime (conditioning)
- **R1.** Which digest context tags condition the strategy? A card valid only
  under `async` MUST say so.
- **R2.** What does this code assume about its neighbours (invariants imported
  from D4 types)?

### 5.5 Counterfactual / hazard
- **C1.** What nearly-identical code fails to compile here, and with which
  rustc error code? (Joins to the error-index cascade.)
- **C2.** Which Clippy lints does this pattern narrowly avoid?

---

## 6. Phase C — Prompt construction

### 6.1 Cached prefix (byte-identical within a batch)

The prefix MUST contain, in order:

1. The distiller role statement (§6.3).
2. The full interrogation set (§5) verbatim.
3. The output JSON schema (§7) verbatim.
4. The abstraction and abstention rules (§6.4).

The prefix MUST NOT contain anything unit-specific. Any change to the prefix is
a spec revision and MUST bump the `contract_version` emitted in every card.

**Batching.** The orchestrator SHOULD schedule all segments of a unit
consecutively within a batch. Because the digest is the first suffix component
(§6.2), prefix + digest is then a shared prefix across the unit's segments, and
automatic prefix caching covers the digest as well as the skeleton.

**Serving note.** If the serving model is a hybrid linear-attention
architecture, automatic prefix caching MAY be unavailable; the orchestration
layer MUST NOT assume cache hits in its throughput model without an empirical
check on the deployed vLLM version.

### 6.2 Variable suffix (per segment)

In order:

1. The context digest (D1–D8), with trust tier stripped (§3.3).
2. For S2-split segments only: the sibling stub (signatures + first doc lines
   of the impl's other methods; no bodies).
3. The segment source, fenced, with the synthetic split header if S2 applied.
4. The harvested decision points: for each call site, the r-a candidate list
   (with op ids per S3) and the author's actual choice, as structured JSON —
   never prose.
5. The single instruction line: *"Emit exactly one JSON object conforming to
   the schema. No prose before or after."*

### 6.3 Role statement (normative text)

> You are a code analyst producing structured annotations of Rust source for a
> knowledge graph. You describe and generalise; you never invent APIs, never
> propose code, and never claim facts about items not shown in the digest or
> segment. Where the segment does not support an answer, you abstain on that
> field. Your answers must hold for the *pattern*, not the *snippet*: express
> strategy as conditional rules ("when X, prefer Y over Z because W"), never as
> a description of this file.

### 6.4 Abstraction and abstention rules (normative text, in prefix)

- Strategy and purpose fields MUST be expressed as conditional rules keyed on
  typed-context features, never on identifiers from this segment except where
  the identifier is a std/public-API name.
- Verbatim source reproduction is capped per AMIGA-RUST-03 legal posture: probe
  snippets only, and no contiguous quotation exceeding the diff-hunk cap. All
  other references to source use item paths.
- Every field admits the value `"abstain"`. Abstention on a field is always
  acceptable; a fabricated answer is never acceptable. (Weighting per AMIGA
  four-tier doctrine: false confidence is 10× worse than over-abstention.)
- The analyst MUST NOT use provenance, popularity, or style-guide authority as
  justification; justification derives only from semantics visible in the
  segment + digest.

---

## 7. Output schema

One JSON object per segment. All string enums are closed; unknown values fail
validation.

```json
{
  "contract_version": "OLEUM-DST-01/2",
  "segment_id": "string  // orchestrator-supplied, echoed verbatim",
  "purpose": {
    "contract": "string | abstain",
    "blast_radius": ["item paths from digest"],
    "misuse_prevented": "string | abstain"
  },
  "strategy": [
    {
      "decision_site": "string // harvester site id, echoed",
      "op_id": "string // harvester-supplied op id (SPIKE-0 grammar) or 'unkeyed', echoed verbatim",
      "chosen": "string // author's choice, echoed",
      "rejected_alternatives": [
        { "candidate": "string", "op_id": "string | unkeyed", "traded_away": "string" }
      ],
      "pattern": "string | none",
      "applicability": "string // conditional-rule form: when X, prefer Y over Z because W",
      "register": "canonical_idiom | deliberate_deviation | language_workaround",
      "deviation_justification": "string | justification_absent | n/a"
    }
  ],
  "execution": {
    "ownership_events": [ { "kind": "move|borrow|clone|alloc|lock", "at": "string" } ],
    "failure_paths": [ { "kind": "try|panic|truncation", "at": "string", "note": "string" } ],
    "alloc_class": "zero | bounded | per_element | unbounded | abstain",
    "hot_path_plausible": "true | false | abstain"
  },
  "regime": {
    "conditioning_tags": ["subset of digest context_tags"],
    "neighbour_assumptions": ["string"]
  },
  "hazards": [
    { "kind": "rustc_error | clippy_lint", "code": "string  // e.g. E0502, clippy::needless_collect",
      "trigger": "string // the near-miss, abstractly stated" }
  ],
  "confidence": { "purpose": 0.0, "strategy": 0.0, "execution": 0.0 },
  "abstained_fields": ["string"]
}
```

Field-level notes:

- `strategy[].applicability` MUST parse as a conditional rule; the merge layer
  rejects entries containing this segment's private identifiers.
- `strategy[].op_id` and `rejected_alternatives[].op_id` are harvester-supplied
  and echoed; the LM MUST NOT construct or alter op ids (V8).
- `hazards[].code` MUST be a real rustc error-index code or Clippy lint name;
  the merge layer verifies existence against the imported index and rejects
  unknown codes (this is the cheapest confabulation trap in the whole
  pipeline). The oracle is the `codes.json` emitted by the rust-base pack
  producer alongside the pack, pinned to the same toolchain.
- `confidence` values are floats in **[0, 1]**. No calibration is assumed;
  gates treat them as ordinal (§8 V7 routes primarily on model disagreement).
- `execution` claims are compiler-adjacent and auditable; `purpose`/`strategy`
  are not (§8).

---

## 8. Phase D — Validation gates (hard, regression-tested)

| Gate | Rule | On failure |
|---|---|---|
| V1 schema | Object validates against §7 exactly; no extra keys | reject segment output, one retry with error appended, then quarantine |
| V2 verbatim | No contiguous source quotation beyond the AMIGA-RUST-03 cap outside `chosen`/`decision_site` echoes | reject, no retry (systemic prompt fault) |
| V3 hazard existence | Every `hazards[].code` exists in `codes.json` (rustc error index + Clippy lint list, toolchain-pinned) | strip offending entries, log |
| V4 candidate closure | Every `rejected_alternatives[].candidate` ∈ the supplied r-a set | strip offending entries, log (graph MUST NOT originate) |
| V5 identifier leak | `applicability` strings contain no segment-local identifiers | reject entry |
| V6 audit (execution) | Sampled `failure_paths` and `ownership_events` of kind `lock`/`clone`/`alloc` spot-checked against the deterministic op walk (the annotation-lane extractor) at rate ≥5% per corpus batch; disagreement rate MUST be <2%. (v2 MAY extend the auditor with a MIR walk for `move`/`borrow` events; v1 does not audit those kinds.) | halt batch, review distiller/model config |
| V7 adjudication routing | Cards destined for T0/T1 tiers are distilled by **both** models; `purpose`/`strategy` disagreement → quarantine tier. Secondary trigger: `confidence.strategy < 0.6` routes to the second model even off-T0/T1. | n/a (routing rule) |
| V8 op-id echo | Every `strategy[].op_id` (and candidate `op_id`) parses under the SPIKE-0 §6 grammar or equals `"unkeyed"`, and byte-matches the harvester-supplied value | strip offending entry, log (an altered id is a fabrication) |

Regression suite: a frozen set of ≥50 hand-annotated segments spanning all
context tags. Gates V1–V5 and V8 MUST hold at 100% on the frozen set for any
prompt or model change to ship. V6 disagreement on the frozen set MUST NOT
regress. (Bootstrap: harvest a well-known T1 crate, hand-annotate a first
tranche of ~15 to unblock gate development, grow to 50 before the first
corpus-scale run.)

---

## 9. Merge policy (accepted cards → knowledge graph)

- **Destination.** Distilled cards land in the **`rust-learned`** bundle, never
  `rust-base`. Trust is learned-tier (below curated); the corpus trust tier
  stripped in §3.3 is re-attached here as provenance metadata.
- **Attachment.** A strategy entry attaches to the op node named by its
  `op_id`. `"unkeyed"` entries are stored as unattached observations (visible
  to review tooling, never served through `ops_annotate`).
- **Dedup / accumulation.** Before insert, the merge layer normalises the
  `applicability` rule (case-fold, whitespace-collapse; V5 has already removed
  local identifiers) and content-hashes (rule, op_id, register,
  conditioning_tags). A hash hit MUST NOT create a new card: it increments the
  existing card's **`observed_count`** (VINUR-OPS-01 reserved field), appends
  the segment's provenance to support, and refreshes `last_observed`. This
  accumulation is the denominator AMIGA-RUST-03's conditional rank consumes.
- **Hazard cross-links.** Accepted `hazards[]` entries link the op node to the
  existing `rust:diag:*` node for the code; they do not create diag nodes.
- **Adjudication class.** Distilled cards are, as a class, **not**
  probe-adjudicated: their auditable claims are audited (V6) and their
  unauditable claims are two-model-routed (V7). The researcher lane's
  must-compile probe gate is a separate, stricter path and stays that way —
  the two card factories remain distinguishable in the graph via their support
  docs.

---

## 10. Open questions (not blocking)

1. Whether D8 (unit gloss) earns its cost — **default: keep**, served by the
   small model (amortised per unit); A/B on frozen-set card quality when the
   suite exists.
2. Prefix-cache economics on the chosen serving model (see §6.1 serving note)
   — measure before committing the throughput budget.
3. Whether `observed_count` accumulation should be tier-weighted at merge time
   or left raw for RUST-03's rank computation to weight — current lean: store
   raw counts + provenance, weight at rank time (keeps the merge layer dumb).

---

## Non-normative build-order note

DST-01 consumes the harvester (S3 candidate sets + op ids), which extends the
existing annotation-lane extractor (semantic-token walk + one completion
request per call site). Order: harvester extension → digest builder (D1–D7
deterministic) → prompt skeleton + validators (V1–V5, V8 are pure functions;
V3's oracle already ships with the rust-base pack) → frozen-set bootstrap →
Phase C serving.
