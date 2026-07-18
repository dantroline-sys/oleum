"""Phase A — the per-unit context digest, fields D1–D7 (OLEUM-DST-01 §3).

All deterministic: cargo metadata, path-derived module path, heuristic context
tags, rust-analyzer documentSymbol for the type vocabulary and impls, a
use-tree walk for deps, a cfg scan for feature gates.  D8 (unit_gloss) is the
only LM field — left None here; the orchestrator fills it via the small model.

The corpus trust tier is attached OUTSIDE the digest (unit record), never
inside it: §3.3 forbids showing provenance authority to the distiller.
"""
import json
import re
import subprocess
from pathlib import Path

from ..probe import _env

_TYPE_KINDS = {5: "class", 10: "enum", 11: "interface", 23: "struct", 26: "type"}
_USE = re.compile(r"^\s*(?:pub\s+)?use\s+([^;]+);", re.M)
_CFG = re.compile(r"#!?\[cfg(?:_attr)?\(([^)]*)\)\]")
_META_CACHE = {}


def _cargo_meta(ws_root):
    ws_root = str(Path(ws_root).resolve())
    if ws_root not in _META_CACHE:
        r = subprocess.run(["cargo", "metadata", "--no-deps", "--format-version", "1"],
                           capture_output=True, text=True, cwd=ws_root, env=_env())
        _META_CACHE[ws_root] = json.loads(r.stdout) if r.returncode == 0 else {}
    return _META_CACHE[ws_root]


def crate_ident(ws_root, unit_path):
    """D1: the package owning the unit, by longest manifest-dir prefix."""
    unit = str(Path(unit_path).resolve())
    best, ident = "", {}
    for p in _cargo_meta(ws_root).get("packages", []):
        d = str(Path(p["manifest_path"]).parent)
        if unit.startswith(d + "/") and len(d) > len(best):
            best = d
            ident = {"name": p["name"], "version": p["version"],
                     "edition": p.get("edition", "")}
    return ident


def module_path(ws_root, unit_path):
    """D2: path-derived module path (crate-relative; bins named as bin:<stem>)."""
    rel = Path(unit_path).resolve().relative_to(Path(ws_root).resolve())
    parts = list(rel.parts)
    if "src" in parts:
        parts = parts[parts.index("src") + 1:]
    if parts[:1] == ["bin"]:
        return "bin:" + Path(parts[-1]).stem
    parts[-1] = Path(parts[-1]).stem
    if parts[-1] in ("lib", "main", "mod"):
        parts = parts[:-1]
    return "::".join(["crate"] + parts)


def context_tags(source):
    """D3 heuristics.  `kernel` needs corpus config (not inferable from one unit);
    `general` when nothing else fires."""
    tags = []
    if re.search(r"#!\[no_std\]", source):
        tags.append("no_std")
    if re.search(r"\basync\s+fn\b|\.await\b", source):
        tags.append("async")
    if re.search(r'\bextern\s+"C"|#\[no_mangle\]', source):
        tags.append("ffi")
    if re.search(r"#\[cfg\(test\)\]|mod\s+tests\b", source):
        tags.append("test")
    unsafe_n = len(re.findall(r"\bunsafe\b", source))
    lines = max(source.count("\n"), 1)
    if unsafe_n >= 3 or (unsafe_n >= 2 and unsafe_n * 50 > lines):
        tags.append("unsafe_heavy")
    return tags or ["general"]


def _symbols(session, uri):
    res = session.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
    flat = []

    def walk(nodes):
        for s in nodes or []:
            flat.append(s)
            walk(s.get("children"))
    walk(res if isinstance(res, list) else [])
    return flat


def build(session, ws_root, unit_path, code=None):
    """D1–D7 digest for one compilation unit.  Opens (and closes) the unit as an
    overlay on the session."""
    uri, source = session.open_overlay(unit_path, code)
    try:
        syms = _symbols(session, uri)
    finally:
        session.close_overlay(uri)
    types, impls = [], []
    for s in syms:
        kind = s.get("kind")
        name = s.get("name", "")
        if kind in _TYPE_KINDS:
            types.append({"name": name, "kind": _TYPE_KINDS[kind],
                          "detail": (s.get("detail") or "")[:120]})
        elif name.startswith("impl"):
            impls.append(name[:160])
    deps = {}
    for m in _USE.finditer(source):
        tree = m.group(1).strip()
        root = re.split(r"::|\{| ", tree, 1)[0].strip()
        if root in ("crate", "super", "self", ""):
            continue
        leaves = re.findall(r"([A-Za-z_]\w*)\s*[,}]|([A-Za-z_]\w*)\s*$", tree)
        items = sorted({a or b for a, b in leaves} - {root})[:12]
        deps.setdefault(root, [])
        deps[root] = sorted(set(deps[root]) | set(items))[:12]
    return {
        "crate_ident": crate_ident(ws_root, unit_path),          # D1
        "module_path": module_path(ws_root, unit_path),          # D2
        "context_tags": context_tags(source),                    # D3
        "type_vocabulary": types[:40],                           # D4
        "trait_impls_in_scope": impls[:40],                      # D5
        "deps_of_interest": deps,                                # D6
        "feature_flags": sorted({m.group(1).strip()
                                 for m in _CFG.finditer(source)})[:20],  # D7
        "unit_gloss": None,                                      # D8 — LM, later
    }
