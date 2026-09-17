"""codec-agent workbench api-server.

Adapted from hls-rtl api/server.py. A thin FastAPI layer over the pipeline — it
adds no verification logic of its own, so the UI shows exactly what the CLI
(`pipeline/gate.py`, `mutate.py`, `generate.py`) would print.

The SPA calls everything under `/api/*`; the prefix is stripped in-process so the
same client works behind the Vite dev proxy and when this server serves
`frontend/dist` directly.

Long flows (generation through Hermes, the mutation campaign) run as background
jobs: POST returns a job id, GET /jobs/{id} polls it. The gate (~5 s) and the L0
tests (<1 s) run inline.

Run:  .venv/bin/uvicorn api.server:app --host 127.0.0.1 --port 8013
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import gate  # noqa: E402
import generate as gen  # noqa: E402
import generate_tb  # noqa: E402
import waves  # noqa: E402
from hermes_client import HermesClient, upstream_model  # noqa: E402

app = FastAPI(title="codec-agent workbench", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5175", "http://localhost:5175"],
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def _strip_api_prefix(request, call_next):
    path = request.scope.get("path", "")
    if path == "/api":
        request.scope["path"] = "/"
    elif path.startswith("/api/"):
        request.scope["path"] = path[4:]
    return await call_next(request)


# ------------------------------------------------------------------ helpers ---

def _tool(name: str) -> bool:
    return shutil.which(name) is not None


def _runs(kind: str) -> List[Path]:
    return sorted(gate.RUNS.glob(f"*-{kind}.json") if kind != "gate" else gate.RUNS.glob("*-gate-*.json"),
                  reverse=True)


def _load(p: Path) -> dict:
    return json.loads(p.read_text())


def _safe_under(base: Path, rel: str) -> Path:
    p = (ROOT / rel).resolve()
    if base.resolve() not in p.parents and p != base.resolve():
        raise HTTPException(400, "path outside allowed directory")
    if not p.exists():
        raise HTTPException(404, f"{rel} not found")
    return p


# --------------------------------------------------------------------- jobs ---

JOBS: Dict[str, Dict[str, Any]] = {}
_JOB_LOCK = threading.Lock()


def _start_job(kind: str, params: dict, fn) -> dict:
    with _JOB_LOCK:
        running = [j for j in JOBS.values() if j["kind"] == kind and j["status"] == "running"]
        if running:
            raise HTTPException(409, f"a {kind} job is already running ({running[0]['id']})")
        job = {"id": uuid.uuid4().hex[:12], "kind": kind, "params": params, "status": "running",
               "started": time.time(), "finished": None, "result": None, "error": None}
        JOBS[job["id"]] = job

    def work():
        try:
            job["result"] = fn()
            job["status"] = "done"
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI verbatim
            job["error"], job["status"] = f"{type(exc).__name__}: {exc}", "error"
        job["finished"] = time.time()

    threading.Thread(target=work, daemon=True).start()
    return job


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    if job_id not in JOBS:
        raise HTTPException(404, "unknown job")
    return JOBS[job_id]


@app.get("/jobs")
def jobs() -> List[dict]:
    return sorted(({k: v for k, v in j.items() if k != "result"} for j in JOBS.values()),
                  key=lambda j: -j["started"])


# ---------------------------------------------------------------- endpoints ---

@app.get("/health")
def health() -> dict:
    h = HermesClient().health()
    return {
        "ok": True,
        "tools": {t: _tool(t) for t in ("verilator", "iverilog", "yosys", "sby", "z3")},
        "stl_built": (ROOT / "third_party/stl/build/bin/g711demo").exists(),
        "spec_pdf": (ROOT / "specs/g711/T-REC-G.711-198811-I-PDF-E.pdf").exists(),
        "hermes": h,
        "hermes_model": upstream_model(),
    }


@app.get("/design")
def design() -> dict:
    mods = []
    for m in gate.MODULES:
        src = (gate.BASELINE_RTL / f"{m}.sv").read_text()
        mods.append({"name": m, "role": gen.ROLE[m], "lines": src.count("\n"),
                     "ports": gen.port_contract(m), "reqs": sorted(set(re.findall(r"REQ-[A-Z]+-\d+", src)))})
    return {"modules": mods}


@app.get("/rtl/{module}")
def rtl(module: str) -> dict:
    if module not in gate.MODULES:
        raise HTTPException(404, "unknown module")
    return {"module": module, "path": f"generated/g711/rtl/{module}.sv",
            "source": (gate.BASELINE_RTL / f"{module}.sv").read_text()}


@app.get("/requirements")
def requirements() -> dict:
    items = []
    section = None
    for line in gen.REQS.read_text().splitlines():
        top = re.match(r"^(\w+):", line)
        if top:
            section = top.group(1)
        m = re.match(r"^\s+(REQ-[A-Z]+-\d+):\s*(.*)$", line)
        if m:
            text, _, source = m.group(2).partition("  # source:")
            items.append({"id": m.group(1), "section": section, "text": text.strip().strip('"'),
                          "source": source.strip() or None,
                          "design_choice": source.strip().startswith("DESIGN")})
    excl = re.findall(r"^\s+- (.*)$", gen.REQS.read_text().split("interface:")[0], re.M)
    return {"items": items, "exclude": [e.strip('"') for e in excl]}


class GateReq(BaseModel):
    candidate: Optional[str] = None     # repo-relative candidate .sv under build/generate


@app.post("/gate")
def run_gate(req: GateReq) -> dict:
    if req.candidate:
        cand = _safe_under(ROOT / "build" / "generate", req.candidate)
        proof = gate.prove(cand.parent, name=f"ui-{cand.parent.parent.name}-{cand.parent.name}",
                           stop_on_fail=False)
    else:
        proof = gate.prove(stop_on_fail=False)
    return proof.to_dict()


@app.get("/gate/latest")
def gate_latest() -> dict:
    runs = [p for p in _runs("gate") if "-gate-baseline" in p.name or "-gate-ui-" not in p.name]
    if not runs:
        raise HTTPException(404, "no gate run archived yet")
    return {"file": runs[0].name, **_load(runs[0])}


@app.post("/tests")
def run_tests() -> dict:
    py = ROOT / ".venv/bin/python"
    xml = ROOT / "build" / "pytest.xml"
    xml.parent.mkdir(exist_ok=True)
    t0 = time.time()
    p = subprocess.run([str(py if py.exists() else sys.executable), "-m", "pytest", "tests", "-q",
                        f"--junitxml={xml}"], cwd=ROOT, capture_output=True, text=True, timeout=300)
    cases = []
    if xml.exists():
        text = xml.read_text()
        for m in re.finditer(r'<testcase classname="([^"]+)" name="([^"]+)"[^>]*?(/>|>(.*?)</testcase>)', text, re.S):
            body = m.group(4) or ""
            status = "fail" if "<failure" in body or "<error" in body else "skip" if "<skipped" in body else "pass"
            reason = re.search(r'message="([^"]*)"', body)
            cases.append({"file": m.group(1).split(".")[-1], "name": m.group(2), "status": status,
                          "message": reason.group(1) if reason else ""})
    return {"returncode": p.returncode, "elapsed_s": round(time.time() - t0, 2),
            "summary": (p.stdout.strip().splitlines() or [""])[-1], "cases": cases}


@app.get("/mutation/latest")
def mutation_latest() -> dict:
    full = [p for p in _runs("mutate") if _load(p).get("total", 0) >= 100]
    if not full:
        raise HTTPException(404, "no full mutation campaign archived")
    return {"file": full[0].name, **_load(full[0])}


class MutateReq(BaseModel):
    jobs: int = 8
    operator: Optional[str] = None


@app.post("/mutation/run")
def mutation_run(req: MutateReq) -> dict:
    def work():
        before = set(p.name for p in _runs("mutate"))
        cmd = [sys.executable, str(ROOT / "pipeline/mutate.py"), "-j", str(req.jobs)]
        if req.operator:
            cmd += ["--operator", req.operator]
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=3600)
        new = [x for x in _runs("mutate") if x.name not in before]
        if not new:
            raise RuntimeError((p.stderr or p.stdout)[-800:])
        return {"file": new[0].name, **_load(new[0])}
    return _start_job("mutate", req.model_dump(), work)


class GenerateReq(BaseModel):
    module: str
    max_iters: int = 3


@app.post("/generate")
def generate_start(req: GenerateReq) -> dict:
    if req.module not in gate.MODULES:
        raise HTTPException(400, "unknown module")
    if not 1 <= req.max_iters <= 6:
        raise HTTPException(400, "max_iters must be 1..6")
    return _start_job("generate", req.model_dump(), lambda: gen.run(req.module, max_iters=req.max_iters))


class GenerateTbReq(BaseModel):
    module: str
    max_iters: int = 3
    min_kill: float = 90.0


@app.post("/generate_tb")
def generate_tb_start(req: GenerateTbReq) -> dict:
    if req.module not in gate.MODULES:
        raise HTTPException(400, "unknown module")
    if not 1 <= req.max_iters <= 6:
        raise HTTPException(400, "max_iters must be 1..6")
    return _start_job("generate_tb", req.model_dump(),
                      lambda: generate_tb.run(req.module, max_iters=req.max_iters, min_kill=req.min_kill))


@app.get("/generate/prompt/{module}")
def generate_prompt(module: str) -> dict:
    if module not in gate.MODULES:
        raise HTTPException(404, "unknown module")
    return {"module": module, "prompt": gen.build_prompt(module), "tb_prompt": generate_tb.build_prompt(module)}


@app.get("/generate/runs")
def generate_runs() -> List[dict]:
    out = []
    for p in _runs("generate"):
        d = _load(p)
        out.append({"file": p.name, "run_id": d["run_id"], "module": d["module"], "outcome": d["outcome"],
                    "kind": d.get("kind", "rtl"), "kill_pct": d.get("kill_pct"),
                    "regate": (d.get("regate") or {}).get("verdict"),
                    "iterations": d["iterations"], "equivalent_to_baseline": d.get("equivalent_to_baseline"),
                    "model": d.get("model")})
    return out


@app.get("/generate/runs/{name}")
def generate_run(name: str) -> dict:
    p = gate.RUNS / name
    if not re.fullmatch(r"[\w.-]+-generate\.json", name) or not p.exists():
        raise HTTPException(404, "unknown run")
    return _load(p)


@app.get("/candidate")
def candidate(path: str) -> dict:
    try:
        p = _safe_under(ROOT / "build" / "generate", path)
    except HTTPException:
        p = _safe_under(ROOT / "build" / "generate_tb", path)
    return {"path": path, "source": p.read_text()}


# ---------------------------------------------------------------- waveforms ---

@app.get("/waves/tests")
def waves_tests() -> List[dict]:
    return waves.list_tests()


@app.get("/waves/captures")
def waves_captures() -> List[dict]:
    return waves.list_captures()


class WaveCaptureReq(BaseModel):
    test: Optional[str] = None          # a gate test (tests.txt), with the committed RTL or `candidate`
    candidate: Optional[str] = None     # repo-relative candidate .sv under build/generate
    testbench: Optional[str] = None     # repo-relative generated testbench under build/generate_tb
    module: Optional[str] = None
    cycles: int = 200


@app.post("/waves/capture")
def waves_capture(req: WaveCaptureReq) -> dict:
    if not 20 <= req.cycles <= 5000:
        raise HTTPException(400, "cycles must be 20..5000")
    if req.testbench:
        if req.module not in gate.MODULES:
            raise HTTPException(400, "module required for a testbench capture")
        tb = _safe_under(ROOT / "build" / "generate_tb", req.testbench)
        return waves.capture_testbench(tb, req.module, cycles=req.cycles)
    if not req.test:
        raise HTTPException(400, "test or testbench required")
    rtl_dir = _safe_under(ROOT / "build" / "generate", req.candidate).parent if req.candidate else gate.BASELINE_RTL
    return waves.capture_gate_test(req.test, rtl_dir, cycles=req.cycles)


@app.get("/waves/{name}")
def waves_extract(name: str, scope: Optional[str] = None) -> dict:
    return waves.extract(name, scope=scope)


DOCS = {"README": "README.md", "PLAN": "claudedocs/spec-to-rtl-service-plan.md",
        "PHASE0": "claudedocs/phase0-report.md", "CLAUDE": "CLAUDE.md"}


@app.get("/doc/{name}")
def doc(name: str) -> dict:
    if name not in DOCS:
        raise HTTPException(404, "unknown document")
    return {"name": name, "path": DOCS[name], "markdown": (ROOT / DOCS[name]).read_text()}


_DIST = ROOT / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
