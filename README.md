# oleum

*(working name — Rust-Oleum parody; final name TBD)*

Rust coding assistance over the [vinur](../vinur) knowledge host: guide a
coding LM to "make no mistakes" by annotating the operations it is about to
use with hazards, rationale and learned rankings from a curated knowledge
graph — while **renting legality from rust-analyzer** (the graph may annotate,
it must never originate or suppress candidates).

## Architecture

One daemon, protocol-agnostic core, thin protocol faces:

- **MCP server (first face)** — stdio Model Context Protocol server exposing
  hazard-check / op-annotation / practice-lookup tools to whatever coding
  agent the user runs (Claude Code, Cursor, Zed, JetBrains, VS Code agent
  mode).  Coarse granularity: annotate the ops in a diff or plan.
- **LSP proxy (second face, later)** — transparent wrapper around
  rust-analyzer for completion-time annotation in any LSP editor.
- **ACP (possible later face)** — only if oleum ever becomes the interactive
  agent itself; out of scope for the proof of concept.

The daemon owns: a **pinned** rust-analyzer + rustc pair (`rust-toolchain.toml`
— bumps are deliberate events that invalidate probe verdicts), the curated
example-corpus tree, the researcher lane, and a local SQLite for traces/raw
harvest records.  Knowledge (aggregates + cards) lives in the shared vinur kb
under the op-id region / domain facet; oleum consumes it over vinur's tool
host (`ops_annotate`, domain-filtered `kb_ask`).  The Rust toolchain never
touches vinur.

## Programme

| Contract | Scope | Status |
|---|---|---|
| VINUR-OPS-01 | vinur-side op-annotation surface | **built** (vinur repo) |
| SPIKE-0 | join key: r-a candidate → op id | **decided** — [docs/OLEUM-SPIKE-0_join_key_decision.md](docs/OLEUM-SPIKE-0_join_key_decision.md) |
| AMIGA-RUST-02 | annotation runtime, hazard import, probes | next |
| AMIGA-RUST-03 | learned layer (harvest, negatives, conditional rank) | after -02 |

## Layout

```
rust-toolchain.toml        pinned rustc + rust-analyzer + rust-src
fixtures/join_ws/          golden fixture workspace (9 join cases; app + ext crates)
spikes/spike0/             stdlib-only LSP harness + join-key evaluation
docs/                      decision records
```

## Running the spike

```
rustup toolchain install 1.97.0 --profile minimal --component rust-analyzer,rust-src
cd fixtures/join_ws && cargo check --bin completed     # fixture gate
cd ../../spikes/spike0 && python3 run_spike.py         # two passes + stability diff
```

Pure-python stdlib harness, no services; the report lands in
`spikes/spike0/results/spike0_report.json` and is committed as the regression
baseline for toolchain bumps.

## License

PolyForm Noncommercial planned (vinkona precedent); not yet applied — do not
distribute.
