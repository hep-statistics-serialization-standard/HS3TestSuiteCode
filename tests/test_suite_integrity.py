from __future__ import annotations

from pathlib import Path

from hs3suite.manifest import extract_features, load_json, verify_hashes
from hs3suite.runner import run_suite, summarize
from hs3suite.validation import validate_suite


ROOT = Path(__file__).resolve().parents[1]


def test_json_files_validate_against_local_schemas() -> None:
    assert validate_suite(ROOT) == []


def test_manifest_hashes_match_fixtures() -> None:
    manifest = load_json(ROOT / "manifest.json")
    assert verify_hashes(ROOT, manifest) == []


def test_feature_extraction_detects_interpreted_functions() -> None:
    payload = load_json(ROOT / "fixtures" / "rf103_interprfuncs" / "hs3.json")
    features = extract_features(payload)
    assert "generic_dist" in features["types"]
    assert "generic_function" in features["types"]
    assert "functions" in features["sections"]


def test_roofit_runner_executes_reference_scan() -> None:
    results = run_suite(ROOT, "roofit", {"rf101_basics"})
    summary = summarize(results)
    assert summary["failed"] == 0
    assert summary["passed"] == 3
    assert any(result.check_id == "twice_delta_nll_scan" for result in results)


def test_roofit_runner_honors_expected_xfail() -> None:
    results = run_suite(ROOT, "roofit", {"rf209_anaconv"})
    summary = summarize(results)
    assert summary["failed"] == 0
    assert summary["xfail"] == 1
