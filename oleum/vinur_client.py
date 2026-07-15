"""Thin client for the vinur knowledge host (POST /call, optional Bearer token).

Fail-open by contract: vinur being unreachable degrades annotations to absent,
it never fails the tool call — legality is rust-analyzer's job, knowledge is
best-effort decoration (AMIGA-RUST-02 cardinal rule).
"""
import json
import urllib.error
import urllib.request


class Vinur:
    def __init__(self, url, token="", timeout=60.0):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, name, arguments):
        """-> {ok, result|error}; transport failures come back as ok=False."""
        body = json.dumps({"name": name, "arguments": arguments}).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.url + "/call", data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            return {"ok": False, "error": f"vinur unreachable: {e}"}

    def ops_annotate(self, ops, context_features=None):
        """-> parsed {annotations, graph_version, …} or None when unavailable."""
        args = {"ops": list(ops)}
        if context_features:
            args["context_features"] = context_features
        res = self.call("ops_annotate", args)
        if not res.get("ok"):
            return None
        try:
            return json.loads(res["result"])
        except (KeyError, TypeError, ValueError):
            return None

    def kb_ask(self, question, domain):
        """Domain-scoped prose lookup: naming the axis both filters to the domain
        and lifts the host-side conversational exclusion (VINUR-OPS-01 §5)."""
        return self.call("kb_ask", {"query": question,
                                    "facets": {"domain": [domain]}})
