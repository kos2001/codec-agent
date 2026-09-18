"""Knowledge base of solved failures — past rejections and the edit that fixed them.

Adapted from lsi_error_analyzer (`src/knowledge_store.py`, `src/retrievers.py`): resolved
cases are the asset, retrieval is BM25 + entity overlap fused with RRF, weak matches are
dropped by a coverage gate, and the store is a committed JSON so the knowledge is
versioned, reviewable and shared rather than living in someone's cache.

Here a "resolved case" is mined from `runs/*.json`, which already records every rejected
attempt, the gate's evidence for it, and the next attempt that passed:

  symptom     the evidence the gate (or the testbench judge) produced
  root_cause  the failing stage / fault class
  resolution  what actually changed in the attempt that was accepted (diff summary)

Curated rules from CLAUDE.md ("Rules learned the hard way") are loaded as cases too: they
are the same knowledge, written by hand before the loop existed.

    python pipeline/kb.py build     # (re)mine runs/ + CLAUDE.md -> data/kb.json
    python pipeline/kb.py query "lint WIDTHEXPAND ..."
    python pipeline/kb.py eval      # leave-one-out retrieval accuracy
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
import gate  # noqa: E402

STORE = gate.ROOT / "data" / "kb.json"
SCHEMA_VERSION = 1

#: Identifiers that carry meaning in this domain; overlap with the query is a strong signal
#: even when the wording differs (the lsi_error_analyzer "entity graph" idea).
ENTITY_PATTERNS = (
    r"REQ-[A-Z]+-\d+",                       # requirements
    r"\b(?:lint|synthesize|simulate|formal|equiv)\b",
    r"%?Warning-[A-Z]+|%Error[-A-Za-z]*",    # verilator diagnostics
    r"\bA\d_[a-z_]+\b",                      # formal property names
    r"\b(?:L1|L2|L3|P)_[a-z0-9_]+\b",        # gate test names
    r"\b(?:in_valid|in_ready|out_valid|out_ready|out_code|out_pcm|in_pcm|in_code|rst_n|clk)\b",
    r"\balways_ff\b|\balways_comb\b|\bnegedge\b|\bposedge\b|\bcasez\b",
    r"\$(?:urandom|display|finish|dumpvars|readmemh|time)\b",
    r"\b(?:async|asynchronous|synchronous)\b",
    r"\b(?:stuck-at|dropped-update|reset-style|slice-shift|mux-swap|constant|inversion)\b",
    r"\bg711_[a-z]+_[a-z]+\b",
)


def _tokenize(s: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9_$%\-]+", (s or "").lower())


def entities(text: str) -> List[str]:
    out = set()
    for pat in ENTITY_PATTERNS:
        for m in re.findall(pat, text or "", re.I):
            out.add((m if isinstance(m, str) else m[0]).lower())
    return sorted(out)


@dataclass
class Case:
    key: str
    kind: str                 # rtl | testbench | rule
    module: str
    stage: str                # failing stage, or fault class for a testbench
    symptom: str
    root_cause: str
    resolution: str
    source: str
    entities: List[str] = field(default_factory=list)

    def text(self) -> str:
        return f"{self.stage}\n{self.symptom}\n{self.root_cause}\n{self.resolution}"

    def to_dict(self) -> dict:
        d = {**self.__dict__}
        d["content_hash"] = hashlib.sha1(self.text().encode()).hexdigest()[:12]
        return d

    def render(self) -> str:
        return (f"### {self.key} ({self.kind}, {self.module}, {self.stage})\n"
                f"SYMPTOM: {self.symptom.strip()[:600]}\n"
                f"CAUSE:   {self.root_cause.strip()[:400]}\n"
                f"FIX:     {self.resolution.strip()[:600]}\n"
                f"SOURCE:  {self.source}")


# ------------------------------------------------------------------- mining ---

def _diff_summary(before: str, after: str, limit: int = 12) -> str:
    """The lines that changed between a rejected attempt and the accepted one."""
    d = [l for l in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
         if l and l[0] in "+-" and not l.startswith(("+++", "---"))]
    head = d[:limit]
    return "\n".join(head) + (f"\n… {len(d) - limit} more changed lines" if len(d) > limit else "")


def _read(p: Optional[str]) -> str:
    if not p:
        return ""
    f = gate.ROOT / p
    return f.read_text() if f.exists() else ""


def mine_runs() -> List[Case]:
    cases: List[Case] = []
    for run in sorted(gate.RUNS.glob("*-generate.json")):
        d = json.loads(run.read_text())
        attempts = d.get("attempts", [])
        tb = d.get("kind") == "testbench"
        for i, a in enumerate(attempts):
            failed = (a.get("verdict") not in ("PROVEN", "ACCEPTED")) or a.get("error")
            if not failed:
                continue
            nxt = attempts[i + 1] if i + 1 < len(attempts) else None
            if not nxt or nxt.get("verdict") not in ("PROVEN", "ACCEPTED"):
                continue          # only cases whose fix is known are knowledge
            before = _read(a.get("candidate") or a.get("testbench"))
            after = _read(nxt.get("candidate") or nxt.get("testbench"))
            fix = _diff_summary(before, after) if before and after else "(candidate files no longer on disk)"
            stage = (a.get("error") and "api")
            if not stage:
                stage = next((s for s in gate.GATING if (a.get("stages") or {}).get(s) == "fail"),
                             a.get("verdict", "unknown").lower())
            symptom = (a.get("evidence") or a.get("error") or
                       json.dumps(a.get("details", {}))[:400]).strip()
            cases.append(Case(
                key=f"{d['run_id']}-iter{a['iteration']}", kind="testbench" if tb else "rtl",
                module=d["module"], stage=stage, symptom=symptom,
                root_cause=f"{'testbench' if tb else 'RTL'} rejected at `{stage}`",
                resolution=fix, source=f"runs/{run.name}",
                entities=entities(symptom + " " + fix)))
    return cases


def mine_rules() -> List[Case]:
    """The hand-written rules in CLAUDE.md: each bullet is symptom + fix already."""
    text = (gate.ROOT / "CLAUDE.md").read_text()
    body = text.split("## Rules learned the hard way", 1)
    if len(body) < 2:
        return []
    section = body[1].split("\n## ", 1)[0]
    out = []
    for n, bullet in enumerate(re.split(r"\n- ", section)):
        b = bullet.strip(" -\n")
        if len(b) < 40:
            continue
        title = re.sub(r"\*\*", "", b.split(".")[0])[:90]
        out.append(Case(key=f"rule-{n}", kind="rule", module="*", stage="rule",
                        symptom=title, root_cause="known pitfall in this repository",
                        resolution=re.sub(r"\s+", " ", b), source="CLAUDE.md",
                        entities=entities(b)))
    return out


def build(write: bool = True) -> dict:
    cases = mine_rules() + mine_runs()
    seen, uniq = set(), []
    for c in cases:                                     # dedupe by content
        h = hashlib.sha1(c.text().encode()).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        uniq.append(c)
    env = {"schema_version": SCHEMA_VERSION, "cases": [c.to_dict() for c in uniq]}
    if write:
        STORE.parent.mkdir(exist_ok=True)
        STORE.write_text(json.dumps(env, indent=2, ensure_ascii=False))
    return env


def load() -> List[Case]:
    if not STORE.exists():
        return []
    return [Case(**{k: v for k, v in c.items() if k != "content_hash"})
            for c in json.loads(STORE.read_text())["cases"]]


# ---------------------------------------------------------------- retrieval ---

class BM25:
    """Okapi BM25, stdlib only (rank_bm25 is not a dependency of this repo)."""

    def __init__(self, docs: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b, self.docs = k1, b, docs
        self.n = len(docs) or 1
        self.avglen = sum(len(d) for d in docs) / self.n
        self.tf = [Counter(d) for d in docs]
        df: Counter = Counter()
        for d in docs:
            df.update(set(d))
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: List[str]) -> List[float]:
        out = []
        for tf, doc in zip(self.tf, self.docs):
            s = 0.0
            for t in query:
                f = tf.get(t)
                if not f:
                    continue
                s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * len(doc) / self.avglen))
            out.append(s)
        return out


@dataclass
class Hit:
    case: Case
    bm25: float
    overlap: int
    rrf: float


class KB:
    """BM25 + entity overlap, fused with reciprocal rank fusion, gated on coverage."""

    def __init__(self, cases: Optional[List[Case]] = None):
        self.cases = cases if cases is not None else load()
        self.bm25 = BM25([_tokenize(c.text()) for c in self.cases])

    def search(self, query: str, k: int = 3, *, min_bm25: float = 1.0,
               rrf_k: int = 60) -> List[Hit]:
        if not self.cases:
            return []
        q_tokens, q_ents = _tokenize(query), set(entities(query))
        bm = self.bm25.scores(q_tokens)
        ov = [len(q_ents & set(c.entities)) for c in self.cases]
        rank_bm = {i: r for r, i in enumerate(sorted(range(len(bm)), key=lambda i: -bm[i]))}
        rank_ov = {i: r for r, i in enumerate(sorted(range(len(ov)), key=lambda i: -ov[i]))}
        fused = [(i, 1 / (rrf_k + rank_bm[i]) + 1 / (rrf_k + rank_ov[i])) for i in range(len(self.cases))]
        fused.sort(key=lambda x: -x[1])
        hits = []
        for i, score in fused[:k]:
            # Coverage gate (lsi_error_analyzer): a weak match is worse than no context,
            # because the model treats whatever it is given as relevant.
            if bm[i] < min_bm25 and ov[i] == 0:
                continue
            hits.append(Hit(self.cases[i], round(bm[i], 2), ov[i], round(score, 5)))
        return hits

    def context(self, query: str, k: int = 3, budget: int = 2500) -> Tuple[str, List[str]]:
        """Prompt-ready context plus the keys that produced it (for citations)."""
        out, keys, used = [], [], 0
        for h in self.search(query, k):
            block = h.case.render()
            if used + len(block) > budget:
                break
            out.append(block)
            keys.append(h.case.key)
            used += len(block)
        if not out:
            return "", []
        return ("PREVIOUSLY SOLVED FAILURES FROM THIS PROJECT (most similar first; the FIX is what "
                "made the next attempt pass):\n\n" + "\n\n".join(out)), keys


# --------------------------------------------------------------- evaluation ---

def evaluate(k: int = 3) -> dict:
    """Leave-one-out retrieval accuracy over the mined cases.

    A hit is correct when the retrieved case has the same failing stage / fault class as the
    held-out one: that is what makes its FIX applicable. Rule cases have no stage label, so
    they are queries only when they share a stage with a mined case.
    """
    cases = load()
    labelled = [c for c in cases if c.stage not in ("rule", "unknown")]
    stage_n = Counter(c.stage for c in labelled)
    # A leave-one-out query is only answerable when another case shares its label; with a
    # single case per stage P@1 is bounded by 0 and the average says nothing about retrieval.
    answerable = [c for c in labelled if stage_n[c.stage] >= 2]
    if len(answerable) < 2:
        return {"cases": len(cases), "labelled": len(labelled), "answerable": len(answerable),
                "note": "too few cases share a label for a leave-one-out score",
                "singleton_stages": [s for s, n in stage_n.items() if n == 1]}
    p1 = p3 = mrr = gated = 0
    per_stage: Dict[str, List[int]] = {}
    for held in answerable:
        rest = [c for c in cases if c.key != held.key]
        hits = KB(rest).search(held.symptom, k=k)
        if not hits:
            gated += 1
            per_stage.setdefault(held.stage, []).append(0)
            continue
        ranks = [i for i, h in enumerate(hits) if h.case.stage == held.stage]
        first = ranks[0] if ranks else None
        p1 += int(first == 0)
        p3 += int(first is not None)
        mrr += 1 / (first + 1) if first is not None else 0.0
        per_stage.setdefault(held.stage, []).append(int(first == 0))
    n = len(answerable)
    return {"cases": len(cases), "labelled": len(labelled), "answerable": n, "k": k,
            "singleton_stages": [s for s, c in stage_n.items() if c == 1],
            "P@1": round(p1 / n, 3), f"P@{k}": round(p3 / n, 3), "MRR": round(mrr / n, 3),
            "gated_out": gated,
            "per_stage_P@1": {s: round(sum(v) / len(v), 3) for s, v in sorted(per_stage.items())}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    q = sub.add_parser("query"); q.add_argument("text"); q.add_argument("-k", type=int, default=3)
    e = sub.add_parser("eval"); e.add_argument("-k", type=int, default=3)
    sub.add_parser("stats")
    a = ap.parse_args()
    if a.cmd == "build":
        env = build()
        by = Counter(f"{c['kind']}/{c['stage']}" for c in env["cases"])
        print(json.dumps({"cases": len(env["cases"]), "by_kind_stage": dict(by), "store": str(STORE.relative_to(gate.ROOT))}, indent=2))
    elif a.cmd == "query":
        for h in KB().search(a.text, k=a.k):
            print(f"[bm25={h.bm25} overlap={h.overlap} rrf={h.rrf}]\n{h.case.render()}\n")
    elif a.cmd == "eval":
        print(json.dumps(evaluate(a.k), indent=2, ensure_ascii=False))
    else:
        cs = load()
        print(json.dumps({"cases": len(cs), "by_kind": dict(Counter(c.kind for c in cs)),
                          "by_stage": dict(Counter(c.stage for c in cs))}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
