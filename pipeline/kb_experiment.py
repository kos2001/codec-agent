"""Does the knowledge base help? An A/B repair experiment.

Claiming a KB "helps" because the retrieved text looks relevant is exactly the kind of
argument this repository exists to refuse. So: replay real failures.

Each fixture is an archived rejected attempt (`runs/*.json`): its code and the evidence the
gate produced. Both arms send the same repair request to the same model; arm B additionally
carries KB context. The candidate is then judged by the tools — `gate.prove` for RTL,
`generate_tb.check` for a testbench. Score = fraction repaired.

Leakage control: the fixture's own run is excluded from the KB, so a hit can only come from
a *different* failure. Without that the KB would hand back the exact diff that fixed it.

  python pipeline/kb_experiment.py --list
  python pipeline/kb_experiment.py --arms both --repeats 1
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent))
import gate  # noqa: E402
import generate as gen  # noqa: E402
import generate_tb as gtb  # noqa: E402
import kb as kbmod  # noqa: E402
from hermes_client import HermesClient, HermesError, upstream_model  # noqa: E402

BUILD = gate.ROOT / "build" / "kb_experiment"


def fixtures() -> List[dict]:
    """Archived rejected attempts whose source file is still on disk."""
    out = []
    for run in sorted(gate.RUNS.glob("*-generate.json")):
        d = json.loads(run.read_text())
        tb = d.get("kind") == "testbench"
        for a in d.get("attempts", []):
            if a.get("verdict") in ("PROVEN", "ACCEPTED") or a.get("error"):
                continue
            path = a.get("candidate") or a.get("testbench")
            if not path or not (gate.ROOT / path).exists() or not a.get("evidence"):
                continue
            out.append({"id": f"{d['run_id']}-iter{a['iteration']}", "run_id": d["run_id"],
                        "kind": "testbench" if tb else "rtl", "module": d["module"],
                        "path": path, "evidence": a["evidence"], "verdict": a["verdict"],
                        "stage": next((s for s in gate.GATING if (a.get("stages") or {}).get(s) == "fail"),
                                      a.get("verdict", "").lower())})
    return out


def _kb_without(run_id: str) -> kbmod.KB:
    cases = [c for c in kbmod.load() if run_id not in c.key]
    return kbmod.KB(cases)


def run_one(fx: dict, arm: str, client: HermesClient, work: Path) -> dict:
    code = (gate.ROOT / fx["path"]).read_text()
    if fx["kind"] == "rtl":
        prompt = gen.build_repair_prompt(fx["module"], code, fx["evidence"], 1)
    else:
        prompt = gtb.build_repair_prompt(fx["module"], code, fx["evidence"], 1)
    cites: List[str] = []
    if arm == "kb":
        ctx, cites = _kb_without(fx["run_id"]).context(fx["evidence"], k=3)
        if ctx:
            prompt = f"{ctx}\n\n---\n\n{prompt}"
    t0 = time.time()
    try:
        reply = client.chat(prompt)
    except HermesError as exc:
        return {"arm": arm, "id": fx["id"], "error": str(exc), "repaired": False, "citations": cites}
    took = time.time() - t0

    work.mkdir(parents=True, exist_ok=True)
    if fx["kind"] == "rtl":
        new = gen.extract_module(reply.content, fx["module"])
        if not new:
            return {"arm": arm, "id": fx["id"], "repaired": False, "reason": "no module in reply",
                    "citations": cites, "llm_s": round(took, 1), "usage": reply.usage}
        cand = work / "rtl"
        cand.mkdir(exist_ok=True)
        for m in gate.MODULES:
            shutil.copy(gate.BASELINE_RTL / f"{m}.sv", cand)
        (cand / f"{fx['module']}.sv").write_text(new)
        proof = gate.prove(cand, name=f"kbx-{arm}-{fx['id']}", stop_on_fail=False,
                           build_root=work / "gate", archive=False)
        ok = proof.verdict == "PROVEN"
        detail = {s: proof.stages.get(s) for s in gate.GATING}
    else:
        new = gtb.extract_tb(reply.content, fx["module"])
        if not new:
            return {"arm": arm, "id": fx["id"], "repaired": False, "reason": "no testbench in reply",
                    "citations": cites, "llm_s": round(took, 1), "usage": reply.usage}
        tbf = work / f"tb_{fx['module']}.sv"
        tbf.write_text(new)
        res = gtb.check(tbf, fx["module"], work / "check", 90.0)
        ok = res["verdict"] == "ACCEPTED"
        detail = {"verdict": res["verdict"], "sound": res["sound"],
                  "kill_pct": (res.get("kill") or {}).get("kill_pct")}
    shutil.rmtree(work / "gate", ignore_errors=True)
    return {"arm": arm, "id": fx["id"], "kind": fx["kind"], "module": fx["module"],
            "stage": fx["stage"], "repaired": ok, "detail": detail, "citations": cites,
            "llm_s": round(took, 1), "usage": reply.usage}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--arms", choices=["kb", "nokb", "both"], default="both")
    ap.add_argument("--kind", choices=["rtl", "testbench"])
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--one-per-run", action="store_true",
                    help="keep the first fixture of each run: independent repair tasks")
    a = ap.parse_args()

    fx = [f for f in fixtures() if not a.kind or f["kind"] == a.kind]
    if a.one_per_run:
        seen, keep = set(), []
        for f in fx:
            if f["run_id"] in seen:
                continue
            seen.add(f["run_id"]); keep.append(f)
        fx = keep
    if a.limit:
        fx = fx[:a.limit]
    if a.list:
        for f in fx:
            print(f"{f['id']:48s} {f['kind']:9s} {f['stage']:10s} {f['module']}")
        return 0

    arms = ["nokb", "kb"] if a.arms == "both" else [a.arms]
    client = HermesClient(name="kb-experiment")
    rows: List[dict] = []
    for rep in range(a.repeats):
        for f in fx:
            for arm in arms:
                work = BUILD / f"{arm}-{f['id']}-r{rep}"
                shutil.rmtree(work, ignore_errors=True)
                r = run_one(f, arm, client, work)
                r["repeat"] = rep
                rows.append(r)
                print(f"  {arm:5s} {f['id'][:44]:44s} repaired={r['repaired']} "
                      f"cites={len(r.get('citations') or [])} {r.get('reason', '')}", flush=True)
                shutil.rmtree(work, ignore_errors=True)

    summary = {}
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        summary[arm] = {"n": len(rs), "repaired": sum(r["repaired"] for r in rs),
                        "rate": round(sum(r["repaired"] for r in rs) / len(rs), 3) if rs else None,
                        "with_citations": sum(bool(r.get("citations")) for r in rs)}
    out = {"model": upstream_model(), "fixtures": len(fx), "repeats": a.repeats,
           "summary": summary, "rows": rows,
           "note": "Each fixture's own run is excluded from the KB, so any hit comes from a "
                   "different failure. Judged by gate.prove / generate_tb.check, not by the model."}
    gate.RUNS.mkdir(exist_ok=True)
    p = gate.RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-kb-experiment.json"
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2))
    print(f"report: {p.relative_to(gate.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
