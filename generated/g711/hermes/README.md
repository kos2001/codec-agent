# Hermes-generated artifacts (not the design of record)

Produced by the `codecagent` Hermes API server (OpenRouter `deepseek/deepseek-v4.1-flash`) from
`requirements.yaml` + the port contract only. The committed design remains `../rtl/`.

| file | source run (`runs/`) | verdict |
|---|---|---|
| rtl/g711_alaw_enc.sv | 20260917-232539-g711_alaw_enc | gate PROVEN, iter 1 |
| rtl/g711_alaw_dec.sv | 20260917-233017-g711_alaw_dec | gate PROVEN, iter 2 |
| rtl/g711_ulaw_enc.sv | 20260917-234157-g711_ulaw_enc | gate PROVEN, iter 2 (after the REQ-IF-04 check) |
| rtl/g711_ulaw_dec.sv | 20260917-234230-g711_ulaw_dec | gate PROVEN, iter 2 (after the REQ-IF-04 check) |
| tb/tb_g711_ulaw_enc.sv | 20260917-233054-tb-g711_ulaw_enc | ACCEPTED, 147,635 checks |
| tb/tb_g711_alaw_dec.sv | 20260917-235146-tb-g711_alaw_dec | ACCEPTED, 26/28 mutants |
| tb/tb_g711_ulaw_dec.sv | 20260917-235823-tb-g711_ulaw_dec | ACCEPTED, 23/24 mutants |
| tb/tb_g711_alaw_enc.sv | 20260918-000601-tb-g711_alaw_enc | ACCEPTED, 60/61 mutants |

Re-check any of them without the LLM:

```sh
python3 pipeline/gate.py --rtl-dir <dir with all four modules>
python3 pipeline/generate_tb.py --check generated/g711/hermes/tb/tb_g711_alaw_enc.sv --module g711_alaw_enc
python3 pipeline/waves.py --tb generated/g711/hermes/tb/tb_g711_alaw_enc.sv --module g711_alaw_enc
```
