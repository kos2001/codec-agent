"""Testbench generation: Hermes writes a self-checking testbench, tools judge the testbench.

A generated testbench is itself an unverified claim, so it is checked twice:

  1. Soundness — it must PASS on the committed RTL, which the gate has PROVEN against
     ITU-T STL and the G.711 tables. A FAIL there means the testbench's own reference
     model is wrong; the mismatching stimuli are fed back together with the
     authoritative expected values (spec_model, bit-exact with STL) for repair.
  2. Sensitivity — it must FAIL on broken RTL. Every non-equivalent mutant of the
     module from pipeline/mutate.py is simulated under the testbench; the kill rate is
     the testbench's detection power. A testbench that always prints PASS scores 0.

The model is given the requirements and the port contract only: no expected vectors,
no committed RTL, no STL output. Its reference model is therefore independent.

Outcomes:
  ACCEPTED    sound, and kill rate >= --min-kill (default 90%)
  WEAK        sound, but kills fewer mutants than required
  EXHAUSTED   never became sound within the iteration budget
  ERRORED     Hermes API failure

Usage:
  python pipeline/generate_tb.py --module g711_ulaw_enc [--max-iters 3] [--min-kill 90]
  python pipeline/generate_tb.py --check path/to/tb.sv --module g711_ulaw_enc   # no LLM
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "golden" / "g711"))
import gate  # noqa: E402
import generate as gen  # noqa: E402
import mutate  # noqa: E402
import spec_model  # noqa: E402
from hermes_client import HermesClient, HermesError, upstream_model  # noqa: E402

BUILD = gate.ROOT / "build" / "generate_tb"
SIM_TIMEOUT = 120

GOLDEN = {"g711_alaw_enc": spec_model.alaw_encode, "g711_alaw_dec": spec_model.alaw_decode,
          "g711_ulaw_enc": spec_model.ulaw_encode, "g711_ulaw_dec": spec_model.ulaw_decode}


def build_prompt(module: str) -> str:
    enc = module.endswith("_enc")
    stim, out = ("in_pcm (16-bit signed)", "out_code (8-bit)") if enc else ("in_code (8-bit)", "out_pcm (16-bit signed)")
    return f"""Write a self-checking SystemVerilog testbench `tb_{module}` for the module below.

REQUIREMENTS (ITU-T G.711):
```yaml
{gen.requirements_for(module)}
```

DEVICE UNDER TEST (port contract; the implementation is NOT provided):
```systemverilog
{gen.port_contract(module)}
```
Handshake: in_ready = ~out_valid | out_ready; one register stage; rst_n synchronous active-low
clears out_valid. A sample is accepted when in_valid && in_ready, delivered when
out_valid && out_ready.

TESTBENCH CONTRACT (a tool runs it with `iverilog -g2012` and parses the log):
- Top module name `tb_{module}`, no ports; instantiate the DUT as `{module} dut (...)`.
- Implement your OWN reference model of the requirements as a SystemVerilog function
  (stimulus {stim} -> expected {out}). Do not hard-code expected values from memory.
- Apply reset for several cycles, then drive stimuli through the valid/ready handshake with
  pseudo-random in_valid gaps and out_ready stalls (use $urandom with a fixed seed).
- Keep a queue of expected outputs; compare every delivered output in order.
- Cover: {'every one of the 256 codes' if not enc else 'all 65536 16-bit inputs (exhaustive sweep -32768..32767)'},
  plus segment boundaries, saturation, zero and sign handling.
- Also check: out_valid is 0 after reset; data stays stable while out_valid && !out_ready;
  no output is lost, duplicated or invented (count delivered == count accepted).
- On each mismatch print exactly: ERROR stim=%h got=%h exp=%h   (hex, stim zero-extended to 16 bits)
- At the end print exactly one line: RESULT PASS checks=%0d   or   RESULT FAIL checks=%0d errors=%0d
  then call $finish. Include a watchdog that prints RESULT FAIL and finishes if the run hangs.
- No $dumpfile, no DPI, no classes/UVM, no `timescale-dependent #delays inside functions.
- Icarus Verilog limits: do NOT declare variables inside always/initial blocks or with
  `automatic` in procedural code (declare them at module level); functions may use
  `automatic` and local integers. Queues ([$], push_back, pop_front, size) are supported.
  `$urandom(seed)` requires `seed` to be a module-level `integer` variable (not a literal,
  not an `int` parameter); or call `$urandom` without an argument after one seeding call.

Reply with exactly one ```systemverilog block containing `module tb_{module}` through `endmodule`."""


def build_repair_prompt(module: str, previous: str, evidence: str, iteration: int) -> str:
    return f"""{build_prompt(module)}

YOUR TESTBENCH (attempt {iteration}) WAS RUN AGAINST AN RTL IMPLEMENTATION THAT A TOOL-DRIVEN GATE
HAS PROVEN BIT-EXACT WITH ITU-T G.191 STL ON EVERY INPUT. The testbench reported failures, so
its reference model or its protocol checking is wrong:
```
{evidence}
```

Your testbench:
```systemverilog
{previous}
```
Fix the testbench so it agrees with the authoritative values above. Change nothing else."""


def extract_tb(reply: str, module: str) -> Optional[str]:
    return gen.extract_module(reply, f"tb_{module}")


def _parse(log: str) -> dict:
    res = re.search(r"^RESULT (PASS|FAIL)(?: checks=(\d+))?(?: errors=(\d+))?", log, re.M)
    errs = re.findall(r"^ERROR stim=([0-9a-fA-FxXzZ]+) got=([0-9a-fA-FxXzZ]+) exp=([0-9a-fA-FxXzZ]+)", log, re.M)
    other = [l for l in log.splitlines() if l.startswith("ERROR") and not l.startswith("ERROR stim=")]
    return {"result": res.group(1) if res else None,
            "checks": int(res.group(2)) if res and res.group(2) else None,
            "errors": errs, "other_errors": other[:5]}


def simulate(tb: Path, rtl_file: Path, work: Path, module: str) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    vvp = work / "sim.vvp"
    c = subprocess.run(["iverilog", "-g2012", "-o", str(vvp), "-s", f"tb_{module}", str(tb), str(rtl_file)],
                       capture_output=True, text=True, timeout=SIM_TIMEOUT)
    if c.returncode != 0:
        return {"compiled": False, "log": c.stderr[-2000:], "result": None, "checks": None, "errors": [], "other_errors": []}
    try:
        r = subprocess.run(["vvp", "-n", str(vvp)], capture_output=True, text=True, timeout=SIM_TIMEOUT)
        log = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return {"compiled": True, "timeout": True, "log": "", "result": None, "checks": None, "errors": [], "other_errors": []}
    return {"compiled": True, "timeout": False, "log": log[-3000:], **_parse(log)}


def _hex(v: int, bits: int) -> str:
    return f"{v & ((1 << bits) - 1):0{(bits + 3) // 4}x}"


def soundness_evidence(module: str, run: dict) -> str:
    if not run["compiled"]:
        return "COMPILE ERROR (iverilog -g2012):\n" + run["log"]
    if run.get("timeout"):
        return f"The simulation did not finish within {SIM_TIMEOUT}s (hang, or no $finish)."
    lines = []
    if run["result"] is None:
        lines.append("No `RESULT PASS|FAIL` line was printed.")
    enc = module.endswith("_enc")
    fn = GOLDEN[module]
    misaligned = 0
    for stim, got, exp in run["errors"][:8]:
        try:
            s_ = int(stim, 16)
            if int(got, 16) & (0xFF if enc else 0xFFFF) != fn(s_ - 0x10000 if enc and s_ >= 0x8000 else s_ & 0xFF) & (0xFF if enc else 0xFFFF):
                misaligned += 1
        except ValueError:
            pass
    if misaligned:
        # The RTL is proven on every input, so an output that is wrong *for the stimulus the
        # testbench printed* means the testbench paired it with the wrong stimulus.
        lines.append(f"{misaligned} of the reported mismatches pair an output with a stimulus that did not "
                     "produce it (the RTL is proven correct for every input). Your stimulus/expected-value "
                     "bookkeeping is misaligned with the handshake: push the expected value exactly when "
                     "in_valid && in_ready at a clock edge, and pop exactly when out_valid && out_ready.")
    for stim, got, exp in run["errors"][:8]:
        try:
            s = int(stim, 16)
            x = s - 0x10000 if enc and s >= 0x8000 else s & 0xFF
            truth = fn(x)
            lines.append(f"stim={stim}: RTL delivered got={got}; your testbench expected exp={exp}; "
                         f"the authoritative value is {_hex(truth, 8 if enc else 16)}")
        except ValueError:
            lines.append(f"stim={stim} got={got} exp={exp} (unparseable)")
    lines += run["other_errors"]
    if run["result"] == "FAIL" and not run["errors"] and not run["other_errors"]:
        lines.append("RESULT FAIL without ERROR lines. Log tail:\n" + run["log"][-800:])
    return "\n".join(lines) or run["log"][-800:]


def kill_rate(tb: Path, module: str, work: Path, jobs: int = 8) -> dict:
    """Simulate the testbench against every mutant of `module` (dedup'd, excluding mutants
    that do not compile standalone — those are lint/synthesis catches, not TB catches)."""
    muts = mutate.generate((module,))
    known_equiv = {"g711_ulaw_enc": {"const-27-26-"}}.get(module, set())
    muts = [m for m in muts if m.id not in known_equiv]

    def one(m):
        d = work / f"mut-{m.id}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{module}.sv").write_text(m.code)
        r = simulate(tb, d / f"{module}.sv", d, module)
        shutil.rmtree(d, ignore_errors=True)
        if not r["compiled"]:
            status = "not-compilable"
        elif r["result"] == "PASS":
            status = "survived"
        else:
            status = "killed"
        return {"id": m.id, "operator": m.operator, "line": m.line, "after": m.after.strip(), "status": status}

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        rows = list(ex.map(one, muts))
    considered = [r for r in rows if r["status"] != "not-compilable"]
    killed = sum(r["status"] == "killed" for r in considered)
    return {"mutants": len(rows), "considered": len(considered), "killed": killed,
            "kill_pct": round(100 * killed / len(considered), 1) if considered else None,
            "survivors": [r for r in considered if r["status"] == "survived"], "rows": rows}


def check(tb: Path, module: str, work: Path, min_kill: float) -> dict:
    sound = simulate(tb, gate.BASELINE_RTL / f"{module}.sv", work / "sound", module)
    ok = sound["compiled"] and sound["result"] == "PASS" and not sound.get("timeout")
    out = {"sound": ok, "soundness": {k: v for k, v in sound.items() if k != "log"}, "log_tail": sound["log"][-600:]}
    if ok:
        k = kill_rate(tb, module, work / "mutants")
        out["kill"] = {x: y for x, y in k.items() if x != "rows"}
        out["verdict"] = "ACCEPTED" if (k["kill_pct"] or 0) >= min_kill else "WEAK"
    else:
        out["verdict"] = "UNSOUND"
        out["evidence"] = soundness_evidence(module, sound)
    return out


def run(module: str, *, max_iters: int = 3, min_kill: float = 90.0,
        client: Optional[HermesClient] = None) -> dict:
    client = client or HermesClient(name=f"generate-tb-{module}")
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-tb-{module}"
    run_dir = BUILD / run_id
    attempts: List[dict] = []
    outcome, accepted = "EXHAUSTED", None
    prompt = build_prompt(module)

    for i in range(1, max_iters + 1):
        try:
            reply = client.chat(prompt)
        except HermesError as exc:
            attempts.append({"iteration": i, "error": str(exc)})
            outcome = "ERRORED"
            break
        code = extract_tb(reply.content, module)
        rec = {"iteration": i, "usage": reply.usage, "llm_elapsed_s": round(reply.elapsed_s, 1)}
        if code is None:
            rec.update(error="no complete testbench module in reply", reply_head=reply.content[:400])
            attempts.append(rec)
            prompt = build_prompt(module) + "\n\nYour previous reply had no complete `tb_` module."
            continue
        d = run_dir / f"iter{i}"
        d.mkdir(parents=True, exist_ok=True)
        tb = d / f"tb_{module}.sv"
        tb.write_text(code)
        t0 = time.time()
        res = check(tb, module, d, min_kill)
        rec.update(testbench=str(tb.relative_to(gate.ROOT)), check_elapsed_s=round(time.time() - t0, 1), **res)
        attempts.append(rec)
        if res["verdict"] in ("ACCEPTED", "WEAK"):
            outcome, accepted = res["verdict"], rec
            break
        prompt = build_repair_prompt(module, code, res["evidence"], i)

    result = {"run_id": run_id, "module": module, "kind": "testbench", "outcome": outcome,
              "iterations": len(attempts), "max_iters": max_iters, "min_kill_pct": min_kill,
              "model": upstream_model() or client.s.model, "server": client.s.base_url,
              "testbench": accepted["testbench"] if accepted else None,
              "kill_pct": accepted["kill"]["kill_pct"] if accepted else None,
              "attempts": attempts,
              "note": "A sound testbench agrees with RTL proven against ITU-T STL; kill rate is its "
                      "detection power over the module's mutants. The gate's own testbench kills 100%."}
    gate.RUNS.mkdir(exist_ok=True)
    (gate.RUNS / f"{run_id}-generate.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", choices=gate.MODULES, required=True)
    ap.add_argument("--max-iters", type=int, default=3)
    ap.add_argument("--min-kill", type=float, default=90.0)
    ap.add_argument("--check", type=Path, help="evaluate an existing testbench file without the LLM")
    ap.add_argument("--print-prompt", action="store_true")
    args = ap.parse_args()
    if args.print_prompt:
        print(build_prompt(args.module))
        return 0
    if args.check:
        work = BUILD / f"check-{time.strftime('%Y%m%d-%H%M%S')}-{args.module}"
        r = check(args.check.resolve(), args.module, work, args.min_kill)
        print(json.dumps({k: v for k, v in r.items() if k != "log_tail"}, indent=2))
        return 0 if r["verdict"] == "ACCEPTED" else 1
    r = run(args.module, max_iters=args.max_iters, min_kill=args.min_kill)
    print(f"{r['module']} testbench: {r['outcome']} after {r['iterations']} iteration(s), "
          f"kill={r['kill_pct']}%  run={r['run_id']}")
    for a in r["attempts"]:
        print(f"   iter{a['iteration']}: {a.get('verdict') or 'ERROR'} "
              f"{a.get('soundness', {}).get('checks')} checks  "
              f"{(a.get('kill') or {}).get('killed')}/{(a.get('kill') or {}).get('considered')} mutants killed  "
              f"{a.get('error', '')}")
    return 0 if r["outcome"] == "ACCEPTED" else 1


if __name__ == "__main__":
    sys.exit(main())
