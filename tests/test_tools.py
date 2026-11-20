"""The two scripts under ``tools/``: fixture generation and HS3 upconversion.

Both are driven through the same plugin seam as the runner, so both are exercised here
against a plugin written into ``tmp_path`` rather than against anything real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hs3suite.manifest import load_json, write_json

from conftest import default_metadata, hs3_payload


GENERATOR_PLUGIN = '''
"""A plugin that answers every scan deterministically, without a statistics library."""


class HS3TestSuiteBackend:
    name = "fake"

    def load_workspace(self, hs3_path):
        return {"path": str(hs3_path)}

    def structure(self, workspace):
        return {"pdfs": [], "functions": [], "data": []}

    def run_structure_check(self, workspace, check):
        return None

    def _scan(self, check):
        return [float(sum(point)) for point in check["scan_points"]]

    def run_twice_delta_nll_scan(self, workspace, check, hs3_path):
        return self._scan(check)

    def run_pdf_scan(self, workspace, check, hs3_path):
        return self._scan(check)

    def run_function_scan(self, workspace, check, hs3_path):
        return self._scan(check)
'''


UPCONVERT_PLUGIN = '''
import json

REFUSE = False


class HS3TestSuiteBackend:
    name = "fake"

    def upconvert(self, input_path, output_path, *, verbose=False):
        if REFUSE:
            raise RuntimeError("this backend refuses")
        payload = json.loads(input_path.read_text())
        payload.setdefault("metadata", {})["hs3_version"] = "0.3"
        output_path.write_text(json.dumps(payload))
'''

REFUSING_PLUGIN = UPCONVERT_PLUGIN.replace("REFUSE = False", "REFUSE = True")
VERBOSELESS_PLUGIN = UPCONVERT_PLUGIN.replace(", *, verbose=False", "")


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    """A fixtures-repo-shaped root holding one fixture with hs3.json + metadata.json."""
    directory = tmp_path / "fixtures" / "synth_basics"
    write_json(directory / "hs3.json", hs3_payload())
    metadata = default_metadata("synth_basics")
    metadata["nll_scans"] = [
        {"pdf": "gauss", "data": "gaussData", "parameters": ["mu"], "points": [[0.0], [1.0]]}
    ]
    metadata["pdf_scans"] = [
        {
            "pdf": "gauss",
            "observables": ["x"],
            "parameters": ["mu"],
            "points": [[0.0], [1.0]],
        }
    ]
    metadata["function_scans"] = [
        {"function": "double_mu", "parameters": ["mu"], "points": [[0.0], [1.0]]}
    ]
    write_json(directory / "metadata.json", metadata)
    return directory


# --------------------------------------------------------------------------------------
# build_manifest_and_expected.py
# --------------------------------------------------------------------------------------


def test_generation_writes_expected_and_a_manifest_entry(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    install_plugin(source=GENERATOR_PLUGIN)
    root = fixture_dir.parents[1]

    assert build_tool.main(["-f", str(fixture_dir), "-r", str(root)]) == 0

    expected = load_json(fixture_dir / "expected.json")
    assert [check["kind"] for check in expected["checks"]] == [
        "static_integrity",
        "structure_import",
        "twice_delta_nll_scan",
        "pdf_scan",
        "function_scan",
    ]
    assert expected["test_id"] == "synth_basics"

    entry = load_json(root / "manifest.json")["fixtures"][0]
    assert entry["test_id"] == "synth_basics"
    assert entry["path"] == "fixtures/synth_basics"
    assert entry["checks"] == [check["id"] for check in expected["checks"]]


def test_generated_expectations_are_whatever_the_backend_returned(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    """The frozen numbers come from ``run_<kind>``; the tool never computes them itself."""
    install_plugin(source=GENERATOR_PLUGIN)
    build_tool.main(["-f", str(fixture_dir), "-r", str(fixture_dir.parents[1])])

    checks = {c["id"]: c for c in load_json(fixture_dir / "expected.json")["checks"]}
    assert checks["twice_delta_nll_scan"]["expected"] == [0.0, 1.0]
    assert checks["pdf_scan"]["expected"] == [0.0, 1.0]


def test_the_generated_manifest_hashes_match_the_model_on_disk(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    from hs3suite.validation import validate_suite

    install_plugin(source=GENERATOR_PLUGIN)
    root = fixture_dir.parents[1]
    build_tool.main(["-f", str(fixture_dir), "-r", str(root)])

    manifest = load_json(root / "manifest.json")
    from hs3suite.manifest import verify_hashes

    assert verify_hashes(root, manifest) == []
    assert validate_suite(root) == []


def test_the_reference_point_comes_from_default_values(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    install_plugin(source=GENERATOR_PLUGIN)
    build_tool.main(["-f", str(fixture_dir), "-r", str(fixture_dir.parents[1])])

    check = load_json(fixture_dir / "expected.json")["checks"][2]
    assert check["reference_point"] == {"mu": 1.0, "sigma": 2.0, "two": 2.0}


def test_metadata_is_left_alone_when_it_already_exists(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    """metadata.json is a hand-written input; generation must never overwrite it."""
    install_plugin(source=GENERATOR_PLUGIN)
    before = (fixture_dir / "metadata.json").read_text()
    build_tool.main(["-f", str(fixture_dir), "-r", str(fixture_dir.parents[1])])
    assert (fixture_dir / "metadata.json").read_text() == before


def test_extend_manifest_keeps_entries_for_other_fixtures(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    """CI generates one fixture per job, so ``-e`` must add rather than replace."""
    install_plugin(source=GENERATOR_PLUGIN)
    root = fixture_dir.parents[1]
    write_json(
        root / "manifest.json",
        {
            "schema_version": 1,
            "fixtures": [
                {
                    "test_id": "already_there",
                    "path": "fixtures/already_there",
                    "hashes": {"sha256": "0" * 64, "canonical_sha256": "0" * 64},
                    "features": {"sections": [], "types": [], "semantic": []},
                    "tags": [],
                    "conformance": [],
                    "checks": [],
                }
            ],
        },
    )

    build_tool.main(["-e", "-f", str(fixture_dir), "-r", str(root)])
    ids = [entry["test_id"] for entry in load_json(root / "manifest.json")["fixtures"]]
    assert ids == ["already_there", "synth_basics"]


def test_without_extend_manifest_the_index_is_rebuilt_from_scratch(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    install_plugin(source=GENERATOR_PLUGIN)
    root = fixture_dir.parents[1]
    write_json(root / "manifest.json", {"schema_version": 1, "fixtures": [{"test_id": "stale"}]})

    build_tool.main(["-f", str(fixture_dir), "-r", str(root)])
    ids = [entry["test_id"] for entry in load_json(root / "manifest.json")["fixtures"]]
    assert ids == ["synth_basics"]


def test_a_single_scan_gets_the_clean_default_id_and_several_get_suffixes(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    install_plugin(source=GENERATOR_PLUGIN)
    metadata = load_json(fixture_dir / "metadata.json")
    metadata["pdf_scans"] = [
        {"pdf": "gauss", "observables": ["x"], "parameters": ["mu"], "points": [[0.0]]},
        {"pdf": "gauss", "observables": ["x"], "parameters": ["sigma"], "points": [[1.0]]},
    ]
    write_json(fixture_dir / "metadata.json", metadata)

    build_tool.main(["-f", str(fixture_dir), "-r", str(fixture_dir.parents[1])])
    ids = [check["id"] for check in load_json(fixture_dir / "expected.json")["checks"]]
    assert "twice_delta_nll_scan" in ids  # one entry: no suffix
    assert "pdf_scan_0" in ids and "pdf_scan_1" in ids  # several: suffixed


def test_an_explicit_scan_id_in_metadata_wins(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    install_plugin(source=GENERATOR_PLUGIN)
    metadata = load_json(fixture_dir / "metadata.json")
    metadata["nll_scans"][0]["id"] = "scan_mu"
    write_json(fixture_dir / "metadata.json", metadata)

    build_tool.main(["-f", str(fixture_dir), "-r", str(fixture_dir.parents[1])])
    ids = [check["id"] for check in load_json(fixture_dir / "expected.json")["checks"]]
    assert "scan_mu" in ids


def test_a_fixture_outside_the_root_is_refused(build_tool, fixture_dir: Path, tmp_path) -> None:
    """Manifest paths are root-relative; a fixture elsewhere has no representable path."""
    with pytest.raises(SystemExit, match="is outside the suite root"):
        build_tool.relative_path(fixture_dir, tmp_path / "somewhere_else")


def test_structure_targets_hides_roofits_internal_helper_functions(build_tool) -> None:
    payload = {
        "distributions": [{"name": "gauss"}],
        "functions": [{"name": "real"}, {"name": "decay_exponential_inverted"}],
        "data": [{"name": "gaussData"}],
    }
    assert build_tool.structure_targets(payload) == {
        "pdfs": ["gauss"],
        "functions": ["real"],
        "data": ["gaussData"],
    }


def test_a_model_without_default_values_gets_an_empty_reference_point(build_tool) -> None:
    assert build_tool.default_parameter_point({"parameter_points": [{"name": "other"}]}) == {}


# --------------------------------------------------------------------------------------
# upconvert_hs3.py
# --------------------------------------------------------------------------------------


def test_discover_prefers_the_manifest_over_the_fixtures_directory(
    upconvert_tool, tmp_path: Path
) -> None:
    """The manifest is the index: a fixture directory it ignores is not upconverted."""
    write_json(tmp_path / "fixtures" / "listed" / "hs3.json", {})
    write_json(tmp_path / "fixtures" / "unlisted" / "hs3.json", {})
    write_json(
        tmp_path / "manifest.json",
        {"schema_version": 1, "fixtures": [{"test_id": "listed", "path": "fixtures/listed"}]},
    )

    found = upconvert_tool.discover_hs3_files(tmp_path, [])
    assert found == [tmp_path / "fixtures" / "listed" / "hs3.json"]


def test_discover_falls_back_to_globbing_without_a_manifest(
    upconvert_tool, tmp_path: Path
) -> None:
    write_json(tmp_path / "fixtures" / "b" / "hs3.json", {})
    write_json(tmp_path / "fixtures" / "a" / "hs3.json", {})
    found = upconvert_tool.discover_hs3_files(tmp_path, [])
    assert [path.parent.name for path in found] == ["a", "b"]


def test_explicit_files_win_over_discovery(upconvert_tool, tmp_path: Path) -> None:
    write_json(tmp_path / "manifest.json", {"schema_version": 1, "fixtures": []})
    explicit = tmp_path / "elsewhere.json"
    explicit.write_text("{}", encoding="utf-8")
    assert upconvert_tool.discover_hs3_files(tmp_path, [explicit]) == [explicit]


def test_output_path_defaults_to_overwriting_in_place(upconvert_tool, tmp_path: Path) -> None:
    source = tmp_path / "fixtures" / "x" / "hs3.json"
    assert upconvert_tool.output_path_for(source, tmp_path, None) == source


def test_output_dir_mirrors_the_layout_below_the_root(upconvert_tool, tmp_path: Path) -> None:
    source = tmp_path / "fixtures" / "x" / "hs3.json"
    out = upconvert_tool.output_path_for(source, tmp_path, tmp_path / "out")
    assert out == tmp_path / "out" / "fixtures" / "x" / "hs3.json"


def test_a_file_outside_the_root_lands_at_the_top_of_the_output_dir(
    upconvert_tool, tmp_path: Path
) -> None:
    out = upconvert_tool.output_path_for(Path("/elsewhere/hs3.json"), tmp_path, tmp_path / "out")
    assert out == tmp_path / "out" / "hs3.json"


def test_upconversion_replaces_the_target_only_once_the_backend_returns(
    upconvert_tool, fake_backend, tmp_path: Path
) -> None:
    source = tmp_path / "hs3.json"
    write_json(source, hs3_payload())
    upconvert_tool.upconvert_hs3_file(fake_backend.upconvert, source, source)
    assert load_json(source) == hs3_payload()


def test_a_backend_that_raises_leaves_the_fixture_untouched(
    upconvert_tool, fake_backend, tmp_path: Path
) -> None:
    """The default is to overwrite in place, so a half-way failure must not truncate."""
    source = tmp_path / "hs3.json"
    write_json(source, hs3_payload())
    fake_backend.fail_on["upconvert"] = RuntimeError("importJSON failed")

    with pytest.raises(RuntimeError, match="importJSON failed"):
        upconvert_tool.upconvert_hs3_file(fake_backend.upconvert, source, source)
    assert load_json(source) == hs3_payload()
    assert list(tmp_path.iterdir()) == [source]  # the staging directory is gone


def test_a_backend_that_writes_nothing_is_a_failed_conversion(
    upconvert_tool, tmp_path: Path
) -> None:
    source = tmp_path / "hs3.json"
    write_json(source, hs3_payload())

    with pytest.raises(RuntimeError, match="backend upconvert wrote no output file"):
        upconvert_tool.upconvert_hs3_file(lambda *_: None, source, source)
    assert load_json(source) == hs3_payload()


def test_a_missing_input_is_reported_before_the_backend_is_called(
    upconvert_tool, fake_backend, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        upconvert_tool.upconvert_hs3_file(
            fake_backend.upconvert, tmp_path / "nope.json", tmp_path / "out.json"
        )
    assert fake_backend.calls == []


def test_a_plugin_without_upconvert_exits_with_a_usable_message(
    upconvert_tool, fake_backend
) -> None:
    """Upconversion is optional in the contract, so this is a usage error, not a crash."""

    class CheckOnlyBackend:
        name = "check-only"

    with pytest.raises(SystemExit, match="backend 'check-only' implements no upconvert"):
        upconvert_tool.backend_upconvert(CheckOnlyBackend())
    assert upconvert_tool.backend_upconvert(fake_backend) == fake_backend.upconvert


@pytest.mark.parametrize(
    "function, takes_verbose",
    [
        (lambda i, o: None, False),
        (lambda i, o, *, verbose=False: None, True),
        (lambda i, o, **kwargs: None, True),
    ],
)
def test_accepts_verbose_reads_the_signature(upconvert_tool, function, takes_verbose) -> None:
    assert upconvert_tool.accepts_verbose(function) is takes_verbose


def test_main_reports_nothing_to_do_rather_than_pretending_to_convert(
    upconvert_tool, tmp_path: Path, capsys
) -> None:
    assert upconvert_tool.main(["--root", str(tmp_path)]) == 1
    assert "No HS3 files found." in capsys.readouterr().err


def test_main_converts_every_fixture_in_the_manifest(
    upconvert_tool, tmp_path: Path, install_plugin, capsys
) -> None:
    install_plugin(source=UPCONVERT_PLUGIN)
    write_json(tmp_path / "fixtures" / "one" / "hs3.json", hs3_payload())
    write_json(
        tmp_path / "manifest.json",
        {"schema_version": 1, "fixtures": [{"test_id": "one", "path": "fixtures/one"}]},
    )

    assert upconvert_tool.main(["--root", str(tmp_path), "--output-dir", str(tmp_path / "out")]) == 0
    converted = json.loads((tmp_path / "out" / "fixtures" / "one" / "hs3.json").read_text())
    assert converted["metadata"]["hs3_version"] == "0.3"
    assert "UPDATED fixtures/one/hs3.json -> out/fixtures/one/hs3.json" in capsys.readouterr().out


def test_main_exits_nonzero_and_names_the_file_that_failed(
    upconvert_tool, tmp_path: Path, install_plugin, capsys
) -> None:
    install_plugin(source=REFUSING_PLUGIN)
    write_json(tmp_path / "fixtures" / "one" / "hs3.json", hs3_payload())

    assert upconvert_tool.main(["--root", str(tmp_path)]) == 1
    errors = capsys.readouterr().err
    assert "FAILED fixtures/one/hs3.json: this backend refuses" in errors
    assert "Failed to upconvert 1 of 1 HS3 file(s)." in errors


def test_verbose_against_a_plugin_that_ignores_it_says_so(
    upconvert_tool, tmp_path: Path, install_plugin, capsys
) -> None:
    """Verbosity is optional in the contract: say it did not take, don't pretend."""
    install_plugin(source=VERBOSELESS_PLUGIN)
    write_json(tmp_path / "fixtures" / "one" / "hs3.json", hs3_payload())

    assert upconvert_tool.main(["--root", str(tmp_path), "--verbose"]) == 0
    assert "takes no verbose flag" in capsys.readouterr().err




# --------------------------------------------------------------------------------------
# Expected failures are keyed by the generating plugin, not by a hard-coded backend
# --------------------------------------------------------------------------------------


def _with_xfail(fixture_dir: Path, backend: str) -> None:
    metadata = load_json(fixture_dir / "metadata.json")
    metadata["backend_expectations"] = {backend: {"xfail": True, "reason": "cannot import"}}
    write_json(fixture_dir / "metadata.json", metadata)


def test_a_fixture_the_generating_plugin_cannot_import_freezes_no_numbers(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    """A backend that cannot import a model cannot freeze values for it either, so only
    the two checks that need no backend are written."""
    install_plugin(source=GENERATOR_PLUGIN)
    _with_xfail(fixture_dir, "fake")

    build_tool.main(["-f", str(fixture_dir), "-r", str(fixture_dir.parents[1])])
    kinds = [check["kind"] for check in load_json(fixture_dir / "expected.json")["checks"]]
    assert kinds == ["static_integrity", "structure_import"]


def test_skipping_the_numeric_checks_is_announced(
    build_tool, fixture_dir: Path, install_plugin, capsys
) -> None:
    """Regenerating with an xfail backend overwrites expected.json without its numbers;
    that must not happen silently."""
    install_plugin(source=GENERATOR_PLUGIN)
    _with_xfail(fixture_dir, "fake")

    build_tool.main(["-f", str(fixture_dir), "-r", str(fixture_dir.parents[1])])
    out = capsys.readouterr().out
    assert "synth_basics" in out
    assert "'fake' is marked xfail" in out
    assert "3 numeric check(s) not generated" in out


def test_an_expectation_naming_another_backend_does_not_suppress_generation(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    """The old code keyed this off the literal 'roofit'; it is the plugin's own name."""
    install_plugin(source=GENERATOR_PLUGIN)
    _with_xfail(fixture_dir, "some_other_backend")

    build_tool.main(["-f", str(fixture_dir), "-r", str(fixture_dir.parents[1])])
    kinds = [check["kind"] for check in load_json(fixture_dir / "expected.json")["checks"]]
    assert "twice_delta_nll_scan" in kinds


def test_the_expectation_reaches_the_manifest_entry_too(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    install_plugin(source=GENERATOR_PLUGIN)
    _with_xfail(fixture_dir, "fake")

    root = fixture_dir.parents[1]
    build_tool.main(["-f", str(fixture_dir), "-r", str(root)])
    entry = load_json(root / "manifest.json")["fixtures"][0]
    assert entry["backend_expectations"] == {"fake": {"xfail": True, "reason": "cannot import"}}


def test_written_metadata_records_the_backend_the_fixture_was_generated_with(build_tool) -> None:
    """CI uses reference_backend as the container image, so it must be the spec's own
    value rather than a hard-coded RooFit version."""
    from hs3suite.specs import FixtureSpec

    spec = FixtureSpec(
        test_id="synth_basics",
        title="t",
        source="s",
        description="d",
        nll_scans=(),
        reference_backend="ghcr.io/example/backend:1.0",
    )
    assert build_tool.build_metadata(spec)["reference_backend"] == "ghcr.io/example/backend:1.0"


def test_a_fixture_without_modified_from_source_still_generates(
    build_tool, fixture_dir: Path, install_plugin
) -> None:
    """The metadata schema calls the field optional; absent means 'not modified'."""
    install_plugin(source=GENERATOR_PLUGIN)
    metadata = load_json(fixture_dir / "metadata.json")
    del metadata["modified_from_source"]
    write_json(fixture_dir / "metadata.json", metadata)

    assert build_tool.main(["-f", str(fixture_dir), "-r", str(fixture_dir.parents[1])]) == 0


def test_a_legacy_fixtures_expectation_survives_regeneration_from_its_metadata(
    build_tool, tmp_path: Path, install_plugin, monkeypatch
) -> None:
    """The legacy path writes metadata.json from the spec; a later ``-f`` run reads the
    spec back out of that file, so whatever the first run leaves out is lost for good."""
    from hs3suite.specs import FixtureSpec, NLLScanSpec
    from hs3suite.validation import validate_suite

    install_plugin(source=GENERATOR_PLUGIN)
    expectations = {"some_other_backend": {"xfail": True, "reason": "cannot import"}}
    spec = FixtureSpec(
        test_id="legacy_one",
        title="t",
        source="s",
        description="d",
        nll_scans=(NLLScanSpec.one_dim("gauss", "gaussData", "mu", (0.0, 1.0)),),
        reference_backend="fake 1.0",
        backend_expectations=expectations,
    )
    monkeypatch.setattr(build_tool, "LEGACY_FIXTURES", (spec,))
    fixture_dir = tmp_path / "fixtures" / "legacy_one"
    write_json(fixture_dir / "hs3.json", hs3_payload())

    build_tool.main(["-r", str(tmp_path)])
    assert validate_suite(tmp_path) == []
    assert load_json(fixture_dir / "metadata.json")["backend_expectations"] == expectations

    build_tool.main(["-e", "-f", str(fixture_dir), "-r", str(tmp_path)])
    entry = load_json(tmp_path / "manifest.json")["fixtures"][0]
    assert entry["backend_expectations"] == expectations
