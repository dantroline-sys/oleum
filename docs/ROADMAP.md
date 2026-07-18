# oleum roadmap

What remains, why each piece exists, and what it depends on.  Status
2026-07-15.  Contracts live in this directory and in the AMIGA-RUST-02/-03
spec set; this file is the memory-jogger, not the normative text.

## Built ✓

| Piece | Where |
|---|---|
| VINUR-OPS-01 op-annotation surface (map-keyed join, firewall, reserved learned fields) | vinur repo |
| SPIKE-0 join key + final id grammar `#free\|#inherent\|#trait` | `docs/OLEUM-SPIKE-0…`, `oleum/opkey.py` |
| MCP face: `rust_annotate` / `rust_hazards` / `rust_practice` | `oleum/mcp_server.py` |
| rust-base pack: 518 E-codes + 187 hazard-group lints + 11 curated op hazards, plus `codes.json` (DST-01 V3 oracle) | `producers/build_pack.py` → `dist/` |
| Probe harness: rustc adjudication, ra_divergence records | `oleum/probe.py` → `var/probes.db` |
| OLEUM-DST-01 Draft 2 (distillation contract) | `docs/OLEUM-DST-01…` |
| DST-01 harvester (S3): decision sites + candidate sets + op ids, item segments | `oleum/harvest.py` |

## Next, in dependency order

### 1. DST-01 digest builder (Phase A, D1–D7)
Per compilation unit, the deterministic context digest: crate ident (cargo
metadata), module path, **context tags** (start heuristic: cfg/attr scan for
`no_std`/`test`/`ffi`, async-fn presence, unsafe-block density), type
vocabulary + trait impls in scope (documentSymbol/hover over the unit's
items), deps-of-interest (use-tree walk), feature flags (cfg scan).  D8 (the
≤120-word unit gloss) is the only LM field and waits for the serving stack —
the builder should emit D1–D7 now and leave D8 null-able.
*Why:* segments must never be distilled bare; the digest is reused by every
segment of the unit, and its position first-in-suffix makes it prefix-cached.

### 2. DST-01 validators (Phase D)
V1 schema, V2 verbatim cap, V3 hazard existence (against `dist/codes.json` —
oracle already ships), V4 candidate closure (against the harvester's sets),
V5 identifier leak, V8 op-id echo (grammar parse + byte-match).  All pure
functions — buildable and unit-testable **today**, before any LM exists.
V6 auditor: cross-check sampled `failure_paths` + `lock/clone/alloc` events
against the extractor's op walk (≥5% sample, <2% disagreement halts the
batch).  V7 routing needs two models (see 4).
*Why:* the gates are the product's honesty; building them before the
distiller means the first LM output ever produced is already gated.

### 3. Frozen regression set
≥50 hand-annotated segments across context tags; gates must hold at 100% for
any prompt/model change to ship.  Bootstrap: harvest a well-known T1 crate,
Dan hand-annotates a first tranche of ~15 to unblock gate development.
*Why:* prompt changes are invisible regressions without it; it is the only
genuinely manual line item in the pipeline.

### 4. Distiller orchestrator (Phase C serving) — needs the 96 GB box
Batches segments unit-consecutively (digest rides the vLLM prefix cache),
builds prefix/suffix per §6, enforces retry-then-quarantine, runs V7 routing.
Model split advice on 96 GB: 32B-class primary (FP8/AWQ) + different-family
~24B secondary for V7 disagreement + D8 gloss, both resident; ~10k
tokens/request envelope; verify prefix-cache hits empirically (§6.1 note);
take the `lm_lease` if the embedder shares the GPU.
*Why V7 needs a second family:* independent errors — same weights re-sampled
agree with themselves.

### 5. Merge layer (§9)
Normalize `applicability` → content-hash (rule, op_id, register, tags); hash
hit increments `observed_count` + appends provenance instead of minting a
duplicate card; output lands in the **rust-learned** bundle via
vinur-as-library (same pattern as `build_pack.py`); unkeyed entries stored as
unattached observations; hazards cross-link existing `rust:diag:*` nodes.
*Why:* accumulation is RUST-03's rank denominator; without dedup every corpus
pass doubles the graph.

### 6. Trace capture (small, RUST-02 day-one item)
Log every `rust_annotate` call (op ids, spans count, unkeyed, graph_version,
knowledge status) to `var/` SQLite alongside probes.  Probe runs are already
recorded; annotate calls are not.
*Why:* coverage reporting (joined/requested over time) and gap detection —
which ops does real usage hit that the kb knows nothing about?

### 7. Researcher lane (contract to draft: OLEUM-RES-01)
The idle loop: gaps (unkeyed ops, ops with zero annotations from trace
capture, ra_divergence surprises) → framed research questions → keyless
sources (Stack Overflow / users.rust-lang.org via the StackExchange API,
docs.rs, error-index prose) → candidate practice snippets → **must-compile
probe gate** (`probe.run_probe` — already built) → cards into rust-learned at
research-tier trust.  Stricter than the distiller class: no probe pass, no
card.
*Why:* continual adaptation — the system learns what its users actually
stumble on, not what a corpus happened to contain.

### 8. AMIGA-RUST-03 learned layer
Harvest at scale over tiered corpora (T0 std=4.0 … T3=0.25) using
`oleum/harvest.py`; negatives = unchosen candidates (v2: shadow-key the top-K
candidates so negatives get real op ids); fix-commit mining + perturbation
with the poison gate (mutations that still compile are discarded);
**conditional rank** computed per typed-context features (receiver type,
context tags — never global frequency; the unwrap-vs-`?` conditionality
golden is the gate); writes `conditional_rank`/`anti_pattern_of` into the
reserved fields, which `ops_annotate` already relays.
*Why last:* every input it needs (ids, harvest, counts, probes, merge) is
produced by items 1–7.

### 9. LSP proxy lane (later)
Transparent `oleum-lsp` wrapper around rust-analyzer for completion-time
annotation in any LSP editor: forward everything, decorate/reorder
`textDocument/completion` responses using `ops_annotate` (ranking reorders,
never adds).  Shadow-accept the visible top-N to key candidates — MUST derive
the same `#trait` ids via declaration-jump.
*Why later:* the MCP diff-lane already delivers the product promise; this is
the keystroke-latency upgrade.

### 10. Packaging & distribution (later)
pyproject/uv packaging so `oleum` installs instead of running from the repo;
ship as: MCP server + `rust-base.kdb` (+ eventually `rust-learned.kdb`) +
vinur config fragment (`ops_regions`, `ask_exclude_facets`) — the data-pack
pattern shared with the clinical overlay.  Windows pass rides the
platform-independence track.

## Go-live checklist (96 GB box, vinur without vinkona)

1. Push vinur + oleum repos; pull on the box.
2. Pending vinur maintenance: one `migrate-vocab` run on the live kb.
3. `python3 producers/build_pack.py` (or copy `dist/`), then on the host:
   `import-bundle --path rust-base.kdb` + `facetize`.
4. Host config: `ops_regions = ["rust=rust-coding"]`,
   `ask_exclude_facets = ["domain:rust-coding"]` (fragment in
   `config.example.toml` on the vinur side).
5. Mount the MCP server in the coding agent (README snippet); smoke:
   `rust_annotate` a file using `unwrap()` → panic caveat returns.
6. When the distiller lands: vLLM up with the two-model split, empirical
   prefix-cache check, `lm_lease` coordination with the embedder.
