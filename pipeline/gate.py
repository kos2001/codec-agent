"""Sign-off gate for a candidate G.711 RTL directory.

Adapted from hls-rtl api/improve.py::prove and api/signoff.py. No LLM anywhere in
here: every verdict comes from a tool.

Stages (in order; each is pass / fail / skip):
  lint        verilator --lint-only -Wall per module                    gating
  synthesize  yosys synth + check -assert + no latches                  gating
  simulate    iverilog (4-state) regression: L1 STL vectors, L2
              exhaustive decode, L3 spec edges, P full-rate guard       gating
  formal      SymbiYosys: unbounded control + datapath proof, cover     gating
  equiv       yosys equivalence vs the baseline RTL                     information only

Honesty rule carried over unchanged: a skipped stage is missing evidence, not a
pass. Any gating skip makes the verdict INCONCLUSIVE, never PROVEN.

Usage:
  python pipeline/gate.py                         # gate the committed RTL
  python pipeline/gate.py --rtl-dir path/to/cand  # gate a candidate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
G711 = ROOT / "generated" / "g711"
BASELINE_RTL = G711 / "rtl"
TB_DIR = G711 / "tb"
FORMAL_DIR = G711 / "formal"
RUNS = ROOT / "runs"
MODULES = ("g711_alaw_enc", "g711_alaw_dec", "g711_ulaw_enc", "g711_ulaw_dec")
GATING = ("lint", "synthesize", "simulate", "formal")
STAGE_ORDER = GATING + ("equiv",)


@dataclass
class Proof:
    name: str
    rtl_dir: str
    stages: Dict[str, str] = field(default_factory=dict)       # stage -> pass/fail/skip
    details: Dict[str, str] = field(default_factory=dict)
    tests: Dict[str, str] = field(default_factory=dict)        # test -> RESULT line
    first_errors: Dict[str, str] = field(default_factory=dict)  # test -> first ERROR line
    evidence: Dict[str, str] = field(default_factory=dict)     # vector stem -> sha256
    rtl_sha256: Dict[str, str] = field(default_factory=dict)
    elapsed_s: float = 0.0

    PROVES = (
        "For every module: (simulate) output is byte-identical to the ITU-T G.191 STL "
        "reference on its official sweep vectors (all 65536 16-bit inputs), to STL on all "
        "256 decoder codes, and to the spec-table model on segment-edge vectors, under "
        "random stalls in a 4-state simulator; full-rate throughput is 1 sample/cycle; "
        "(formal, unbounded k-induction) on EVERY reachable trace: no sample lost, "
        "duplicated or invented, no false backpressure, output stable under stall, reset "
        "empties the stage, and every delivered value equals a reference model written "
        "from the G.711 table formulas.")
    DOES_NOT_PROVE = (
        "Behaviour the Recommendation leaves open (§3.6: inputs exactly on a decision value "
        "follow the STL convention, a design choice), excluded clauses (§3.2 all-zero "
        "suppression, §3.5 transcoding), timing closure/Fmax, area or power on a real "
        "library. Table conformance itself is checked by tests/test_spec_tables.py, not "
        "by this gate.")

    @property
    def verdict(self) -> str:
        gating = [self.stages.get(s) for s in GATING]
        if any(s == "fail" for s in gating):
            return "REJECTED"
        if all(s == "pass" for s in gating):
            return "PROVEN"
        return "INCONCLUSIVE"

    def failure_evidence(self, limit: int = 6) -> str:
        """Concrete facts a next generation attempt needs — values, not adjectives."""
        lines = [f"{s}: FAILED — {self.details.get(s, '')}"
                 for s in STAGE_ORDER if self.stages.get(s) == "fail"]
        for test, err in list(self.first_errors.items())[:limit]:
            lines.append(f"{test}: {err}")
        return "\n".join(lines) or "no failing stage"

    def to_dict(self) -> dict:
        return {"name": self.name, "verdict": self.verdict, "rtl_dir": self.rtl_dir,
                "stages": self.stages, "details": self.details, "tests": self.tests,
                "first_errors": self.first_errors, "evidence_sha256": self.evidence,
                "rtl_sha256": self.rtl_sha256, "elapsed_s": round(self.elapsed_s, 1),
                "proves": self.PROVES if self.verdict == "PROVEN" else "",
                "does_not_prove": self.DOES_NOT_PROVE,
                "failure_evidence": "" if self.verdict == "PROVEN" else self.failure_evidence()}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _run(cmd, cwd: Path, timeout: int) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"


# ------------------------------------------------------------------ stages ---

def stage_lint(rtl: Path, build: Path, timeout: int) -> Tuple[str, str]:
    if not shutil.which("verilator"):
        return "skip", "verilator not available"
    bad = []
    for m in MODULES:
        rc, out = _run(["verilator", "--lint-only", "-Wall", "--top-module", m, str(rtl / f"{m}.sv")],
                       build, timeout)
        (build / f"lint_{m}.log").write_text(out)
        if rc != 0:
            first = next((l for l in out.splitlines() if l.startswith("%")), out[:200])
            bad.append(f"{m}: {first}")
    return ("fail", "; ".join(bad)) if bad else ("pass", f"{len(MODULES)} modules clean")


def stage_synthesize(rtl: Path, build: Path, timeout: int) -> Tuple[str, str]:
    if not shutil.which("yosys"):
        return "skip", "yosys not available"
    cells, bad = {}, []
    for m in MODULES:
        # REQ-IF-04 requires a synchronous reset. Simulation and formal sample reset at clock
        # edges and cannot tell an async reset apart; the netlist can. Checked right after
        # `proc`, before techmapping renames the cells. (Found by a Hermes-generated
        # candidate that passed every other stage with `posedge clk or negedge rst_n`.)
        script = (f"read_verilog -sv {rtl / (m + '.sv')}; hierarchy -top {m}; proc; "
                  "select -assert-none t:$adff t:$adffe t:$aldff t:$aldffe t:$dffsr t:$dffsre t:$sr; "
                  f"synth -top {m} -flatten; check -assert; "
                  "select -assert-none t:$dlatch t:$_DLATCH_*; stat")
        rc, out = _run(["yosys", "-q", "-p", script, "-l", str(build / f"synth_{m}.log")], build, timeout)
        log = (build / f"synth_{m}.log").read_text() if (build / f"synth_{m}.log").exists() else out
        if rc != 0:
            err = next((l for l in (out + log).splitlines() if "ERROR" in l), "synthesis failed")
            if "Assertion failed" in err and ("$adff" in (out + log) or "adff" in err):
                err = "REQ-IF-04 violated: asynchronous reset/set flip-flop (use always_ff @(posedge clk) with if (!rst_n))"
            bad.append(f"{m}: {err.strip()}")
            continue
        c = re.findall(r"^\s+(\d+)\s+cells\s*$", log, re.M)
        cells[m] = int(c[-1]) if c else -1
    if bad:
        return "fail", "; ".join(bad)
    return "pass", " ".join(f"{m.replace('g711_', '')}={n}" for m, n in cells.items()) + " cells"


def stage_simulate(rtl: Path, build: Path, timeout: int, proof: Proof) -> Tuple[str, str]:
    if not shutil.which("iverilog"):
        return "skip", "iverilog not available"
    rc, _ = _run(["env", f"RTL_DIR={rtl}", f"BUILD={build / 'sim'}", "bash", str(TB_DIR / "run_all.sh")],
                 TB_DIR, timeout)
    listed = [l.split() for l in (TB_DIR / "vectors" / "tests.txt").read_text().splitlines() if l.strip()]
    passed = 0
    for name, _mode, _n, _bp, stem in listed:
        log = build / "sim" / f"{name}.log"
        text = log.read_text() if log.exists() else ""
        res = next((l for l in text.splitlines() if l.startswith("RESULT")), "RESULT FAIL (no result: did not compile or run)")
        err = next((l for l in text.splitlines() if l.startswith("ERROR")), "")
        proof.tests[name] = res
        if res.startswith("RESULT PASS"):
            passed += 1
            proof.evidence[stem] = _sha(TB_DIR / "vectors" / f"{stem}.exp")
        else:
            proof.first_errors[name] = err or res
    total = len(listed)
    if rc == 124:
        return "fail", f"regression timed out ({passed}/{total} passed)"
    return ("pass" if passed == total else "fail"), f"{passed}/{total} tests pass"


def _sby_cmd() -> Optional[List[str]]:
    sby, py = shutil.which("sby"), ROOT / ".venv" / "bin" / "python"
    if not sby:
        return None
    for interp in ([str(py)] if py.exists() else []) + [sys.executable]:
        if subprocess.run([interp, "-c", "import click"], capture_output=True).returncode == 0:
            return [interp, sby]
    return None


def stage_formal(rtl: Path, build: Path, timeout: int) -> Tuple[str, str]:
    cmd = _sby_cmd()
    if cmd is None:
        return "skip", "sby (with python click) not available"
    fdir = build / "formal"
    shutil.rmtree(fdir, ignore_errors=True)
    fdir.mkdir(parents=True)
    shutil.copy(FORMAL_DIR / "g711_props.sv", fdir)
    sby = (FORMAL_DIR / "g711.sby").read_text()
    for m in MODULES:
        sby = sby.replace(f"../rtl/{m}.sv", str(rtl / f"{m}.sv"))
    (fdir / "g711.sby").write_text(sby)
    tasks = re.search(r"\[tasks\]\n(.*?)\n\n", sby, re.S).group(1).split()
    bad, ok = [], 0
    for t in tasks:
        rc, out = _run(cmd + ["-f", "g711.sby", t], fdir, timeout)
        status = re.search(r"DONE \((\w+)", out)
        status = status.group(1) if status else ("TIMEOUT" if rc == 124 else "ERROR")
        if status == "PASS":
            ok += 1
            continue
        failed = re.findall(r"(?:Assert failed in|Unreached cover statement at) \S+: (\w+)", out)
        err = next((l.split("ERROR:", 1)[1].strip() for l in out.splitlines() if "ERROR:" in l), "")
        bad.append(f"{t} {status}" + (f" [{', '.join(sorted(set(failed)))}]" if failed else "")
                   + (f" ({err})" if err and not failed else ""))
    return ("fail", "; ".join(bad)) if bad else ("pass", f"{ok}/{len(tasks)} tasks (prove: unbounded, cover: reached)")


def _valid_qualified_wrapper(module: str, inner: str, name: str) -> str:
    """Expose the DUT with its data output masked by out_valid. Output data while
    out_valid=0 is a don't-care of the interface (REQ-IF-03/05), so two designs that
    differ only there are functionally equivalent and must not be reported as different."""
    data, w = ("out_code", 8) if module.endswith("_enc") else ("out_pcm", 16)
    din, dw = ("in_pcm", 16) if module.endswith("_enc") else ("in_code", 8)
    return f"""module {name} (input logic clk, input logic rst_n, input logic in_valid, output logic in_ready,
  input logic [{dw - 1}:0] {din}, output logic out_valid, input logic out_ready, output logic [{w - 1}:0] q);
  logic [{w - 1}:0] d;
  {inner} u (.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_ready(in_ready), .{din}({din}),
             .out_valid(out_valid), .out_ready(out_ready), .{data}(d));
  assign q = out_valid ? d : '0;
endmodule
"""


def stage_equiv(rtl: Path, build: Path, timeout: int, baseline: Path) -> Tuple[str, str]:
    """Is the candidate functionally the same design as the baseline? Information only.
    Valid-qualified sequential equivalence: miter over wrappers that mask output data by
    out_valid, temporal induction from a common zero state."""
    if not shutil.which("yosys"):
        return "skip", "yosys not available"
    if rtl.resolve() == baseline.resolve():
        return "pass", "candidate is the baseline"
    same, diff = [], []
    for m in MODULES:
        a, b = baseline / f"{m}.sv", rtl / f"{m}.sv"
        if a.read_bytes() == b.read_bytes():
            same.append(m); continue
        src = build / f"eq_{m}.sv"
        src.write_text(
            re.sub(rf"\bmodule\s+{m}\b", f"module {m}_gold", a.read_text(), count=1) + "\n" +
            re.sub(rf"\bmodule\s+{m}\b", f"module {m}_gate", b.read_text(), count=1) + "\n" +
            _valid_qualified_wrapper(m, f"{m}_gold", "gold") + _valid_qualified_wrapper(m, f"{m}_gate", "gate"))
        script = (f"read_verilog -sv {src}; proc; flatten; opt_clean; "
                  "miter -equiv -flatten -make_outputs gold gate miter; hierarchy -top miter; opt_clean; "
                  "sat -verify -tempinduct -prove trigger 0 -set-init-zero -seq 1 -maxsteps 8 miter")
        rc, out = _run(["yosys", "-q", "-p", script], build, timeout)
        if rc == 0:
            same.append(m)
        elif "proof did fail" in out or "model found: FAIL" in out:
            diff.append(m)
        else:                     # a tool limitation is missing evidence, never "differs"
            err = next((l for l in out.splitlines() if "ERROR" in l), out[-200:])
            return "skip", f"{m}: equivalence not decidable here — {err.strip()}"
    if diff:
        return "fail", "functionally differs from baseline (valid-qualified): " + ", ".join(diff)
    return "pass", "equivalent to baseline on valid outputs (" + ", ".join(x.replace("g711_", "") for x in same) + ")"


# -------------------------------------------------------------------- gate ---

def prove(rtl_dir: Path = BASELINE_RTL, *, name: str = "", timeout: int = 900,
          stop_on_fail: bool = True, baseline: Path = BASELINE_RTL,
          build_root: Optional[Path] = None, archive: bool = True) -> Proof:
    t0 = time.time()
    rtl_dir = Path(rtl_dir).resolve()
    name = name or ("baseline" if rtl_dir == baseline.resolve() else rtl_dir.name)
    build = (build_root or ROOT / "build" / "gate") / name
    shutil.rmtree(build, ignore_errors=True)
    build.mkdir(parents=True)
    proof = Proof(name=name, rtl_dir=str(rtl_dir))
    proof.rtl_sha256 = {m: _sha(rtl_dir / f"{m}.sv") for m in MODULES if (rtl_dir / f"{m}.sv").exists()}
    missing = [m for m in MODULES if m not in proof.rtl_sha256]

    runners = {
        "lint": lambda: stage_lint(rtl_dir, build, timeout),
        "synthesize": lambda: stage_synthesize(rtl_dir, build, timeout),
        "simulate": lambda: stage_simulate(rtl_dir, build, timeout, proof),
        "formal": lambda: stage_formal(rtl_dir, build, timeout),
        "equiv": lambda: stage_equiv(rtl_dir, build, timeout, baseline),
    }
    for stage in STAGE_ORDER:
        if missing:
            proof.stages[stage], proof.details[stage] = "fail", f"missing modules: {', '.join(missing)}"
            break
        if stop_on_fail and any(proof.stages.get(s) == "fail" for s in GATING):
            proof.stages[stage], proof.details[stage] = "skip", "not run: an earlier gating stage failed"
            continue
        proof.stages[stage], proof.details[stage] = runners[stage]()
    proof.elapsed_s = time.time() - t0
    if archive:
        RUNS.mkdir(exist_ok=True)
        (RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-gate-{name}.json").write_text(
            json.dumps(proof.to_dict(), indent=2, ensure_ascii=False))
    return proof


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rtl-dir", type=Path, default=BASELINE_RTL)
    ap.add_argument("--name", default="")
    ap.add_argument("--no-stop", action="store_true", help="run every stage even after a failure")
    ap.add_argument("--json", action="store_true", help="print the full proof as JSON")
    args = ap.parse_args()
    proof = prove(args.rtl_dir, name=args.name, stop_on_fail=not args.no_stop)
    if args.json:
        print(json.dumps(proof.to_dict(), indent=2, ensure_ascii=False))
    else:
        for s in STAGE_ORDER:
            tag = "" if s in GATING else "  (info)"
            print(f"  {s:11s} {proof.stages.get(s, '-'):5s} {proof.details.get(s, '')}{tag}")
        print(f"VERDICT {proof.verdict}  ({proof.elapsed_s:.1f}s)")
        if proof.verdict != "PROVEN":
            print(proof.failure_evidence())
    return 0 if proof.verdict == "PROVEN" else 1


if __name__ == "__main__":
    sys.exit(main())
