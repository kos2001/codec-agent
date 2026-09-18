"""Spec → RTL generation loop: Hermes proposes, the gate decides.

Adapted from hls-rtl api/improve.py::run_loop. For each target module:

  requirements.yaml + port contract ──► Hermes (codec-agent API server)
                                               │ ```systemverilog
                                               ▼
                                 gate.prove(candidate)  — no LLM
                                   │ PROVEN            │ REJECTED
                                   ▼                   ▼
                                 stop        failure_evidence() + previous code
                                                  ──► Hermes (repair), ≤ max_iters

The generator never sees the baseline implementation — only the requirements, the
port list and conventions — so a PROVEN result is genuine generation, not copying.
The `equiv` stage then reports whether the new RTL is logically identical to the
hand-written baseline (information only).

Outcomes (never a bare boolean):
  PROVEN      a generated candidate passed every gating stage
  EXHAUSTED   the iteration budget ran out; every attempt and its evidence is kept
  ERRORED     the API server failed (unreachable, auth, upstream quota)

Candidates are written under build/generate/<run>/ and are NOT applied to
generated/g711/rtl — promote one by hand after review.

Usage:
  python pipeline/generate.py --health
  python pipeline/generate.py --module g711_ulaw_enc --max-iters 3
  python pipeline/generate.py --all
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent))
import gate  # noqa: E402
from hermes_client import HermesClient, HermesError, upstream_model  # noqa: E402

REQS = gate.G711 / "requirements.yaml"
BUILD = gate.ROOT / "build" / "generate"

LAW_SECTION = {"g711_alaw_enc": "alaw", "g711_alaw_dec": "alaw",
               "g711_ulaw_enc": "ulaw", "g711_ulaw_dec": "ulaw"}
ROLE = {"g711_alaw_enc": "A-law encoder: 16-bit linear PCM -> 8-bit character",
        "g711_alaw_dec": "A-law decoder: 8-bit character -> 16-bit linear PCM",
        "g711_ulaw_enc": "mu-law encoder: 16-bit linear PCM -> 8-bit character",
        "g711_ulaw_dec": "mu-law decoder: 8-bit character -> 16-bit linear PCM"}


def port_contract(module: str) -> str:
    """The module header only (through the port list's `);`) — the interface, not the design.
    Lint pragmas and comments are stripped so no implementation hint leaks through."""
    src = (gate.BASELINE_RTL / f"{module}.sv").read_text()
    m = re.search(rf"module\s+{module}\s*\((.*?)\);", src, re.S)
    ports = [re.sub(r"/\*.*?\*/|//.*", "", l).rstrip() for l in m.group(1).splitlines()]
    return f"module {module} (\n" + "\n".join(p for p in ports if p.strip()) + "\n);"


def requirements_for(module: str) -> str:
    text = REQS.read_text()
    keep, out, section = {"scope", "interface", LAW_SECTION[module]}, [], None
    for line in text.splitlines():
        top = re.match(r"^(\w+):", line)
        if top:
            section = top.group(1)
        if line.startswith("#") or section in keep:
            out.append(line)
    return "\n".join(out)


def build_prompt(module: str) -> str:
    return f"""Write the SystemVerilog module `{module}` ({ROLE[module]}).

REQUIREMENTS (ITU-T G.711; REQ-IDs to cite in comments):
```yaml
{requirements_for(module)}
```

PORT CONTRACT (exact names, widths and directions; do not add or remove ports):
```systemverilog
{port_contract(module)}
```

Interface behaviour: in_ready = ~out_valid | out_ready. The reset is SYNCHRONOUS (REQ-IF-04):
`always_ff @(posedge clk)` with `if (!rst_n) out_valid <= 1'b0;` — never
`@(posedge clk or negedge rst_n)`, which the gate rejects at synthesis. On a clock edge with rst_n low,
out_valid <= 0. Otherwise when in_ready: out_valid <= in_valid, and if in_valid the output
register takes the value computed from the input. Latency is one cycle.

LINT CONTRACT (the gate runs `verilator --lint-only -Wall`; ANY warning rejects the design):
- Every assignment width-exact: size constants explicitly (13'd33, not 33), slice or
  zero-extend operands so left and right sides have identical widths (no WIDTHTRUNC/WIDTHEXPAND).
- No unused signals or unused bits (UNUSEDSIGNAL). Input bits below the codec resolution
  are unused by definition: wrap only that port declaration as
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic signed [15:0] in_pcm,
    /* verilator lint_on UNUSEDSIGNAL */
  Do not declare intermediate signals wider than the bits you read.
- No latches: every always_comb output assigned on every path; case statements have default.

Reply with exactly one ```systemverilog block containing `module {module}` through `endmodule`."""


def build_repair_prompt(module: str, previous: str, evidence: str, iteration: int) -> str:
    return f"""{build_prompt(module)}

YOUR ATTEMPT {iteration} WAS REJECTED BY THE SIGN-OFF GATE (tools, not opinions).
Evidence:
```
{evidence}
```
Stimulus/got/exp values are hex. For encoders stim is the 16-bit input and got/exp the
8-bit code; for decoders stim is the code and got/exp the 16-bit output. The expected
values come from ITU-T G.191 STL and the G.711 tables and are authoritative.

Your rejected code:
```systemverilog
{previous}
```
Find the defect that explains these exact values, fix it, and change nothing else."""


def extract_module(reply: str, module: str) -> Optional[str]:
    for block in re.findall(r"```(?:systemverilog|verilog|sv)?\s*\n(.*?)```", reply, re.S):
        m = re.search(rf"\bmodule\s+{module}\b.*?\bendmodule\b", block, re.S)
        if m:
            return m.group(0).strip() + "\n"
    m = re.search(rf"\bmodule\s+{module}\b.*?\bendmodule\b", reply, re.S)
    return m.group(0).strip() + "\n" if m else None


def _evidence(proof: gate.Proof, build: Path, module: str) -> str:
    lines = [proof.failure_evidence()]
    lint_log = build / f"lint_{module}.log"
    if proof.stages.get("lint") == "fail" and lint_log.exists():
        lines += [l for l in lint_log.read_text().splitlines() if l.startswith("%")][:8]
    return "\n".join(lines)[:4000]


def run(module: str, *, max_iters: int = 3, client: Optional[HermesClient] = None) -> dict:
    client = client or HermesClient(name=f"generate-{module}")
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{module}"
    run_dir = BUILD / run_id
    attempts: List[dict] = []
    outcome, proven = "EXHAUSTED", None
    prompt = build_prompt(module)

    for i in range(1, max_iters + 1):
        try:
            reply = client.chat(prompt)
        except HermesError as exc:
            attempts.append({"iteration": i, "error": str(exc)})
            outcome = "ERRORED"
            break
        code = extract_module(reply.content, module)
        record = {"iteration": i, "usage": reply.usage, "llm_elapsed_s": round(reply.elapsed_s, 1)}
        if code is None:
            record.update(error="no complete module in reply", reply_head=reply.content[:400])
            attempts.append(record)
            prompt = build_prompt(module) + "\n\nYour previous reply had no complete module. " \
                     "Reply with exactly one ```systemverilog block."
            continue

        cand = run_dir / f"iter{i}"
        cand.mkdir(parents=True, exist_ok=True)
        for m in gate.MODULES:
            shutil.copy(gate.BASELINE_RTL / f"{m}.sv", cand)
        (cand / f"{module}.sv").write_text(code)
        proof = gate.prove(cand, name=f"{run_id}-iter{i}", stop_on_fail=False,
                           build_root=run_dir / "gate", archive=False)
        record.update(candidate=str((cand / f"{module}.sv").relative_to(gate.ROOT)),
                      verdict=proof.verdict, stages=proof.stages, details=proof.details,
                      equivalent_to_baseline=proof.stages.get("equiv") == "pass",
                      gate_elapsed_s=round(proof.elapsed_s, 1))
        attempts.append(record)
        if proof.verdict == "PROVEN":
            outcome, proven = "PROVEN", record
            break
        evidence = _evidence(proof, run_dir / "gate" / proof.name, module)
        record["evidence"] = evidence
        prompt = build_repair_prompt(module, code, evidence, i)

    result = {
        "run_id": run_id, "module": module, "outcome": outcome, "iterations": len(attempts),
        "max_iters": max_iters, "model": upstream_model() or client.s.model, "server_model": client.s.model,
        "server": client.s.base_url,
        "proven_candidate": proven["candidate"] if proven else None,
        "equivalent_to_baseline": proven["equivalent_to_baseline"] if proven else None,
        "attempts": attempts,
        "proves": gate.Proof.PROVES if proven else "",
        "note": "Candidates are not applied. Review and copy into generated/g711/rtl by hand.",
    }
    gate.RUNS.mkdir(exist_ok=True)
    (gate.RUNS / f"{run_id}-generate.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", choices=gate.MODULES)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max-iters", type=int, default=3)
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--print-prompt", action="store_true")
    args = ap.parse_args()
    if args.health:
        h = HermesClient().health()
        print(json.dumps(h, indent=2))
        return 0 if h.get("model_available") else 1
    targets = list(gate.MODULES) if args.all else [args.module] if args.module else []
    if not targets:
        ap.error("pass --module, --all or --health")
    if args.print_prompt:
        print(build_prompt(targets[0]))
        return 0
    rc = 0
    for mod in targets:
        r = run(mod, max_iters=args.max_iters)
        print(f"{mod:15s} {r['outcome']:9s} iterations={r['iterations']} "
              f"equiv_to_baseline={r['equivalent_to_baseline']} run={r['run_id']}")
        for a in r["attempts"]:
            print(f"   iter{a['iteration']}: {a.get('verdict') or 'ERROR'} "
                  f"{ {k: v for k, v in (a.get('stages') or {}).items()} or a.get('error', '')}")
        rc |= r["outcome"] != "PROVEN"
    return rc


if __name__ == "__main__":
    sys.exit(main())
