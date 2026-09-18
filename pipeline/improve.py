"""Self-improvement loop: measure → diagnose → act → re-measure → record.

Adapted from lsi_error_analyzer `src/self_improve.py` (L1 measurement/diagnosis with a
committed history and drift) and hls-rtl `api/improve.py::audit` (rank what evidence is
MISSING, not what passed).

Two levels, deliberately separated:

  L1  `snapshot()` / `recommend()` — read-only. Collects what the tools already know
      (gate verdict, L0 tests, mutation detection matrix, generated-artifact quality, KB
      retrieval, KB A/B experiment) and ranks the gaps. Safe to run on a timer.
  L2  `act()` — runs the deterministic actions that close a gap (rebuild the KB, re-run the
      mutation campaign, regenerate a weak testbench…), then re-measures and applies a
      REGRESSION GATE: an action that makes a headline metric worse is reported as a
      regression, never as an improvement.

Nothing here decides correctness: every metric comes from `gate.py`, `mutate.py`,
`generate_tb.py`, `kb.py` or pytest. The LLM is only involved when an action it already
owns (regeneration) is selected.

  python pipeline/improve.py snapshot
  python pipeline/improve.py report            # snapshot + drift + ranked recommendations
  python pipeline/improve.py act --id kb.stale
  python pipeline/improve.py loop --max 3      # act on the top recommendation, re-measure
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
import gate  # noqa: E402
import generate as gen  # noqa: E402
import generate_tb as gtb  # noqa: E402
import kb as kbmod  # noqa: E402

HISTORY = gate.ROOT / "data" / "improve_history.json"
REPORTS = gate.ROOT / "claudedocs" / "self_improve"
PY = str(gate.ROOT / ".venv" / "bin" / "python") if (gate.ROOT / ".venv/bin/python").exists() else sys.executable


def _safe(fn: Callable, default=None):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — a broken probe must not hide the rest
        return {"_error": f"{type(exc).__name__}: {exc}"[:160]} if default is None else default


def _latest(pattern: str) -> Optional[dict]:
    files = sorted(gate.RUNS.glob(pattern))
    return json.loads(files[-1].read_text()) | {"_file": files[-1].name} if files else None


# --------------------------------------------------------------- L1: measure ---

def _artifacts() -> dict:
    """Latest generation outcome per module, for RTL and for testbenches."""
    rtl: Dict[str, dict] = {}
    tb: Dict[str, dict] = {}
    for p in sorted(gate.RUNS.glob("*-generate.json")):
        d = json.loads(p.read_text())
        rec = {"run": d["run_id"], "outcome": d["outcome"], "iterations": d["iterations"]}
        if d.get("kind") == "testbench":
            rec |= {"kill_pct": d.get("kill_pct"), "blind_classes": d.get("blind_classes") or []}
            tb[d["module"]] = rec
        else:
            rec |= {"equiv": d.get("equivalent_to_baseline"), "regate": (d.get("regate") or {}).get("verdict")}
            rtl[d["module"]] = rec
    return {"rtl": rtl, "testbench": tb}


def snapshot() -> dict:
    proof = _safe(lambda: gate.prove(stop_on_fail=False, archive=False).to_dict())
    run = subprocess.run([PY, "-m", "pytest", "tests", "-q", "--tb=no"], cwd=gate.ROOT,
                         capture_output=True, text=True, timeout=600)
    last = (run.stdout.strip().splitlines() or [""])[-1]
    tests = {"ok": run.returncode == 0, "summary": last}

    mut = _latest("*-mutate.json") or {}
    kbx = _latest("*-kb-experiment.json") or {}
    kb_cases = _safe(lambda: len(kbmod.load()), 0)
    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "gate": {"verdict": proof.get("verdict"), "stages": proof.get("stages"),
                 "tests_pass": sum(1 for v in (proof.get("tests") or {}).values() if v.startswith("RESULT PASS")),
                 "tests_total": len(proof.get("tests") or {})},
        "l0": tests,
        "mutation": {"file": mut.get("_file"), "total": mut.get("total"), "caught": mut.get("caught"),
                     "survived": mut.get("survived"), "score_pct": mut.get("score_pct"),
                     "sole_detector": sum((mut.get("caught_only_by_stage") or {}).values()),
                     "caught_only_by_stage": mut.get("caught_only_by_stage")},
        "artifacts": _artifacts(),
        "kb": {"cases": kb_cases, "eval": _safe(kbmod.evaluate, {}),
               "experiment": {"file": kbx.get("_file"), "summary": kbx.get("summary")}},
    }


#: Headline metrics guarded by the regression gate (higher is better unless noted).
def key_metrics(snap: dict) -> dict:
    tb = snap["artifacts"]["testbench"]
    return {
        "gate_proven": int(snap["gate"]["verdict"] == "PROVEN"),
        "l0_ok": int(bool(snap["l0"].get("ok"))),
        "mutation_score": snap["mutation"].get("score_pct") or 0,
        "sole_detector_neg": -(snap["mutation"].get("sole_detector") or 0),   # fewer is better
        "tb_min_kill": min([t.get("kill_pct") or 0 for t in tb.values()], default=0),
        "tb_blind_neg": -sum(len(t.get("blind_classes") or []) for t in tb.values()),
        "rtl_proven": sum(1 for r in snap["artifacts"]["rtl"].values()
                          if r["outcome"] == "PROVEN" and r.get("regate") != "REJECTED"),
        "kb_cases": snap["kb"].get("cases") or 0,
    }


# ------------------------------------------------------------- L1: diagnose ---

def recommend(snap: dict) -> List[dict]:
    """Ranked gaps. Each carries the measurement behind it and the action that closes it."""
    out: List[dict] = []
    g = snap["gate"]
    if g.get("verdict") != "PROVEN":
        out.append({"id": "gate.not_proven", "priority": 1, "action": "gate",
                    "why": f"the committed RTL is {g.get('verdict')}: {g.get('stages')}"})
    for stage, st in (g.get("stages") or {}).items():
        if st == "skip":
            out.append({"id": f"gate.skip.{stage}", "priority": 1, "action": None,
                        "why": f"stage `{stage}` skipped — missing evidence, not a pass (install the tool)"})
    if not snap["l0"].get("ok"):
        out.append({"id": "l0.failing", "priority": 1, "action": "tests",
                    "why": f"L0 oracle tests: {snap['l0'].get('summary')}"})

    m = snap["mutation"]
    if (m.get("survived") or 0) > 0:
        out.append({"id": "mutation.survivors", "priority": 1, "action": "mutate",
                    "why": f"{m['survived']} mutant(s) survive the gate: a wrong design it accepts"})
    if (m.get("sole_detector") or 0) > 0:
        out.append({"id": "mutation.sole_detector", "priority": 2, "action": None,
                    "why": f"{m['sole_detector']} fault(s) caught by exactly one stage "
                           f"({m.get('caught_only_by_stage')}): detection depends on that stage alone"})

    for mod, t in snap["artifacts"]["testbench"].items():
        if t.get("blind_classes"):
            out.append({"id": f"tb.blind.{mod}", "priority": 2, "action": f"regen_tb:{mod}",
                        "why": f"generated testbench for {mod} is blind to {t['blind_classes']}"})
        elif (t.get("kill_pct") or 0) < 100:
            out.append({"id": f"tb.kill.{mod}", "priority": 3, "action": f"regen_tb:{mod}",
                        "why": f"generated testbench for {mod} kills {t['kill_pct']}% of mutants"})
    for mod in gate.MODULES:
        if mod not in snap["artifacts"]["testbench"]:
            out.append({"id": f"tb.missing.{mod}", "priority": 2, "action": f"regen_tb:{mod}",
                        "why": f"no generated testbench for {mod}"})
        r = snap["artifacts"]["rtl"].get(mod)
        if not r:
            out.append({"id": f"rtl.missing.{mod}", "priority": 3, "action": f"regen_rtl:{mod}",
                        "why": f"no generated RTL for {mod}"})
        elif r["outcome"] != "PROVEN" or r.get("regate") == "REJECTED":
            out.append({"id": f"rtl.rejected.{mod}", "priority": 2, "action": f"regen_rtl:{mod}",
                        "why": f"latest generated RTL for {mod}: {r['outcome']}"
                               + (f" (regate {r['regate']})" if r.get("regate") else "")})

    kb = snap["kb"]
    mined = len(kbmod.build(write=False)["cases"])
    if mined != kb.get("cases"):
        out.append({"id": "kb.stale", "priority": 3, "action": "kb",
                    "why": f"KB holds {kb.get('cases')} cases but {mined} are minable from runs/ and CLAUDE.md"})
    ev = kb.get("eval") or {}
    if ev.get("answerable", 0) and (ev.get("P@1") or 0) < 0.5:
        out.append({"id": "kb.retrieval_weak", "priority": 4, "action": None,
                    "why": f"KB leave-one-out P@1 = {ev.get('P@1')} over {ev.get('answerable')} answerable "
                           f"cases — too few cases share a label to retrieve a usable precedent"})
    exp = (kb.get("experiment") or {}).get("summary") or {}
    if exp and exp.get("kb", {}).get("rate") is not None and exp["kb"]["rate"] <= exp.get("nokb", {}).get("rate", 0):
        out.append({"id": "kb.no_benefit", "priority": 4, "action": None,
                    "why": f"A/B repair experiment: kb {exp['kb']['rate']} vs nokb {exp['nokb']['rate']} — "
                           f"the KB is not paying for its context yet"})
    out.sort(key=lambda r: r["priority"])
    return out


# ------------------------------------------------------------------ L2: act ---

def _run(cmd: List[str], timeout: int = 5400) -> dict:
    t0 = time.time()
    p = subprocess.run(cmd, cwd=gate.ROOT, capture_output=True, text=True, timeout=timeout)
    return {"cmd": " ".join(cmd[-4:]), "rc": p.returncode, "elapsed_s": round(time.time() - t0, 1),
            "tail": (p.stdout or p.stderr).strip().splitlines()[-3:]}


ACTIONS: Dict[str, Callable[[str], dict]] = {
    "kb": lambda arg: _run([PY, str(gate.ROOT / "pipeline/kb.py"), "build"]),
    "mutate": lambda arg: _run([PY, str(gate.ROOT / "pipeline/mutate.py"), "-j", "6"]),
    "gate": lambda arg: _run([PY, str(gate.ROOT / "pipeline/gate.py")]),
    "tests": lambda arg: _run([PY, "-m", "pytest", "tests", "-q"]),
    "regen_tb": lambda arg: _run([PY, str(gate.ROOT / "pipeline/generate_tb.py"), "--module", arg,
                                  "--max-iters", "3"]),
    "regen_rtl": lambda arg: _run([PY, str(gate.ROOT / "pipeline/generate.py"), "--module", arg,
                                   "--max-iters", "3"]),
}


def act(action: str) -> dict:
    name, _, arg = action.partition(":")
    if name not in ACTIONS:
        raise SystemExit(f"unknown action {name}")
    return {"action": action, **ACTIONS[name](arg)}


def regression(before: dict, after: dict) -> dict:
    b, a = key_metrics(before), key_metrics(after)
    worse = {k: [b[k], a[k]] for k in b if a[k] < b[k]}
    better = {k: [b[k], a[k]] for k in b if a[k] > b[k]}
    return {"better": better, "worse": worse, "regressed": bool(worse)}


def _history() -> List[dict]:
    return json.loads(HISTORY.read_text())["rounds"] if HISTORY.exists() else []


def _record(round_rec: dict) -> None:
    HISTORY.parent.mkdir(exist_ok=True)
    rounds = _history() + [round_rec]
    HISTORY.write_text(json.dumps({"schema_version": 1, "rounds": rounds}, indent=2, ensure_ascii=False))


def loop(max_rounds: int = 1, *, dry_run: bool = False, only: Optional[str] = None) -> dict:
    """Act on the top actionable recommendation, re-measure, gate against regression."""
    rounds = []
    for i in range(max_rounds):
        before = snapshot()
        recs = [r for r in recommend(before) if r["action"] and (not only or r["id"] == only)]
        if not recs:
            rounds.append({"round": i + 1, "note": "nothing actionable", "metrics": key_metrics(before)})
            break
        top = recs[0]
        if dry_run:
            rounds.append({"round": i + 1, "would_act_on": top, "metrics": key_metrics(before)})
            break
        result = act(top["action"])
        after = snapshot()
        rec = {"round": i + 1, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "recommendation": top,
               "result": result, "metrics_before": key_metrics(before), "metrics_after": key_metrics(after),
               "delta": regression(before, after)}
        _record(rec)
        rounds.append(rec)
        if rec["delta"]["regressed"]:
            rec["stopped"] = "regression gate: a headline metric got worse"
            break
    return {"rounds": rounds}


def write_report(snap: dict, recs: List[dict]) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    p = REPORTS / f"{time.strftime('%Y%m%d-%H%M%S')}.md"
    hist = _history()
    prev = hist[-1]["metrics_after"] if hist else None
    cur = key_metrics(snap)
    lines = [f"# Self-improvement snapshot — {snap['at']}", "", "## Metrics", "",
             "| metric | value |" + (" previous |" if prev else ""), "|---|---|" + ("---|" if prev else "")]
    for k, v in cur.items():
        lines.append(f"| {k} | {v} |" + (f" {prev.get(k)} |" if prev else ""))
    lines += ["", "## Ranked gaps", ""]
    if not recs:
        lines.append("None: every measured gap is closed.")
    for r in recs:
        lines.append(f"- **P{r['priority']} `{r['id']}`** — {r['why']}"
                     + (f"  \n  action: `{r['action']}`" if r["action"] else "  \n  action: manual"))
    p.write_text("\n".join(lines) + "\n")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("report")
    a_ = sub.add_parser("act"); a_.add_argument("--action", required=True)
    l_ = sub.add_parser("loop"); l_.add_argument("--max", type=int, default=1)
    l_.add_argument("--dry-run", action="store_true"); l_.add_argument("--only")
    a = ap.parse_args()
    if a.cmd == "snapshot":
        print(json.dumps(snapshot(), indent=2, ensure_ascii=False))
    elif a.cmd == "report":
        s = snapshot()
        r = recommend(s)
        p = write_report(s, r)
        print(json.dumps({"metrics": key_metrics(s), "recommendations": r,
                          "report": str(p.relative_to(gate.ROOT))}, indent=2, ensure_ascii=False))
    elif a.cmd == "act":
        print(json.dumps(act(a.action), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(loop(a.max, dry_run=a.dry_run, only=a.only), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
