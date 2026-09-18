# codec-agent

Spec → RTL for codec standards, where the deliverable is **evidence**, not RTL.
A generated design is accepted only when a tool-driven gate — no LLM in the verdict —
says `PROVEN`.

Pilot: **ITU-T G.711** (A-law / μ-law, encoder + decoder), SystemVerilog, valid/ready.

```
spec (G.711 PDF) ─► requirements.yaml ─► RTL
                          │
   oracles: ITU-T G.191 STL (C)  ·  spec_model.py (from the tables)  ·  PDF tables (tests)
                          │
   gate:   lint ─► synthesize ─► simulate (4-state, STL vectors) ─► formal (unbounded)
                          │
   PROVEN / REJECTED / INCONCLUSIVE   (+ failure evidence, SHA-256, mutation coverage)
```

## Status (Phase 0)

| | |
|---|---|
| Gate | `PROVEN` — lint clean, 16/16 sim tests, 8/8 formal tasks (k-induction) |
| Oracle vs STL | bit-exact on all 65536 inputs / 256 codes |
| Oracle vs G.711 PDF | Tables 1a/1b/2a/2b rebuilt from printed columns: all 256 decoder codes, all encoder interval interiors |
| Mutation coverage | 180/180 non-equivalent mutants caught, and no stage is the only one that catches a fault |
| Hermes-generated RTL | all 4 modules PROVEN (deepseek-v4.1-flash; 1–2 iterations with gate evidence) |
| Hermes-generated testbenches | all 4 ACCEPTED: sound on PROVEN RTL, **100% mutant kill**, no blind fault class |

Reports: [`claudedocs/phase0-report.md`](claudedocs/phase0-report.md) ·
plan: [`claudedocs/spec-to-rtl-service-plan.md`](claudedocs/spec-to-rtl-service-plan.md) ·
working notes and hard-won rules: [`CLAUDE.md`](CLAUDE.md)

Verification approach and workbench UI adapted from [hls-rtl](https://github.com/kos2001/hls-rtl).

## Generation (Hermes) and workbench

RTL generation goes through a local **Hermes Agent API server** (OpenAI-compatible, profile
`codecagent`, port 8659, upstream LLM configurable — currently `deepseek/deepseek-v4.1-flash`).
`pipeline/generate.py` sends requirements + the port contract (never the committed RTL), gates
every candidate, and feeds the gate's failure evidence back for repair.

```sh
scripts/hermes_server.sh start                       # needs a hermes profile named codecagent
printf 'HERMES_API_KEY=...\n' > .env                  # that profile's API_SERVER_KEY
python3 pipeline/generate.py --health
python3 pipeline/generate.py --module g711_ulaw_enc
python3 pipeline/generate_tb.py --module g711_ulaw_enc   # Hermes testbench, judged by soundness + mutant kill rate
python3 pipeline/waves.py --test L1_ulaw_enc             # VCD of a testbench run (also in the Waveform tab)
.venv/bin/pip install fastapi uvicorn && scripts/workbench.sh   # http://127.0.0.1:8013
```

## Run

Requires iverilog, verilator, yosys, SymbiYosys + z3, cmake, Python 3.

```sh
git clone --recursive https://github.com/kos2001/codec-agent && cd codec-agent
python3 -m venv .venv && .venv/bin/pip install pytest click pyyaml
cmake -S third_party/stl -B third_party/stl/build -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build third_party/stl/build --target g711demo cf
python3 golden/g711/crosscheck.py

.venv/bin/python -m pytest tests/ -q     # oracle checks
python3 pipeline/gate.py                 # sign-off gate (~5 s)
python3 pipeline/mutate.py -j 8          # mutation campaign (~3 min)
```

The G.711 PDF is not redistributed — see [`specs/g711/README.md`](specs/g711/README.md).
