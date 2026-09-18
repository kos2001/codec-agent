# CLAUDE.md

Guidance for Claude Code in this repository.

## What this is

A spec → RTL service, piloted on **ITU-T G.711** (A-law / μ-law, encode + decode).
The deliverable is **evidence**, not RTL: a candidate is only accepted when a
tool-driven gate says `PROVEN`. No LLM decides a verdict. Plan:
`claudedocs/spec-to-rtl-service-plan.md`. Phase 0 results: `claudedocs/phase0-report.md`.

Verification approach adapted from the sibling `../hls-rtl` (gamma workbench):
golden-model oracle, stage gate with pass/fail/skip, formal handshake + datapath
properties, mutation coverage. Port ideas, do not import code from it.

## Layout

```
third_party/stl/             ITU-T G.191 STL (git submodule, READ-ONLY) — reference C + official vectors
golden/g711/spec_model.py    model written from the G.711 table formulas (NOT from STL)
golden/g711/crosscheck.py    spec model vs STL, full input space; also writes stl_out/codes.*lin
golden/g711/gen_vectors.py   writes generated/g711/tb/vectors/* and tests.txt
generated/g711/requirements.yaml   REQ-IDs; RTL comments cite them
generated/g711/rtl/          g711_{alaw,ulaw}_{enc,dec}.sv — SystemVerilog, valid/ready, 1 register stage
generated/g711/tb/           tb_g711.sv + run_all.sh (iverilog, 4-state)
generated/g711/formal/       g711_props.sv + g711.sby (SymbiYosys, z3)
pipeline/gate.py             the gate: lint → synthesize → simulate → formal (+ equiv, info)
pipeline/mutate.py           mutation campaign through the gate
pipeline/generate.py         Hermes generation loop: prompt → gate → evidence → repair (PROVEN/EXHAUSTED/ERRORED)
pipeline/hermes_client.py    stdlib client for the codecagent Hermes API server (.env: HERMES_*)
pipeline/generate_tb.py      Hermes writes a self-checking testbench; judged for soundness (PASS on PROVEN RTL)
                             and sensitivity (mutant kill rate): ACCEPTED/WEAK/EXHAUSTED/ERRORED
pipeline/waves.py            trace any testbench run into build/waves/*.vcd (window by clock edges) + VCD→JSON
api/server.py                FastAPI workbench backend (strips /api, serves frontend/dist)
frontend/                    React 19 + Vite workbench, adapted from hls-rtl's EDA-style UI
scripts/hermes_server.sh     start|stop|status the Hermes API server (profile codecagent, :8659)
scripts/workbench.sh         build + serve the workbench on :8013 (--dev: Vite on :5175)
specs/g711/                  G.711 (11/88) PDF + extracted text (tables are images, see rules)
tests/                       L0 pytest: oracle vs STL, oracle vs PDF tables
runs/                        archived gate / mutation JSON
```

## Commands

```sh
python3 -m venv .venv && .venv/bin/pip install pytest click   # click: sby needs it
cmake -S third_party/stl -B third_party/stl/build -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build third_party/stl/build --target g711demo cf

python3 golden/g711/crosscheck.py      # oracle vs STL (run before gen_vectors)
python3 golden/g711/gen_vectors.py     # regenerate TB vectors
.venv/bin/python -m pytest tests/ -q   # L0
python3 pipeline/gate.py               # gate the committed RTL (~5s)
python3 pipeline/gate.py --rtl-dir <candidate_dir> --no-stop
python3 pipeline/mutate.py --preview   # mutant set without running
python3 pipeline/mutate.py -j 8        # full campaign (~3 min)

scripts/hermes_server.sh start          # Hermes API server, profile codecagent, 127.0.0.1:8659
python3 pipeline/generate.py --health  # authenticated check: key works, model listed
python3 pipeline/generate.py --module g711_ulaw_enc --max-iters 3
python3 pipeline/generate_tb.py --module g711_ulaw_enc          # or --check <tb.sv> (no LLM)
python3 pipeline/waves.py --test L1_ulaw_enc [--rtl-dir <cand>] # or --tb <tb.sv> --module <m>
scripts/workbench.sh                   # http://127.0.0.1:8013
```

## Hermes API server (generation backend)

- Profile `~/.hermes/profiles/codecagent` (cloned from `rtlgateway`), `platforms.api_server` only,
  port 8659, model name `codec-agent`, `platform_toolsets.api_server: []` (generation is one turn;
  tools cost ~9.6k prompt tokens per call). Role prompt: the profile's `SOUL.md`.
- Upstream LLM = `model.default` in that config.yaml (currently OpenRouter
  `deepseek/deepseek-v4.1-flash`). Change it there and restart; `generate.py` records it per run.
- Key: the profile `.env` `API_SERVER_KEY` (overrides config.yaml token) = repo `.env`
  `HERMES_API_KEY`. Both git-ignored; never print or commit them.
- Restarting with `--replace` drains in-flight work: requests get 503 `gateway_draining` until the
  old process exits. Upstream failures come back as HTTP 200 with `finish_reason: error` —
  `hermes_client` raises on those (seen: openai-codex 429 quota).

## Rules learned the hard way (each one bit during Phase 0)

- **A skipped stage is missing evidence, not a pass.** Any gating skip ⇒ `INCONCLUSIVE`.
- **Round-trip tests cannot validate a codec.** The first μ-law model inverted the sign
  bit in both encoder and decoder; encode→decode still matched STL 65536/65536. Only
  the one-direction comparisons caught it. Keep L1 enc and dec vectors separate.
- **A passing suite must be shown to fail.** Before trusting a new check, break the RTL
  and watch it go red. The first mutation harness reported "0 detected" for every
  mutant because `head -4` cut the `RESULT` line — a harness bug, not a clean design.
- **Reset must be tested against a hostile environment.** Releasing reset on a posedge,
  with `in_valid=0`/`out_ready=1` during reset, let a design with no `out_valid` reset
  pass all simulation. The TB now drives garbage (`in_valid=1`, `out_ready=0`) during
  reset and releases on a negedge; formal `A6_reset_empties` covers it independently.
- **Watch shift-amount widths.** `3'd2 + seg` is 3 bits: segments 6/7 wrap to 0/1.
- **Formal properties speak about ports only.** A hierarchical reference in yosys becomes
  an undriven wire — a free variable that proves nothing while appearing to run.
- **`equiv_make`/`equiv_induct` is not enough** to classify equivalent mutants; the gate
  uses `miter` + `sat -tempinduct`.
- **Datapath registers are not reset** (only `out_valid`, REQ-IF-04). A data reset value is
  unobservable, so mutating it only produces un-killable mutants.
- **Candidates get isolated build dirs** (`RTL_DIR`, `BUILD` for run_all.sh; `build/gate/<name>`),
  so parallel mutants never share artifacts.
- ITU blocks plain HTTP clients (curl gets "Request Rejected" / 500). A real browser session works:
  load the Recommendation page, then `fetch()` the `dologin_pub.asp ... !!PDF-E` URL in-page.
- **G.711 Tables 1/2 are images in the PDF.** Text extraction returns the prose and silently
  drops the tables. Render the pages and read them; `tests/test_spec_tables.py` is the
  machine-checkable transcription (rebuilt from columns 2-3 and Notes 2-3 only).
- G.711 §3.6 leaves inputs exactly on a decision value to the implementation. REQ-A-02/U-02 are
  a DESIGN choice (STL convention), not spec text — do not "fix" them toward the PDF.
- STL's CTest assumes an in-source working dir; run `g711demo` directly (see crosscheck.py).
- **State the gate's lint contract in the generation prompt.** The first deepseek run produced
  candidates that passed all 16 simulation tests and all 8 formal tasks on iteration 1 and were
  still rejected — by `verilator -Wall` width/unused warnings the prompt never mentioned.
- **A fault only one stage can see is a fragile check.** Track `caught_only_by_stage` in the mutation
  report: it was 4 (synthesize) for async reset until `tb_g711` learned the mid-cycle reset check; it is 0 now.
- **A PROVEN verdict is only as strong as the gate that issued it.** Hermes produced μ-law
  candidates with `always_ff @(posedge clk or negedge rst_n)` that passed lint, 4-state sim and
  unbounded formal — all sample reset at clock edges. REQ-IF-04 (synchronous reset) is now a
  netlist check in `synthesize` (no `$adff`/`$aldff`/`$dffsr` after `proc`), with a `reset-style`
  mutation operator proving it. Archived runs from the weaker gate carry a `regate` field.
- **A tool error is not a verdict.** `equiv` reported "differs" when yosys simply could not model
  an async FF. It now distinguishes `proof did fail` (differs) from other errors (skip).
- **Compare data only where it is defined.** `equiv` masks output data with `out_valid`; a design
  that differs only in don't-care data (e.g. data register not updated during reset) is equivalent.
- **Waveform windows are counted in clock edges, not time.** Generated testbenches often omit
  `timescale; a 10 ns assumption produced an empty trace. `wave_dump` is a separate root
  (`iverilog -s <tb> -s wave_dump`) so no testbench is ever edited to get a waveform.
- **Parse the field you mean.** `t=(\d+)` matched the `t=` inside `got=0001`; anchor trailing fields.
- **Long generations need a long client timeout.** With the strengthened testbench contract
  deepseek took 13 minutes for one reply; `HERMES_TIMEOUT` (repo `.env`, default 600 s) is the
  limit, and a timeout is reported as `ERRORED`, not as a bad testbench.
- **The generation prompt must never contain the committed implementation** (only requirements,
  port contract, conventions); `tests/test_generate.py` enforces it, or PROVEN means copying.

## Conventions

- RTL: SystemVerilog, `always_comb` / `always_ff`, synchronous active-low reset, one
  registered stage, `in_ready = ~out_valid | out_ready`. Cite REQ-IDs in comments.
- A requirement change edits `requirements.yaml` first, then `spec_model.py`, then the
  formal reference functions, then RTL — and all of it re-proves through the gate.
- Never edit `third_party/stl`.
