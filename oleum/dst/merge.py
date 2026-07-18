"""Phase D merge (OLEUM-DST-01 §9): accepted cards → the rust-learned bundle.

Producer-side (imports vinur as a library, like producers/build_pack.py).
Maintains a persistent learned kb under producers/learned/ that accumulates
across corpus runs, then exports dist/rust-learned.kdb via vinur's bundle
closure.

- A strategy entry attaches to the op node named by its op_id (node created
  inert if absent).  "unkeyed" entries and segment-level hazards land in
  observations.jsonl — visible to review tooling, never served.
- Dedup: content hash over (op_id, folded applicability, register, sorted
  conditioning tags), stored as card_hash.  A hit increments observed_count /
  refreshes last_observed and appends provenance — but only for segments not
  already in the card's support, so re-running the same corpus is a no-op.
"""
import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
BUNDLE = "rust-learned"
LEARNED_DIR = REPO / "producers" / "learned"


def _fold(s):
    return " ".join(str(s or "").split()).lower()


def _rule_hash(op_id, entry, tags):
    basis = json.dumps([op_id, _fold(entry.get("applicability")),
                        entry.get("register") or "", sorted(tags or [])])
    return hashlib.sha256(basis.encode()).hexdigest()


def _kb(vinur_repo, kb_dir):
    if str(vinur_repo) not in sys.path:
        sys.path.insert(0, str(vinur_repo))
    from knowledgehost import config as khconfig
    from knowledgehost.kb import KB
    kb_dir = Path(kb_dir)
    kb_dir.mkdir(parents=True, exist_ok=True)
    cfg = dict(khconfig.DEFAULTS)
    cfg["kb_path"] = str(kb_dir / "kb.db")
    cfg["bundle_dir"] = str(kb_dir / "bundles")
    return KB(cfg), cfg


def merge_run(cards, corpus_doc, *, vinur_repo="/home/user/vinur",
              kb_dir=None, trust=0.5, log=None):
    """cards: iterable of accepted records ({segment_id, card, trust_tier}) or a
    cards.jsonl path.  corpus_doc identifies the corpus snapshot (e.g.
    'dst:serde@1.0.200').  Returns stats."""
    say = log or (lambda m: None)
    if isinstance(cards, (str, Path)):
        cards = [json.loads(line) for line in
                 Path(cards).read_text().splitlines() if line.strip()]
    kb, cfg = _kb(vinur_repo, kb_dir or LEARNED_DIR)
    kb.db.execute("INSERT OR IGNORE INTO source_registry(doc_id,title,source_type,"
                  "trust_weight,regime,status,bundle) VALUES(?,?,?,?,?,?,?)",
                  (corpus_doc, corpus_doc, "distilled", trust, "empirical",
                   "active", BUNDLE))
    now = time.time()
    stats = {"cards_in": 0, "new": 0, "reinforced": 0, "unattached": 0,
             "unchanged": 0}
    observations = []
    for rec in cards:
        stats["cards_in"] += 1
        card = rec["card"]
        seg = rec.get("segment_id") or ""
        tags = (card.get("regime") or {}).get("conditioning_tags") or []
        for h in card.get("hazards") or []:
            observations.append({"kind": "hazard", "segment": seg,
                                 "corpus": corpus_doc, **h})
        for entry in card.get("strategy") or []:
            op_id = entry.get("op_id") or "unkeyed"
            if op_id == "unkeyed" or _fold(entry.get("applicability")) in ("", "abstain"):
                stats["unattached"] += 1
                observations.append({"kind": "strategy", "segment": seg,
                                     "corpus": corpus_doc, **entry})
                continue
            kb.db.execute("INSERT OR IGNORE INTO nodes(id,label,kind,summary,"
                          "aliases,support,status) VALUES(?,?,?,?,?,?,?)",
                          (op_id, op_id.split("::")[-1].split("#")[0], "fn", "",
                           "[]", json.dumps([{"doc_id": corpus_doc}]), "active"))
            h = _rule_hash(op_id, entry, tags)
            row = kb.db.execute("SELECT id, support, observed_count FROM "
                                "procedure_cards WHERE card_hash=? AND node_id=? "
                                "AND status='active'", (h, op_id)).fetchone()
            support_entry = {"doc_id": corpus_doc, "segment": seg}
            if row:
                sup = json.loads(row["support"] or "[]")
                if any(e.get("segment") == seg and e.get("doc_id") == corpus_doc
                       for e in sup):
                    stats["unchanged"] += 1           # same corpus re-run: no-op
                    continue
                sup.append(support_entry)
                kb.db.execute("UPDATE procedure_cards SET support=?, "
                              "observed_count=?, last_observed=?, updated_at=? "
                              "WHERE id=?",
                              (json.dumps(sup), (row["observed_count"] or 1) + 1,
                               now, now, row["id"]))
                stats["reinforced"] += 1
            else:
                criteria = {"applicability": entry.get("applicability"),
                            "register": entry.get("register"),
                            "pattern": entry.get("pattern"),
                            "conditioning_tags": tags,
                            "traded_away": [a.get("traded_away") for a in
                                            entry.get("rejected_alternatives")
                                            or []][:6],
                            "trust_tier": rec.get("trust_tier")}
                kb.db.execute(
                    "INSERT INTO procedure_cards(id,node_id,title,card_type,"
                    "criteria,support,status,card_hash,observed_count,"
                    "last_observed,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"rust:learned:card:{h[:16]}", op_id,
                     (entry.get("applicability") or "")[:160], "strategy",
                     json.dumps(criteria, ensure_ascii=False),
                     json.dumps([support_entry]), "active", h, 1, now, now, now))
                stats["new"] += 1
    kb.db.commit()
    if observations:
        obs = Path(kb_dir or LEARNED_DIR) / "observations.jsonl"
        with open(obs, "a") as f:
            for o in observations:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")
    kb.close()
    say(f"merge: {stats}")
    return stats


def export_pack(*, vinur_repo="/home/user/vinur", kb_dir=None, out_dir=None,
                log=None):
    """dist/rust-learned.kdb from the persistent learned kb."""
    say = log or (lambda m: None)
    kb, cfg = _kb(vinur_repo, kb_dir or LEARNED_DIR)
    kb.close()
    from knowledgehost import bundles
    out = Path(out_dir or REPO / "dist")
    out.mkdir(parents=True, exist_ok=True)
    res = bundles.split(cfg, str(out), only={BUNDLE}, force=True,
                        log_fn=lambda m: None)
    f = res.get(BUNDLE, {}).get("file")
    say(f"pack: {f}")
    return f
