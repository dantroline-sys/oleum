"""OLEUM-DST-01 pipeline: digest (Phase A) → prompts (Phase C assembly) →
validate (Phase D gates) → orchestrate (serving + routing) → merge (Phase D
merge into the rust-learned bundle).  Everything except the serving call is
deterministic and runs offline; the orchestrator takes any OpenAI-compatible
endpoint (the 96GB vLLM when deployed, a stub in tests)."""
