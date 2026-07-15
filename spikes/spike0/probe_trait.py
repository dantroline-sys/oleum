"""Follow-up probe: can the accepted-form lane recover trait attribution without
completion items?  Tests full hover markdown + textDocument/declaration (does a
trait-impl method declare-jump to the trait decl, whose externalDocs then gives the
full trait defining path?)."""
import json
from pathlib import Path

import lsp_client
from run_spike import COMPLETED, WS, positions_completed

CASES = ["inherent_method", "ext_trait_method", "user_trait_method",
         "trait_method_std", "deref_method"]


def main():
    c, _ = lsp_client.start(str(WS))
    text = c.open_doc(COMPLETED)
    c.wait_quiescent()
    pos = positions_completed(text)
    uri = Path(COMPLETED).as_uri()
    for case in CASES:
        ln, ch = pos[case]
        p = {"textDocument": {"uri": uri}, "position": {"line": ln, "character": ch}}
        hov = c.request("textDocument/hover", dict(p))
        val = (hov.get("contents") or {}).get("value", "") if isinstance(hov, dict) else ""
        decl = c.request("textDocument/declaration", dict(p))
        print("=" * 20, case)
        print("hover full:", json.dumps(val)[:400])
        if isinstance(decl, list) and decl:
            t = decl[0]
            turi, rng = t.get("targetUri", ""), t.get("targetSelectionRange", {})
            print("decl:", "/".join(turi.split("/")[-3:]), rng.get("start"))
            dp = {"textDocument": {"uri": turi}, "position": rng.get("start")}
            print("decl externalDocs:", c.request("experimental/externalDocs", dp))
        else:
            print("decl:", decl)
    c.shutdown()


if __name__ == "__main__":
    main()
