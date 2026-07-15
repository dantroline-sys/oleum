"""oleum configuration.  TOML over DEFAULTS, vinur house pattern (but tiny)."""
import os
import tomllib
from pathlib import Path

DEFAULTS = {
    # vinur knowledge host (POST /call).  ops_annotate must be advertised there,
    # i.e. the host's config needs its op-id region configured.
    "vinur_url": "http://127.0.0.1:8771",
    "vinur_token": "",
    # facet value oleum opts into on kb_ask (must match the host's region tag)
    "domain_facet": "rust-coding",
    # cargo workspace roots the daemon may open; rust_annotate can also infer the
    # root by walking up from the file to the outermost Cargo.toml
    "workspaces": [],
    # rust-analyzer binary; empty -> resolve from PATH (rustup shim honours
    # rust-toolchain.toml, keeping the pin authoritative)
    "ra_bin": "rust-analyzer",
    "ra_quiesce_timeout": 300.0,
    "request_timeout": 120.0,
}


def load(path=None) -> dict:
    cfg = dict(DEFAULTS)
    p = path or os.environ.get("OLEUM_CONFIG") or "oleum.toml"
    p = Path(p)
    if p.is_file():
        try:
            cfg.update(tomllib.loads(p.read_text()))
        except (tomllib.TOMLDecodeError, OSError) as e:
            raise SystemExit(f"oleum: bad config {p}: {e}")
    return cfg
