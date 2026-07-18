"""Phase C serving + Phase D routing (OLEUM-DST-01 §6/§8 V7).

Takes any OpenAI-compatible chat endpoint (the 96GB vLLM in production, a
stub in tests).  Per unit: fill D8 via the secondary model, run segments
consecutively (prefix + digest ride the served cache), distill with one
schema-retry then quarantine, gate every card, audit every card (V6 v1 audits
at 100% — it is cheap and deterministic; the <2% disagreement halt applies
per batch), and two-model-route T0/T1 or low-confidence cards (V7:
disagreement quarantines).

Outputs are plain dicts; the caller persists (run_units writes JSONL under
var/dst/<run>/ for the merge layer).
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import prompts, validate

REPO = Path(__file__).resolve().parent.parent.parent


class BackendUnavailable(Exception):
    pass


class ServingLM:
    """Minimal OpenAI /v1/chat/completions client with json_schema constraint."""

    def __init__(self, url, model, timeout=180.0, max_tokens=3072):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    def chat_json(self, system, user, schema, max_tokens=None):
        payload = {
            "model": self.model, "temperature": 0.2,
            "max_tokens": max_tokens or self.max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "out", "schema": schema, "strict": True}},
        }
        req = urllib.request.Request(self.url + "/v1/chat/completions",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise BackendUnavailable(str(e))
        try:
            content = data["choices"][0]["message"]["content"]
            start = content.index("{")
            return json.loads(content[start:content.rindex("}") + 1])
        except (KeyError, IndexError, ValueError, TypeError):
            return None


def _distill_once(lm, sfx, error=None):
    user = sfx if error is None else \
        sfx + "\n\nYour previous output failed validation:\n" + \
        "\n".join(error[:10]) + "\nEmit a corrected object."
    return lm.chat_json(prompts.PREFIX, user, prompts.SCHEMA)


def run_unit(primary, unit, *, secondary=None, codes=None, log=None):
    """unit: {digest, trust_tier, segments:[{segment_id, source, sibling_stub?,
    sites, op_ids?}]}.  Returns {accepted, quarantined, stats}."""
    say = log or (lambda m: None)
    codes = codes or {}
    digest = dict(unit["digest"])
    if digest.get("unit_gloss") is None and (secondary or primary):
        head = "\n".join(s["source"] for s in unit["segments"])[:4000]
        obj = (secondary or primary).chat_json(
            prompts.GLOSS_SYSTEM,
            json.dumps({k: digest[k] for k in digest if k != "unit_gloss"},
                       sort_keys=True) + "\nUNIT HEAD:\n" + head,
            prompts.GLOSS_SCHEMA, max_tokens=512)
        digest["unit_gloss"] = " ".join(str((obj or {}).get("gloss") or "").split())[:900]

    accepted, quarantined = [], []
    stats = {"segments": 0, "retries": 0, "audit_disagreements": 0,
             "routed": 0, "v7_quarantined": 0}
    routed_tier = unit.get("trust_tier") in ("T0", "T1")
    for seg in unit["segments"]:
        stats["segments"] += 1
        sfx = prompts.suffix(digest, seg)
        card = _distill_once(primary, sfx)

        def _gate(c):
            return validate.gate(c or {}, schema=prompts.SCHEMA,
                                 source=seg["source"], sites=seg.get("sites"),
                                 codes=codes)
        g = _gate(card)
        if g["quarantine"] and not validate.v2_verbatim(card or {}, seg["source"]):
            stats["retries"] += 1                       # V1 fault: one retry
            card = _distill_once(primary, sfx, error=g["errors"])
            g = _gate(card)
        if g["quarantine"]:
            quarantined.append({"segment_id": seg["segment_id"],
                                "errors": g["errors"], "card": card})
            say(f"quarantine {seg['segment_id']}: {g['errors'][:2]}")
            continue
        card = g["card"]
        dis = validate.audit(card, seg.get("op_ids") or
                             [s.get("op_id") or "" for s in seg.get("sites") or []],
                             seg["source"])
        stats["audit_disagreements"] += len(dis)

        conf = (card.get("confidence") or {}).get("strategy")
        if secondary and (routed_tier or (conf is not None and conf < 0.6)):
            stats["routed"] += 1
            second = _gate(_distill_once(secondary, sfx))
            if second["quarantine"] or validate.disagree(card, second["card"]):
                stats["v7_quarantined"] += 1
                quarantined.append({"segment_id": seg["segment_id"],
                                    "errors": ["v7 two-model disagreement"],
                                    "card": card, "second": second["card"]})
                continue
        card["contract_version"] = prompts.CONTRACT_VERSION
        accepted.append({"segment_id": seg["segment_id"], "card": card,
                         "trust_tier": unit.get("trust_tier"),
                         "stripped": g["stripped"], "audit": dis})
    return {"accepted": accepted, "quarantined": quarantined, "stats": stats}


def run_units(primary, units, *, secondary=None, codes=None, out_dir=None,
              log=None):
    """Batch driver: units sequentially (unit-consecutive by construction),
    JSONL persisted for the merge layer.  Halts if the V6 disagreement rate
    breaches 2% over the batch (§8 V6)."""
    say = log or (lambda m: None)
    out = Path(out_dir or REPO / "var" / "dst" / time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    totals = {"accepted": 0, "quarantined": 0, "segments": 0,
              "audit_disagreements": 0, "halted": False}
    with open(out / "cards.jsonl", "w") as fa, \
            open(out / "quarantine.jsonl", "w") as fq:
        for unit in units:
            res = run_unit(primary, unit, secondary=secondary, codes=codes,
                           log=log)
            for a in res["accepted"]:
                fa.write(json.dumps(a, ensure_ascii=False) + "\n")
            for q in res["quarantined"]:
                fq.write(json.dumps(q, ensure_ascii=False) + "\n")
            for k in ("segments", "audit_disagreements"):
                totals[k] += res["stats"][k]
            totals["accepted"] += len(res["accepted"])
            totals["quarantined"] += len(res["quarantined"])
            if totals["segments"] >= 50 and \
                    totals["audit_disagreements"] > 0.02 * totals["segments"]:
                totals["halted"] = True            # §8 V6: halt batch, review config
                say("V6 disagreement rate > 2% — batch halted")
                break
    totals["out"] = str(out)
    return totals
