"""L0: the self-improvement loop's diagnosis and its regression gate.

These run without tools or an LLM: `recommend` and `regression` are pure functions over a
snapshot, which is what makes the loop safe to run unattended.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import improve  # noqa: E402
import kb as kbmod  # noqa: E402


def snap(**over):
    base = {
        "at": "2026-09-18T00:00:00",
        "gate": {"verdict": "PROVEN", "stages": {"lint": "pass", "synthesize": "pass",
                                                 "simulate": "pass", "formal": "pass"},
                 "tests_pass": 16, "tests_total": 16},
        "l0": {"ok": True, "summary": "51 passed"},
        "mutation": {"total": 181, "caught": 180, "survived": 0, "score_pct": 100.0,
                     "sole_detector": 0, "caught_only_by_stage": {}},
        "artifacts": {"rtl": {m: {"run": "r", "outcome": "PROVEN", "iterations": 1, "equiv": True,
                                  "regate": None} for m in improve.gate.MODULES},
                      "testbench": {m: {"run": "r", "outcome": "ACCEPTED", "iterations": 1,
                                        "kill_pct": 100.0, "blind_classes": []}
                                    for m in improve.gate.MODULES}},
        "kb": {"cases": 30, "eval": {"answerable": 7, "P@1": 0.9}, "experiment": {"summary": {}}},
    }
    base.update(over)
    return base


def ids(recs):
    return [r["id"] for r in recs]


def test_a_healthy_snapshot_reports_no_gap(monkeypatch):
    monkeypatch.setattr(kbmod, "build", lambda write=True: {"cases": [0] * 30})
    assert recs_of(snap(), monkeypatch) == []


def recs_of(s, monkeypatch):
    monkeypatch.setattr(kbmod, "build", lambda write=True: {"cases": [0] * s["kb"]["cases"]})
    return improve.recommend(s)


def test_a_skipped_gate_stage_is_a_gap_not_a_pass(monkeypatch):
    s = snap()
    s["gate"]["stages"]["formal"] = "skip"
    r = recs_of(s, monkeypatch)
    assert "gate.skip.formal" in ids(r) and r[0]["priority"] == 1


def test_surviving_mutants_and_sole_detectors_are_ranked(monkeypatch):
    s = snap()
    s["mutation"].update(survived=2, sole_detector=4, caught_only_by_stage={"synthesize": 4})
    r = recs_of(s, monkeypatch)
    assert ids(r)[:2] == ["mutation.survivors", "mutation.sole_detector"]


def test_blind_testbench_outranks_a_merely_imperfect_one(monkeypatch):
    s = snap()
    s["artifacts"]["testbench"]["g711_alaw_enc"].update(kill_pct=95.0, blind_classes=["reset-style"])
    s["artifacts"]["testbench"]["g711_ulaw_enc"].update(kill_pct=92.0, blind_classes=[])
    r = recs_of(s, monkeypatch)
    assert ids(r)[0] == "tb.blind.g711_alaw_enc"
    assert "tb.kill.g711_ulaw_enc" in ids(r)
    assert [x["action"] for x in r if x["id"].startswith("tb.")] == \
           ["regen_tb:g711_alaw_enc", "regen_tb:g711_ulaw_enc"]


def test_a_regated_rejection_is_not_counted_as_proven(monkeypatch):
    s = snap()
    s["artifacts"]["rtl"]["g711_ulaw_dec"]["regate"] = "REJECTED"
    assert improve.key_metrics(s)["rtl_proven"] == 3
    assert "rtl.rejected.g711_ulaw_dec" in ids(recs_of(s, monkeypatch))


def test_stale_kb_is_detected(monkeypatch):
    s = snap()
    monkeypatch.setattr(kbmod, "build", lambda write=True: {"cases": [0] * 42})
    r = improve.recommend(s)
    assert "kb.stale" in ids(r) and next(x for x in r if x["id"] == "kb.stale")["action"] == "kb"


def test_regression_gate_flags_a_metric_that_got_worse():
    before, after = snap(), snap()
    after["mutation"]["score_pct"] = 96.0
    d = improve.regression(before, after)
    assert d["regressed"] and "mutation_score" in d["worse"]
    assert not improve.regression(before, before)["regressed"]


def test_more_detection_is_an_improvement_not_a_regression():
    before = snap()
    before["mutation"].update(sole_detector=4, caught_only_by_stage={"synthesize": 4})
    after = snap()
    d = improve.regression(before, after)
    assert not d["regressed"] and "sole_detector_neg" in d["better"]


@pytest.mark.parametrize("action", ["kb", "mutate", "gate", "tests", "regen_tb", "regen_rtl"])
def test_every_recommended_action_exists(action):
    assert action in improve.ACTIONS


def test_recommended_actions_are_all_runnable(monkeypatch):
    s = snap()
    s["gate"]["verdict"] = "REJECTED"
    s["l0"]["ok"] = False
    s["mutation"]["survived"] = 1
    s["artifacts"]["testbench"].pop("g711_alaw_enc")
    s["artifacts"]["rtl"].pop("g711_alaw_dec")
    for r in recs_of(s, monkeypatch):
        if r["action"]:
            assert r["action"].partition(":")[0] in improve.ACTIONS
