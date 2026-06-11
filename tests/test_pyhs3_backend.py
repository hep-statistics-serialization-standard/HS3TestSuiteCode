from __future__ import annotations

import os
from pathlib import Path

import pytest

from hs3suite.runner import run_suite, summarize


ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("PYTENSOR_FLAGS", "base_compiledir=/tmp/hs3suite_pytensor")
pytest.importorskip("pyhs3")


def test_pyhs3_runner_executes_reference_scan() -> None:
    results = run_suite(ROOT, "pyhs3", {"rf101_basics"})
    summary = summarize(results)
    assert summary["failed"] == 0
    assert summary["passed"] == 3
    assert any(result.check_id == "twice_delta_nll_scan" for result in results)


def test_pyhs3_unsupported_fixture_fails_with_feature_message() -> None:
    results = run_suite(ROOT, "pyhs3", {"rf110_normintegration"})
    failures = [result for result in results if result.status == "failed"]
    assert failures
    assert any("integral" in result.message for result in failures)
