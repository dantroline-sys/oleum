"""Phase D validation gates (OLEUM-DST-01 §8).  Pure functions, no LM, no IO —
built and regression-tested before the first distiller token is ever served.

gate() applies V1–V5 + V8 to one card and returns the cleaned card plus what
was stripped/rejected and whether the card must be quarantined.  audit() is
the V6 deterministic cross-check (failure paths + lock/clone/alloc events
against the extractor's op walk).  V7 routing lives in the orchestrator;
disagree() is its comparison heuristic.
"""
import json
import re

OP_ID = re.compile(r"^rust:op:[A-Za-z_][\w:<>\[\]&'#\-. ]*#(free|inherent|trait)$")
_IDENT_DECL = re.compile(
    r"\b(?:let(?:\s+mut)?|fn|struct|enum|trait|const|static|mod)\s+([A-Za-z_]\w*)")
_WORD = re.compile(r"[A-Za-z_]\w*")


# ── V1: closed-schema validation (subset walker, no extra keys) ───────────────
def v1_schema(obj, schema, path="$"):
    errs = []
    t = schema.get("type")
    if t == "object":
        if not isinstance(obj, dict):
            return [f"{path}: expected object"]
        props = schema.get("properties", {})
        for k in obj:
            if k not in props and schema.get("additionalProperties") is False:
                errs.append(f"{path}.{k}: unknown key")
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}.{k}: missing")
        for k, v in obj.items():
            if k in props:
                errs += v1_schema(v, props[k], f"{path}.{k}")
    elif t == "array":
        if not isinstance(obj, list):
            return [f"{path}: expected array"]
        for i, v in enumerate(obj):
            errs += v1_schema(v, schema.get("items", {}), f"{path}[{i}]")
    elif t == "string":
        if not isinstance(obj, str):
            return [f"{path}: expected string"]
        if "enum" in schema and obj not in schema["enum"]:
            errs.append(f"{path}: {obj!r} not in enum")
    elif t == "number":
        if not isinstance(obj, (int, float)) or isinstance(obj, bool):
            return [f"{path}: expected number"]
        if "minimum" in schema and obj < schema["minimum"]:
            errs.append(f"{path}: below minimum")
        if "maximum" in schema and obj > schema["maximum"]:
            errs.append(f"{path}: above maximum")
    return errs


# ── V2: verbatim quotation cap ────────────────────────────────────────────────
_ECHO_KEYS = {"decision_site", "chosen", "op_id", "candidate", "segment_id"}


def _strings(obj, skip_keys=_ECHO_KEYS):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in skip_keys:
                yield from _strings(v, skip_keys)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, skip_keys)
    elif isinstance(obj, str):
        yield obj


def v2_verbatim(card, source, cap=200):
    """Any non-echo string containing a contiguous `cap`-char run of the source
    is a violation (systemic prompt fault: reject, no retry)."""
    flat = " ".join(source.split())
    for s in _strings(card):
        f = " ".join(s.split())
        if len(f) < cap:
            continue
        step = max(1, cap // 4)
        for i in range(0, len(f) - cap + 1, step):
            if f[i:i + cap] in flat:
                return [f"verbatim run >= {cap} chars: {f[i:i + 40]!r}…"]
    return []


# ── V3: hazard existence against codes.json ───────────────────────────────────
def v3_hazards(card, codes):
    known = set(codes.get("rustc") or []) | set(codes.get("clippy") or [])
    kept, stripped = [], []
    for h in card.get("hazards") or []:
        (kept if h.get("code") in known else stripped).append(h)
    card["hazards"] = kept
    return [f"unknown code: {h.get('code')}" for h in stripped]


# ── V4: candidate closure (graph MUST NOT originate) ──────────────────────────
def _site_index(sites):
    return {s["site"]: s for s in sites or []}


def v4_closure(card, sites):
    idx = _site_index(sites)
    stripped = []
    for entry in card.get("strategy") or []:
        site = idx.get(entry.get("decision_site"))
        legal = {c.get("label") for c in (site or {}).get("candidates") or []}
        kept = []
        for alt in entry.get("rejected_alternatives") or []:
            if alt.get("candidate") in legal:
                kept.append(alt)
            else:
                stripped.append(f"{entry.get('decision_site')}: originated "
                                f"candidate {alt.get('candidate')!r}")
        entry["rejected_alternatives"] = kept
    return stripped


# ── V5: segment-local identifier leak in applicability rules ──────────────────
def local_identifiers(source):
    return {m.group(1) for m in _IDENT_DECL.finditer(source)
            if len(m.group(1)) > 2 and m.group(1) != "main"}


def v5_identifier_leak(card, source):
    locals_ = local_identifiers(source)
    kept, rejected = [], []
    for entry in card.get("strategy") or []:
        words = set(_WORD.findall(entry.get("applicability") or ""))
        leak = words & locals_
        if leak:
            rejected.append(f"{entry.get('decision_site')}: leaks {sorted(leak)}")
        else:
            kept.append(entry)
    card["strategy"] = kept
    return rejected


# ── V8: op-id echo (grammar + byte-match against the harvester) ───────────────
def v8_op_echo(card, sites):
    idx = _site_index(sites)
    kept, stripped = [], []
    for entry in card.get("strategy") or []:
        oid = entry.get("op_id") or ""
        site = idx.get(entry.get("decision_site"))
        supplied = (site or {}).get("op_id")
        ok = (oid == "unkeyed" or OP_ID.match(oid)) and \
             (supplied is None or oid == supplied)
        (kept if ok else stripped).append(
            entry if ok else f"{entry.get('decision_site')}: op_id "
                             f"{oid!r} != supplied {supplied!r}")
    card["strategy"] = kept
    return [s for s in stripped if isinstance(s, str)]


# ── V6: deterministic audit of execution claims ───────────────────────────────
_PANIC_OPS = ("::unwrap#", "::expect#", "::exit#")
_EVENT_OPS = {"lock": ("::lock#",), "clone": ("::clone#", "::to_owned#",
                                              "::to_vec#", "::to_string#"),
              "alloc": ("::new#", "::with_capacity#", "::from#", "::collect#",
                        "::to_vec#", "::to_string#", "::push#", "::insert#",
                        "::clone#", "String::", "Vec::", "Box::")}


def audit(card, op_ids, source):
    """V6 v1 scope: panic failure-path claims and lock/clone/alloc events must
    have corroboration in the extractor's op walk or the source text; and a
    segment using a panicking op must claim SOME panic path.  move/borrow are
    v2 (MIR).  Returns disagreement strings."""
    dis = []
    ex = card.get("execution") or {}
    claims_panic = any(f.get("kind") == "panic"
                       for f in ex.get("failure_paths") or [])
    has_panic_op = any(p in o for o in op_ids for p in _PANIC_OPS) \
        or re.search(r"panic!|\[[^\]]+\]\s*(?:;|\))|unwrap\(|expect\(", source)
    if has_panic_op and not claims_panic \
            and "execution" not in (card.get("abstained_fields") or []):
        dis.append("segment has a panicking op but no panic failure_path claimed")
    for ev in ex.get("ownership_events") or []:
        kind = ev.get("kind")
        if kind not in _EVENT_OPS:
            continue                                  # move/borrow: not audited in v1
        needles = _EVENT_OPS[kind]
        corroborated = any(n in o for o in op_ids for n in needles) \
            or any(n.strip(":#") in source for n in needles)
        if not corroborated:
            dis.append(f"{kind} event at {ev.get('at')!r} has no corroborating op")
    return dis


# ── V7 comparison heuristic (routing lives in the orchestrator) ───────────────
def disagree(a, b):
    """Two-model disagreement on purpose/strategy — the quarantine trigger."""
    ca = (a.get("confidence") or {}).get("strategy") or 0
    cb = (b.get("confidence") or {}).get("strategy") or 0
    if abs(ca - cb) > 0.4:
        return True
    regs_a = {(s.get("decision_site"), s.get("register"))
              for s in a.get("strategy") or []}
    regs_b = {s.get("decision_site"): s.get("register")
              for s in b.get("strategy") or []}
    for site, reg in regs_a:
        if site in regs_b and regs_b[site] != reg:
            return True
    return False


def gate(card, *, schema, source, sites, codes):
    """V1–V5 + V8 in contract order.  Returns
    {card, quarantine: bool, errors, stripped} — V1/V2 failures quarantine
    (V1 after the orchestrator's single retry), strip gates only log."""
    v1 = v1_schema(card, schema)
    if v1:
        return {"card": card, "quarantine": True, "errors": v1, "stripped": []}
    v2 = v2_verbatim(card, source)
    if v2:
        return {"card": card, "quarantine": True, "errors": v2, "stripped": []}
    stripped = []
    stripped += v3_hazards(card, codes)
    stripped += v4_closure(card, sites)
    stripped += v5_identifier_leak(card, source)
    stripped += v8_op_echo(card, sites)
    return {"card": card, "quarantine": False, "errors": [], "stripped": stripped}
