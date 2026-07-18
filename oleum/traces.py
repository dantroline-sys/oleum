"""Trace capture (AMIGA-RUST-02 day-one requirement): every annotation-lane
call is recorded in var/traces.db — coverage over time (joined/requested) and
gap detection (which ops real usage hits that the kb knows nothing about) both
read from here.  The researcher lane's gap source.

Recording must never break a tool call: any failure is swallowed after a
stderr note."""
import json
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "var" / "traces.db"


def record(kind, payload):
    try:
        DB.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(DB, timeout=5)
        con.execute("CREATE TABLE IF NOT EXISTS traces(id INTEGER PRIMARY KEY, "
                    "ts REAL, kind TEXT, payload TEXT)")
        con.execute("INSERT INTO traces(ts, kind, payload) VALUES(?,?,?)",
                    (time.time(), kind, json.dumps(payload, ensure_ascii=False)))
        con.commit()
        con.close()
    except Exception as e:                      # never fail the tool call
        print(f"[oleum] trace drop ({kind}): {e}", file=sys.stderr)


def gaps(limit=50):
    """Most-hit op ids with no annotation — the researcher lane's queue."""
    if not DB.is_file():
        return []
    con = sqlite3.connect(DB)
    misses = {}
    for (payload,) in con.execute(
            "SELECT payload FROM traces WHERE kind='annotate'"):
        try:
            for op in json.loads(payload).get("bare_ops") or []:
                misses[op] = misses.get(op, 0) + 1
        except ValueError:
            continue
    con.close()
    return sorted(misses.items(), key=lambda kv: -kv[1])[:limit]
