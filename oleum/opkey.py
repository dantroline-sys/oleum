"""Op-id synthesis — the SPIKE-0 recipe, accepted-form lane.

Id grammar (see docs/OLEUM-SPIKE-0_join_key_decision.md):
    rust:op:<defining-path>#<free|inherent|trait>

Signals, all obtainable from accepted-form code:
- use_url:  experimental/externalDocs at the call site (receiver-anchored)
- decl_url: externalDocs at the textDocument/declaration target — for a trait
  method this is the trait's own page, giving the FULL trait defining path
  (supersedes the completion-lane `(as Name)` form, which was name-only)
- hover:    fallback container+fn for primitives, where externalDocs is null

Receiver specialization is NOT part of the id — it is a context feature
(RUST-03 keys conditional rank on typed context, never on id variants).
"""
import re

_FENCE = re.compile(r"```rust\n(.*?)\n```", re.S)


def url_to_path(url):
    """rustdoc URL -> defining path + anchor kind, version-stripped, host-branched."""
    if not isinstance(url, str) or "://" not in url:
        return None, None
    host, _, path = re.sub(r"^https?://", "", url).partition("/")
    if host == "docs.rs":
        path = re.sub(r"^([\w-]+)/[^/]+/", "", path)     # <crate>/<version>/
    else:
        path = re.sub(r"^(stable|nightly|beta)/", "", path)
    path = path.replace(".html", "")
    container, _, anchor = path.partition("#")
    kind = "free" if "/fn." in container else None
    if container.rpartition("/")[2].startswith("trait."):
        kind = "trait"
    container = re.sub(r"(struct|enum|trait|union|primitive|fn)\.", "",
                       container).replace("/", "::")
    name = anchor.split(".")[-1] if anchor else ""
    return (container + ("::" + name if name else "")), kind


def hover_fallback(hover_markdown):
    blocks = _FENCE.findall(hover_markdown or "")
    if len(blocks) < 2:
        return None
    container = blocks[0].strip().splitlines()[-1].strip()
    m = re.search(r"\bfn\s+(\w+)", blocks[1])
    return f"{container}::{m.group(1)}" if m else None


def synth(use_url, decl_url, hover_markdown):
    """Canonical op id, or None when the symbol is unkeyable (fail-open)."""
    decl_path, decl_kind = url_to_path(decl_url)
    if decl_kind == "trait":                 # trait method: id anchors on the trait
        return f"rust:op:{decl_path}#trait"
    use_path, use_kind = url_to_path(use_url)
    if use_path:
        return f"rust:op:{use_path}#{use_kind or 'inherent'}"
    if decl_path:
        return f"rust:op:{decl_path}#{decl_kind or 'inherent'}"
    base = hover_fallback(hover_markdown)    # primitives: externalDocs is null
    return f"rust:op:{base}#inherent" if base else None
