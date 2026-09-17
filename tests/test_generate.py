"""L0: the generation loop's deterministic parts (no Hermes server needed)."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import gate  # noqa: E402
import generate as gen  # noqa: E402
import hermes_client  # noqa: E402


@pytest.mark.parametrize("module", gate.MODULES)
def test_prompt_never_contains_the_committed_implementation(module):
    """A PROVEN generation is only meaningful if the model could not copy the answer."""
    prompt = gen.build_prompt(module)
    body = (gate.BASELINE_RTL / f"{module}.sv").read_text().split(");", 1)[1]
    impl_lines = [l.strip() for l in body.splitlines()
                  if l.strip() and not l.strip().startswith("//") and len(l.strip()) > 12]
    leaked = [l for l in impl_lines if l in prompt]
    assert not leaked, leaked[:3]
    # Generic conventions (always_comb, the lint_off idiom) are part of the contract on
    # purpose; design-specific constructs of the committed RTL are not.
    for token in ("casez", "biased", "13'h1FFF", "mag[", "seg <<", "{sign, ~seg"):
        assert token not in prompt, token


@pytest.mark.parametrize("module", gate.MODULES)
def test_prompt_carries_the_contract(module):
    prompt = gen.build_prompt(module)
    law = "REQ-A-" if "alaw" in module else "REQ-U-"
    other = "REQ-U-" if "alaw" in module else "REQ-A-"
    assert f"module {module} (" in prompt
    assert law + "01" in prompt and other + "01" not in prompt
    assert "REQ-IF-04" in prompt
    ports = re.findall(r"\b(input|output)\s+logic", gen.port_contract(module))
    assert len(ports) == 8


def test_extract_module_takes_the_named_module_from_a_fenced_block():
    reply = ("Here you go.\n```systemverilog\nmodule helper; endmodule\n"
             "module g711_alaw_enc (input logic clk);\n  // body\nendmodule\n```\ntrailing prose")
    code = gen.extract_module(reply, "g711_alaw_enc")
    assert code.startswith("module g711_alaw_enc") and code.rstrip().endswith("endmodule")
    assert "helper" not in code and "prose" not in code


def test_extract_module_returns_none_without_a_complete_module():
    assert gen.extract_module("```systemverilog\nmodule g711_alaw_enc (\n```", "g711_alaw_enc") is None
    assert gen.extract_module("no code at all", "g711_ulaw_dec") is None


def test_repair_prompt_includes_evidence_and_previous_code():
    p = gen.build_repair_prompt("g711_ulaw_enc", "module g711_ulaw_enc(); endmodule",
                                "L1_ulaw_enc: ERROR idx=0 stim=8000 got=0080 exp=0000", 1)
    assert "stim=8000 got=0080 exp=0000" in p
    assert "module g711_ulaw_enc(); endmodule" in p
    assert "REJECTED BY THE SIGN-OFF GATE" in p


def test_client_never_reads_a_key_from_committed_files():
    """The API key lives only in the git-ignored .env or the environment."""
    tracked = (ROOT / ".gitignore").read_text()
    assert re.search(r"^\.env$", tracked, re.M)
    src = (ROOT / "pipeline" / "hermes_client.py").read_text()
    assert not re.search(r"hak-codec-[0-9a-f]{8,}", src)


def test_chat_reports_upstream_agent_failure_as_error(monkeypatch):
    """hermes returns upstream failures as HTTP 200 with finish_reason=error (seen: 429 quota)."""
    c = hermes_client.HermesClient(hermes_client.Settings("http://x", "k", "codec-agent", 5))
    failed = {"choices": [{"message": {"content": "API call failed"}, "finish_reason": "error"}],
              "hermes": {"failed": True, "error": "HTTP 400: bad request"}}
    monkeypatch.setattr(c, "_request", lambda *a, **k: failed)
    with pytest.raises(hermes_client.HermesError, match="agent error"):
        c.chat("hi", retries=1)


def test_generation_run_archives_have_a_known_outcome():
    for p in gate.RUNS.glob("*-generate.json"):
        d = json.loads(p.read_text())
        if d.get("kind") == "testbench":
            assert d["outcome"] in ("ACCEPTED", "WEAK", "EXHAUSTED", "ERRORED"), p.name
            if d["outcome"] == "ACCEPTED":
                assert d["kill_pct"] >= d["min_kill_pct"], p.name
            continue
        assert d["outcome"] in ("PROVEN", "EXHAUSTED", "ERRORED"), p.name
        if d["outcome"] == "PROVEN":
            last = d["attempts"][-1]
            assert last["verdict"] == "PROVEN" and all(
                last["stages"][s] == "pass" for s in gate.GATING), p.name


# ------------------------------------------------------------ testbench generation ---
import shutil as _shutil  # noqa: E402

import generate_tb  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
needs_iverilog = pytest.mark.skipif(not _shutil.which("iverilog"), reason="iverilog absent: missing evidence")


@pytest.mark.parametrize("module", gate.MODULES)
def test_tb_prompt_gives_no_expected_values_or_implementation(module):
    p = generate_tb.build_prompt(module)
    assert "RESULT PASS checks=%0d" in p and "ERROR stim=%h got=%h exp=%h" in p
    body = (gate.BASELINE_RTL / f"{module}.sv").read_text().split(");", 1)[1]
    assert not [l.strip() for l in body.splitlines() if len(l.strip()) > 12 and l.strip() in p]
    assert not (gate.G711 / "tb" / "vectors").exists() or "vectors/" not in p


@needs_iverilog
def test_tb_checker_rejects_a_vacuous_testbench(tmp_path):
    """A testbench that always prints PASS is sound on good RTL — and must score 0."""
    r = generate_tb.check(FIX / "tb_g711_ulaw_enc_vacuous.sv", "g711_ulaw_enc", tmp_path, 90.0)
    assert r["sound"] and r["verdict"] == "WEAK" and r["kill"]["kill_pct"] == 0.0


@needs_iverilog
def test_tb_soundness_evidence_names_the_authoritative_value():
    run = {"compiled": True, "timeout": False, "result": "FAIL", "log": "",
           "errors": [("8000", "00", "80")], "other_errors": []}
    ev = generate_tb.soundness_evidence("g711_ulaw_enc", run)
    assert "authoritative value is 00" in ev   # -32768 encodes to 0x00 (STL)
