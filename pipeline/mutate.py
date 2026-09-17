"""Mutation coverage — would the gate actually CATCH a bug?

Adapted from hls-rtl api/mutate.py. A passing gate says the RTL agrees with the
oracle; it says nothing about sensitivity. This injects one plausible fault at a
time into one module, runs the real gate (pipeline/gate.py) on the result and
records which stages caught it.

Differences from hls-rtl, both because the G.711 gate costs ~5s instead of ~25s:
  * every stage runs on every mutant (no stop-on-first-fail), so the report is a
    detection MATRIX — it shows what formal catches on its own, not only what it
    catches first;
  * mutants run in parallel; each has an isolated candidate dir and build dir.

A survivor is either a detection gap or an equivalent mutant (an edit that does
not change behaviour). The `equiv` stage separates the two: a survivor that yosys
proves equivalent to the baseline is reported as `equivalent`, not as a gap.

Usage:
  python pipeline/mutate.py --preview
  python pipeline/mutate.py -j 8
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).parent))
import gate  # noqa: E402

BUILD = gate.ROOT / "build" / "mutate"


@dataclass
class Mutant:
    id: str
    module: str
    operator: str
    description: str
    line: int
    before: str
    after: str
    code: str = field(repr=False, default="")

    def to_dict(self) -> dict:
        return {"id": self.id, "module": self.module, "operator": self.operator,
                "description": self.description, "line": self.line,
                "before": self.before.strip(), "after": self.after.strip()}


def _code_lines(src: str) -> List[Tuple[int, str]]:
    """Code lines with trailing comments removed. Declarations and port lists are
    skipped: mutating a width declaration exercises the elaborator, not the design."""
    out = []
    for n, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if (not s or s.startswith("//") or s.startswith("/*") or s.startswith("module")
                or s.startswith("input") or s.startswith("output") or s.startswith("logic")
                or s.startswith(")") or s.startswith("endmodule")):
            continue
        out.append((n, line.split("//")[0].rstrip()))
    return out


def _replace_line(src: str, n: int, new: str) -> str:
    lines = src.splitlines()
    lines[n - 1] = new
    return "\n".join(lines) + "\n"


def _delete_line(src: str, n: int) -> str:
    lines = src.splitlines()
    del lines[n - 1]
    return "\n".join(lines) + "\n"


# (operator, pattern, replacement-or-callable, description)
_SWAPS = (
    ("relational", r"==", "!=", "inverted an equality"),
    ("relational", r"!=", "==", "inverted an inequality"),
    ("arithmetic", r"(?<![+])\+(?![+])", "-", "turned + into -"),
    ("arithmetic", r"(?<![-<>=!])-(?![->])", "+", "turned - into +"),
    ("boolean", r"(?<![&])&(?![&])", "|", "turned & into |"),
    ("boolean", r"(?<![|])\|(?![|])", "&", "turned | into &"),
    ("boolean", r"\^", "|", "turned ^ into |"),
    ("inversion", r"~(?=[\w({])", "", "dropped a bitwise inversion"),
    ("inversion", r"(?<![\w'])!(?=[\w(])", "", "dropped a logical negation"),
)


def _swap_mutants(src: str, module: str) -> List[Mutant]:
    out = []
    for n, text in _code_lines(src):
        for op, pat, repl, desc in _SWAPS:
            for m in re.finditer(pat, text):
                if re.search(r"\[[^\]]*$", text[:m.start()]):  # inside [..] -> width idiom
                    continue
                new = text[:m.start()] + repl + text[m.end():]
                out.append(Mutant(f"{op}-{n}-{m.start()}", module, op, desc, n, text, new,
                                  _replace_line(src, n, new)))
    return out


def _constant_mutants(src: str, module: str) -> List[Mutant]:
    """Codec constants: the XOR masks, the mu-law bias 33, saturation limits,
    and the case-item segment numbers."""
    out = []
    for n, text in _code_lines(src):
        for m in re.finditer(r"(\d+)'([hd])([0-9A-Fa-f]+)", text):
            width, base, val = int(m.group(1)), m.group(2), m.group(3)
            v = int(val, 16 if base == "h" else 10)
            for dv in (-1, 1):
                nv = v + dv
                if nv < 0 or nv >= (1 << width):
                    continue
                lit = f"{width}'{base}{nv:X}" if base == "h" else f"{width}'{base}{nv}"
                new = text[:m.start()] + lit + text[m.end():]
                out.append(Mutant(f"const-{n}-{m.start()}{'+' if dv > 0 else '-'}", module, "constant",
                                  f"changed {m.group(0)} to {lit}", n, text, new, _replace_line(src, n, new)))
    return out


def _slice_mutants(src: str, module: str) -> List[Mutant]:
    """Shift a part-select window by one bit — the classic segment/step slicing bug."""
    out = []
    for n, text in _code_lines(src):
        for m in re.finditer(r"(\w+)\[(\d+):(\d+)\]", text):
            hi, lo = int(m.group(2)), int(m.group(3))
            for d in (-1, 1):
                if lo + d < 0:
                    continue
                sel = f"{m.group(1)}[{hi + d}:{lo + d}]"
                new = text[:m.start()] + sel + text[m.end():]
                out.append(Mutant(f"slice-{n}-{m.start()}{'+' if d > 0 else '-'}", module, "slice-shift",
                                  f"shifted {m.group(0)} to {sel}", n, text, new, _replace_line(src, n, new)))
    return out


def _mux_mutants(src: str, module: str) -> List[Mutant]:
    """Swap the arms of `cond ? a : b` — wrong sign branch, wrong segment-0 branch."""
    out = []
    for n, text in _code_lines(src):
        m = re.search(r"=\s*([^=?;]+?)\s*\?\s*([^:;]+?)\s*:\s*([^;]+);", text)
        if m:
            new = text[:m.start(2)] + m.group(3) + " : " + m.group(2) + text[m.end(3):]
            out.append(Mutant(f"mux-{n}", module, "mux-swap", "swapped the arms of a ?:", n, text, new,
                              _replace_line(src, n, new)))
    return out


def _stuck_drop_mutants(src: str, module: str) -> List[Mutant]:
    out = []
    for n, text in _code_lines(src):
        m = re.match(r"^(\s*assign\s+(\w+)\s*=\s*).+;$", text)
        if m:
            for c in ("1'b0", "1'b1"):
                new = f"{m.group(1)}{c};"
                out.append(Mutant(f"stuck-{n}-{c[-1]}", module, "stuck-at", f"tied {m.group(2)} to {c}",
                                  n, text, new, _replace_line(src, n, new)))
        # `x <= y;` optionally guarded by `if (...)` / `else if (...)` on the same line.
        # Keep the guard so the if/else chain stays syntactically intact.
        d = re.match(r"^(\s*(?:else\s+)?if\s*\(.*\)\s*)?(\w+)\s*<=.*;$", text)
        if d:
            new = (d.group(1) + "begin end") if d.group(1) else None
            out.append(Mutant(f"drop-{n}", module, "dropped-update", f"deleted the update of {d.group(2)}",
                              n, text, new or "(deleted)",
                              _replace_line(src, n, new) if new else _delete_line(src, n)))
    return out


_GENERATORS = (_swap_mutants, _constant_mutants, _slice_mutants, _mux_mutants, _stuck_drop_mutants)


def generate(modules=gate.MODULES) -> List[Mutant]:
    muts: Dict[Tuple[str, str], Mutant] = {}
    for mod in modules:
        src = (gate.BASELINE_RTL / f"{mod}.sv").read_text()
        for g in _GENERATORS:
            for mu in g(src, mod):
                key = (mod, mu.code)
                if mu.code != src and key not in muts:  # dedupe identical programs
                    muts[key] = mu
    return sorted(muts.values(), key=lambda m: (m.module, m.operator, m.line, m.id))


def _run_one(mu: Mutant) -> dict:
    cand = BUILD / "cand" / f"{mu.module}-{mu.id}"
    shutil.rmtree(cand, ignore_errors=True)
    cand.mkdir(parents=True)
    for m in gate.MODULES:
        shutil.copy(gate.BASELINE_RTL / f"{m}.sv", cand)
    (cand / f"{mu.module}.sv").write_text(mu.code)
    proof = gate.prove(cand, name=f"{mu.module}-{mu.id}", stop_on_fail=False,
                       build_root=BUILD / "gate", archive=False)
    caught_by = [s for s in gate.GATING if proof.stages.get(s) == "fail"]
    if caught_by:
        status = "caught"
    elif proof.stages.get("equiv") == "pass":
        status = "equivalent"          # behaviour unchanged: not a detection gap
    else:
        status = "survived"
    shutil.rmtree(cand, ignore_errors=True)
    shutil.rmtree(BUILD / "gate" / proof.name, ignore_errors=True)
    return {**mu.to_dict(), "status": status, "caught_by": caught_by,
            "stages": proof.stages, "details": {s: proof.details.get(s, "") for s in caught_by}}


def run(jobs: int = 8, operators=None, modules=gate.MODULES) -> dict:
    mutants = [m for m in generate(modules) if not operators or m.operator in operators]
    t0, results = time.time(), []
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(_run_one, m) for m in mutants]
        for i, f in enumerate(as_completed(futs), 1):
            results.append(f.result())
            print(f"\r  {i}/{len(mutants)} mutants", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    results.sort(key=lambda r: (r["module"], r["operator"], r["line"], r["id"]))
    non_equiv = [r for r in results if r["status"] != "equivalent"]
    caught = [r for r in non_equiv if r["status"] == "caught"]
    matrix: Dict[str, int] = {s: 0 for s in gate.GATING}
    only: Dict[str, int] = {s: 0 for s in gate.GATING}
    for r in caught:
        for s in r["caught_by"]:
            matrix[s] += 1
        if len(r["caught_by"]) == 1:
            only[r["caught_by"][0]] += 1
    by_op: Dict[str, Dict[str, int]] = {}
    for r in results:
        by_op.setdefault(r["operator"], {"caught": 0, "survived": 0, "equivalent": 0})[r["status"]] += 1
    return {
        "total": len(results), "equivalent": len(results) - len(non_equiv),
        "caught": len(caught), "survived": len(non_equiv) - len(caught),
        "score_pct": round(100 * len(caught) / len(non_equiv), 1) if non_equiv else None,
        "caught_by_stage": matrix, "caught_only_by_stage": only, "by_operator": by_op,
        "elapsed_s": round(time.time() - t0, 1),
        "survivors": [r for r in results if r["status"] == "survived"],
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-j", "--jobs", type=int, default=8)
    ap.add_argument("--operator", action="append")
    ap.add_argument("--module", action="append")
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    modules = tuple(args.module) if args.module else gate.MODULES
    if args.preview:
        muts = [m for m in generate(modules) if not args.operator or m.operator in args.operator]
        by: Dict[str, int] = {}
        for m in muts:
            by[f"{m.module}/{m.operator}"] = by.get(f"{m.module}/{m.operator}", 0) + 1
        print(json.dumps({"total": len(muts), "by_module_operator": by}, indent=2))
        return 0
    # A generator that silently matches nothing inflates the score (it happened: an RTL
    # restyle made `dropped-update` produce 0 mutants while the campaign reported 100%).
    expected = set(args.operator) if args.operator else {
        "relational", "arithmetic", "boolean", "inversion", "constant",
        "slice-shift", "mux-swap", "stuck-at", "dropped-update"}
    produced = {m.operator for m in generate(modules)}
    if expected - produced:
        print(f"ERROR: no mutants generated for operator(s): {sorted(expected - produced)}", file=sys.stderr)
        return 2
    rep = run(args.jobs, args.operator, modules)
    gate.RUNS.mkdir(exist_ok=True)
    out = gate.RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-mutate.json"
    out.write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in rep.items() if k not in ("results",)}, indent=2, ensure_ascii=False))
    print(f"report: {out.relative_to(gate.ROOT)}")
    return 0 if rep["survived"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
