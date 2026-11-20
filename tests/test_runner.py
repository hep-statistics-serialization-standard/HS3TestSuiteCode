"""Check dispatch, expected failures, and what the CLI makes of the results."""

from __future__ import annotations

import pytest

from hs3suite.cli import build_parser, main
from hs3suite.runner import CheckResult, _run_fixture, compare_vectors, run_suite, summarize

from conftest import scan_check


# --------------------------------------------------------------------------------------
# compare_vectors
# --------------------------------------------------------------------------------------


def test_compare_vectors_accepts_a_deviation_exactly_at_the_tolerance() -> None:
    """allowed = atol + rtol * |expected|, and the boundary itself passes.

    Powers of two throughout, so the boundary is the boundary and not a rounding artefact.
    """
    compare_vectors([4.75], [4.0], {"rtol": 0.125, "atol": 0.25})


def test_compare_vectors_rejects_a_deviation_just_past_the_tolerance() -> None:
    with pytest.raises(AssertionError, match=r"point 0: got .*, expected .*, delta .* > .*"):
        compare_vectors([4.8125], [4.0], {"rtol": 0.125, "atol": 0.25})


def test_compare_vectors_scales_the_allowance_with_the_expected_magnitude() -> None:
    """The same absolute deviation passes on a large value and fails on a small one."""
    compare_vectors([1000.0 + 0.5], [1000.0], {"rtol": 1e-3, "atol": 0.0})
    with pytest.raises(AssertionError):
        compare_vectors([1.0 + 0.5], [1.0], {"rtol": 1e-3, "atol": 0.0})


def test_compare_vectors_names_the_offending_point() -> None:
    with pytest.raises(AssertionError, match="point 2:"):
        compare_vectors([0.0, 1.0, 9.0], [0.0, 1.0, 2.0], {"rtol": 0.0, "atol": 0.0})


def test_compare_vectors_rejects_a_length_mismatch_before_comparing() -> None:
    with pytest.raises(AssertionError, match="length mismatch: 2 != 3"):
        compare_vectors([1.0, 2.0], [1.0, 2.0, 3.0], {"rtol": 1e-8, "atol": 1e-7})


# --------------------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------------------


def test_every_check_kind_reaches_its_backend_method(suite_factory, fake_backend) -> None:
    suite = suite_factory()
    results = run_suite(suite.root, backend=fake_backend)

    assert summarize(results) == {"passed": 5, "failed": 0, "skipped": 0, "xfail": 0}
    assert [call[0] for call in fake_backend.calls] == [
        "run_structure_check",
        "run_twice_delta_nll_scan",
        "run_pdf_scan",
        "run_function_scan",
    ]


def test_the_workspace_is_loaded_once_per_fixture_not_once_per_check(
    suite_factory, fake_backend
) -> None:
    suite = suite_factory(test_ids=("first", "second"))
    run_suite(suite.root, backend=fake_backend)
    assert len(fake_backend.loads) == 2


def test_static_integrity_needs_no_backend_at_all(suite_factory, fake_backend) -> None:
    suite = suite_factory(checks=[{"id": "static_integrity", "kind": "static_integrity"}])
    results = run_suite(suite.root, backend=fake_backend)

    assert [result.status for result in results] == ["passed"]
    assert fake_backend.loads == []


def test_a_malformed_model_is_reported_rather_than_crashing_the_run(
    suite_factory, fake_backend
) -> None:
    """``canonical_sha256`` has to parse the model, so a malformed one is caught here —
    before ``static_integrity``, which would otherwise be the check to report it."""
    suite = suite_factory(checks=[{"id": "static_integrity", "kind": "static_integrity"}])
    (suite.fixture_dir("synth_basics") / "hs3.json").write_text("{not json", encoding="utf-8")

    results = run_suite(suite.root, backend=fake_backend)
    assert {result.check_id for result in results} == {"hash_validation"}
    assert any("hs3.json is not valid JSON" in result.message for result in results)
    assert fake_backend.calls == []


def test_a_model_the_manifest_registers_but_nobody_wrote_is_reported(
    suite_factory, fake_backend
) -> None:
    suite = suite_factory()
    (suite.fixture_dir("synth_basics") / "hs3.json").unlink()

    results = run_suite(suite.root, backend=fake_backend)
    assert any("cannot read hs3.json" in result.message for result in results)
    assert fake_backend.calls == []


def test_an_unsupported_check_kind_fails_that_check_only(suite_factory, fake_backend) -> None:
    """The runner's backstop for a kind the schema has learned but it has not.

    Reached directly: ``expected.schema.json`` enumerates the kinds, so a suite carrying
    an unknown one never gets past ``validate_suite`` (which is asserted separately).
    """
    suite = suite_factory()
    fixture = suite.manifest["fixtures"][0]
    results = _run_fixture(
        suite.root,
        fake_backend,
        fixture,
        {"checks": [{"id": "future", "kind": "future_scan"}]},
    )

    assert results[0].status == "failed"
    assert "unsupported check kind 'future_scan'" in results[0].message


def test_a_numeric_disagreement_fails_only_the_check_that_disagrees(
    suite_factory, fake_backend
) -> None:
    suite = suite_factory()
    fake_backend.results["run_pdf_scan"] = [9.0, 9.0, 9.0]
    results = run_suite(suite.root, backend=fake_backend)

    failed = [result for result in results if result.status == "failed"]
    assert [result.check_id for result in failed] == ["pdf_scan"]
    assert summarize(results)["passed"] == 4


def test_a_backend_error_is_reported_against_its_own_check(suite_factory, fake_backend) -> None:
    suite = suite_factory()
    fake_backend.fail_on["run_structure_check"] = RuntimeError("no such pdf 'gauss'")
    results = run_suite(suite.root, backend=fake_backend)

    failure = next(result for result in results if result.status == "failed")
    assert failure.check_id == "structure_import"
    assert failure.message == "no such pdf 'gauss'"


def test_selection_runs_only_the_named_fixtures(suite_factory, fake_backend) -> None:
    suite = suite_factory(test_ids=("first", "second", "third"))
    results = run_suite(suite.root, backend=fake_backend, selected={"second"})
    assert {result.test_id for result in results} == {"second"}


# --------------------------------------------------------------------------------------
# Validation gates the whole run
# --------------------------------------------------------------------------------------


def test_a_hash_mismatch_aborts_before_any_check_executes(suite_factory, fake_backend) -> None:
    """A fixture whose model no longer matches its frozen hash cannot be compared."""
    suite = suite_factory()
    payload = suite.read("synth_basics", "hs3.json")
    payload["distributions"][0]["mean"] = "mu_renamed"
    suite.write("synth_basics", "hs3.json", payload)

    results = run_suite(suite.root, backend=fake_backend)
    assert {result.check_id for result in results} == {"hash_validation"}
    assert fake_backend.calls == []


def test_a_schema_error_aborts_before_any_check_executes(suite_factory, fake_backend) -> None:
    suite = suite_factory()
    (suite.fixture_dir("synth_basics") / "metadata.json").unlink()

    results = run_suite(suite.root, backend=fake_backend)
    assert [result.check_id for result in results] == ["schema_validation"]
    assert results[0].test_id == "<suite>"
    assert fake_backend.calls == []


# --------------------------------------------------------------------------------------
# Expected failures
# --------------------------------------------------------------------------------------


XFAIL = {"fake": {"xfail": True, "reason": "known gap in the fake backend"}}


def test_an_expected_failure_collapses_the_fixture_to_one_xfail(
    suite_factory, fake_backend
) -> None:
    suite = suite_factory(backend_expectations=XFAIL)
    fake_backend.results["run_pdf_scan"] = [9.0, 9.0, 9.0]

    results = run_suite(suite.root, backend=fake_backend)
    assert summarize(results) == {"passed": 0, "failed": 0, "skipped": 0, "xfail": 1}
    assert results[0].check_id == "<fixture>"
    assert "known gap in the fake backend" in results[0].message


def test_an_expected_failure_covers_a_fixture_that_cannot_even_load(
    suite_factory, fake_backend
) -> None:
    suite = suite_factory(backend_expectations=XFAIL)
    fake_backend.fail_on["load_workspace"] = RuntimeError("importJSON rejected the model")

    results = run_suite(suite.root, backend=fake_backend)
    assert summarize(results)["xfail"] == 1
    assert "importJSON rejected the model" in results[0].message


def test_an_unexpected_pass_is_a_failure_so_the_manifest_gets_reviewed(
    suite_factory, fake_backend
) -> None:
    suite = suite_factory(backend_expectations=XFAIL)
    results = run_suite(suite.root, backend=fake_backend)

    assert summarize(results) == {"passed": 0, "failed": 1, "skipped": 0, "xfail": 0}
    assert results[0].message.startswith("XPASS: ")


def test_expectations_are_keyed_by_the_backends_own_name_not_the_cli_spec(
    suite_factory, fake_backend
) -> None:
    """The same fixture keeps its known failures however the plugin was addressed."""
    suite = suite_factory(backend_expectations=XFAIL)
    fake_backend.fail_on["run_pdf_scan"] = RuntimeError("unsupported")

    results = run_suite(suite.root, "some_module:SomeClass", backend=fake_backend)
    assert summarize(results)["xfail"] == 1


def test_an_expectation_for_another_backend_does_not_apply(suite_factory, fake_backend) -> None:
    suite = suite_factory(backend_expectations={"other": {"xfail": True, "reason": "theirs"}})
    fake_backend.fail_on["run_pdf_scan"] = RuntimeError("unsupported")

    results = run_suite(suite.root, backend=fake_backend)
    assert summarize(results)["failed"] == 1
    assert summarize(results)["xfail"] == 0


def test_summarize_counts_statuses_it_was_not_told_about() -> None:
    summary = summarize([CheckResult("t", "c", "passed"), CheckResult("t", "c", "erratic")])
    assert summary["passed"] == 1
    assert summary["erratic"] == 1


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def test_the_parser_defaults_root_to_the_working_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    args = build_parser().parse_args(["run"])
    assert args.root == tmp_path
    assert args.backend is None
    assert args.test_id is None


def test_a_command_is_required() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_run_exits_zero_and_prints_test_id_double_colon_check_id(
    suite_factory, install_plugin, capsys, monkeypatch
) -> None:
    suite = suite_factory(checks=[{"id": "static_integrity", "kind": "static_integrity"}])
    install_plugin()
    monkeypatch.delenv("HS3SUITE_BACKEND", raising=False)

    assert main(["run", "--root", str(suite.root)]) == 0
    output = capsys.readouterr().out
    assert "Backend: installed-fake" in output
    assert "PASSED  synth_basics::static_integrity" in output
    assert "Summary: passed=1, xfail=0, skipped=0, failed=0" in output


def test_run_exits_one_when_a_check_fails(suite_factory, install_plugin, capsys) -> None:
    suite = suite_factory()
    suite.fixture_dir("synth_basics").joinpath("expected.json").unlink()
    install_plugin()

    assert main(["run", "--root", str(suite.root), "--backend", "hs3suite_backend"]) == 1
    assert "FAILED  <suite>::schema_validation" in capsys.readouterr().out


def test_run_exits_two_when_the_plugin_cannot_be_loaded(suite_factory, capsys) -> None:
    """A missing plugin is a setup problem, not a conformance failure — hence 2, not 1."""
    suite = suite_factory()
    assert main(["run", "--root", str(suite.root), "--backend", "nosuchbackend"]) == 2
    assert "ERROR   cannot import backend module 'nosuchbackend'" in capsys.readouterr().out


def test_test_id_is_repeatable(suite_factory, install_plugin, capsys) -> None:
    suite = suite_factory(
        test_ids=("first", "second", "third"),
        checks=[{"id": "static_integrity", "kind": "static_integrity"}],
    )
    install_plugin()

    main(
        [
            "run",
            "--root",
            str(suite.root),
            "--backend",
            "hs3suite_backend",
            "--test-id",
            "first",
            "--test-id",
            "third",
        ]
    )
    output = capsys.readouterr().out
    assert "first::static_integrity" in output
    assert "third::static_integrity" in output
    assert "second" not in output
