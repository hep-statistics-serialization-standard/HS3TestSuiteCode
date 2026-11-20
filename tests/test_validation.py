"""Schema validation, hashing, and feature extraction — everything the runner does
before a backend is involved."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hs3suite.manifest import (
    canonical_sha256,
    extract_features,
    load_json,
    raw_sha256,
    verify_hashes,
    write_json,
)
from hs3suite.validation import SCHEMAS, load_schema, validate_suite

from conftest import default_metadata, hs3_payload, scan_check


# --------------------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------------------


def test_every_declared_schema_ships_with_the_package() -> None:
    """An installed hs3suite must validate a fixtures repo without a local schema copy."""
    for name in SCHEMAS:
        assert load_schema(name)["$schema"].startswith("https://json-schema.org/")


def test_load_schema_rejects_an_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown schema 'nope'"):
        load_schema("nope")


def test_a_freshly_built_suite_validates(suite_factory) -> None:
    assert validate_suite(suite_factory().root) == []


def test_an_invalid_manifest_short_circuits_before_the_fixtures(suite_factory) -> None:
    """One manifest error is returned alone: per-fixture paths cannot be trusted after it."""
    suite = suite_factory()
    manifest = suite.manifest
    del manifest["fixtures"][0]["hashes"]
    manifest["fixtures"][0]["path"] = "fixtures/gone"
    suite.rewrite_manifest(manifest)

    errors = validate_suite(suite.root)
    assert len(errors) == 1
    assert errors[0].startswith("manifest.json: ")


@pytest.mark.parametrize("missing", ["hs3.json", "metadata.json", "expected.json"])
def test_a_missing_fixture_file_is_reported_by_relative_path(suite_factory, missing: str) -> None:
    suite = suite_factory()
    (suite.fixture_dir("synth_basics") / missing).unlink()
    assert validate_suite(suite.root) == [f"fixtures/synth_basics/{missing}: file not found"]


def test_an_invalid_expected_file_is_reported_against_its_schema(suite_factory) -> None:
    suite = suite_factory()
    suite.write(
        "synth_basics",
        "expected.json",
        {"schema_version": 1, "test_id": "synth_basics", "checks": [{"id": "x", "kind": "nope"}]},
    )
    errors = validate_suite(suite.root)
    assert len(errors) == 1
    assert errors[0].startswith("fixtures/synth_basics/expected.json: ")


def test_a_scan_check_missing_its_frozen_values_is_rejected(suite_factory) -> None:
    """The schema requires expected/tolerance for every scan kind; a check without them
    pins no numbers at all."""
    bare = scan_check("pdf_scan", "pdf_scan")
    del bare["expected"]
    suite = suite_factory(checks=[bare])
    assert validate_suite(suite.root)


def test_duplicate_check_ids_are_flagged(suite_factory) -> None:
    """Ids are how the runner reports a check, so a collision makes output ambiguous."""
    suite = suite_factory(
        checks=[
            scan_check("shared", "pdf_scan"),
            scan_check("shared", "function_scan"),
        ]
    )
    assert validate_suite(suite.root) == [
        "fixtures/synth_basics/expected.json: duplicate check id 'shared'"
    ]


def test_a_fixture_directory_the_manifest_ignores_is_not_validated(suite_factory) -> None:
    """Documented behavior: validation follows the manifest, not the filesystem."""
    suite = suite_factory()
    orphan = suite.root / "fixtures" / "orphan"
    write_json(orphan / "expected.json", {"nonsense": True})
    assert validate_suite(suite.root) == []


# --------------------------------------------------------------------------------------
# Hashes
# --------------------------------------------------------------------------------------


def test_raw_and_canonical_hashes_disagree_on_reformatting(tmp_path: Path) -> None:
    """That is the whole point of keeping both: canonical survives a reformat, raw does not."""
    payload = hs3_payload()
    pretty = tmp_path / "pretty.json"
    compact = tmp_path / "compact.json"
    pretty.write_text(json.dumps(payload, indent=4, sort_keys=False), encoding="utf-8")
    compact.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")

    assert raw_sha256(pretty) != raw_sha256(compact)
    assert canonical_sha256(pretty) == canonical_sha256(compact)


def test_verify_hashes_accepts_an_untouched_suite(suite_factory) -> None:
    suite = suite_factory()
    assert verify_hashes(suite.root, suite.manifest) == []


def test_verify_hashes_reports_raw_and_canonical_mismatches_separately(suite_factory) -> None:
    suite = suite_factory()
    payload = suite.read("synth_basics", "hs3.json")
    payload["distributions"][0]["sigma"] = "sigma_renamed"
    suite.write("synth_basics", "hs3.json", payload)

    assert verify_hashes(suite.root, suite.manifest) == [
        "synth_basics: raw sha256 mismatch",
        "synth_basics: canonical sha256 mismatch",
    ]


def test_a_reformatted_model_trips_only_the_raw_hash(suite_factory) -> None:
    suite = suite_factory()
    hs3_path = suite.fixture_dir("synth_basics") / "hs3.json"
    hs3_path.write_text(json.dumps(load_json(hs3_path), indent=8), encoding="utf-8")

    assert verify_hashes(suite.root, suite.manifest) == ["synth_basics: raw sha256 mismatch"]


def test_write_json_is_stable_across_rewrites(tmp_path: Path) -> None:
    """Generated files are committed, so their serialization must not churn a diff."""
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    write_json(first, {"b": 1, "a": [2, 3]})
    write_json(second, {"a": [2, 3], "b": 1})
    assert first.read_text() == second.read_text()
    assert first.read_text().endswith("\n")


# --------------------------------------------------------------------------------------
# Features
# --------------------------------------------------------------------------------------


def test_extract_features_collects_sections_and_types() -> None:
    features = extract_features(hs3_payload())
    assert features["sections"] == [
        "data",
        "distributions",
        "domains",
        "functions",
        "parameter_points",
    ]
    assert features["types"] == ["gaussian_dist", "product", "product_domain", "unbinned"]
    assert features["semantic"] == []


def test_extract_features_ignores_non_list_sections_and_untyped_entries() -> None:
    """``metadata`` is a dict, not a section, and an entry without a ``type`` contributes
    no type."""
    features = extract_features(
        {"metadata": {"hs3_version": "0.2"}, "functions": [{"name": "f"}, "not-a-dict"]}
    )
    assert features["sections"] == ["functions"]
    assert features["types"] == []


# --------------------------------------------------------------------------------------
# The metadata schema and the generator have to agree on what a fixture may declare
# --------------------------------------------------------------------------------------


def test_metadata_may_declare_backend_expectations(suite_factory) -> None:
    """``build_manifest_and_expected.py`` reads them from metadata, so the schema that
    guards metadata has to allow them."""
    metadata = default_metadata("synth_basics")
    metadata["backend_expectations"] = {"fake": {"xfail": True, "reason": "known gap"}}
    assert validate_suite(suite_factory(metadata=metadata).root) == []


def test_metadata_backend_expectations_reject_unknown_keys(suite_factory) -> None:
    metadata = default_metadata("synth_basics")
    metadata["backend_expectations"] = {"fake": {"xfail": True, "why": "typo"}}
    assert validate_suite(suite_factory(metadata=metadata).root)


def test_metadata_must_name_the_reference_backend(suite_factory) -> None:
    """CI uses this field directly as the generating container image, so a fixture
    without one cannot be generated at all."""
    metadata = default_metadata("synth_basics")
    del metadata["reference_backend"]
    errors = validate_suite(suite_factory(metadata=metadata).root)
    assert any("reference_backend" in error for error in errors)


# --------------------------------------------------------------------------------------
# Hash verification reports, it does not raise
# --------------------------------------------------------------------------------------


def test_verify_hashes_reports_a_malformed_model_instead_of_raising(suite_factory) -> None:
    """The canonical hash has to parse the file; failing to do so is a finding, not a crash."""
    suite = suite_factory()
    (suite.fixture_dir("synth_basics") / "hs3.json").write_text("{not json", encoding="utf-8")

    errors = verify_hashes(suite.root, suite.manifest)
    assert "synth_basics: raw sha256 mismatch" in errors
    assert any("hs3.json is not valid JSON" in error for error in errors)


def test_verify_hashes_reports_an_unreadable_model_instead_of_raising(suite_factory) -> None:
    suite = suite_factory()
    (suite.fixture_dir("synth_basics") / "hs3.json").unlink()

    errors = verify_hashes(suite.root, suite.manifest)
    assert len(errors) == 1
    assert errors[0].startswith("synth_basics: cannot read hs3.json: ")


def test_one_broken_fixture_does_not_hide_the_next_one(suite_factory) -> None:
    """Each fixture is reported on its own; the loop does not stop at the first casualty."""
    suite = suite_factory(test_ids=("broken", "also_broken"))
    for test_id in ("broken", "also_broken"):
        (suite.fixture_dir(test_id) / "hs3.json").write_text("{not json", encoding="utf-8")

    errors = verify_hashes(suite.root, suite.manifest)
    assert sum("is not valid JSON" in error for error in errors) == 2


def test_verify_hashes_reports_a_non_utf8_model_instead_of_raising(suite_factory) -> None:
    suite = suite_factory()
    (suite.fixture_dir("synth_basics") / "hs3.json").write_bytes(b'{"name": "\xff\xfe"}')

    errors = verify_hashes(suite.root, suite.manifest)
    assert any("hs3.json is not valid JSON" in error for error in errors)


@pytest.mark.parametrize("name", ["metadata.json", "expected.json"])
@pytest.mark.parametrize("content", [b"{not json", b'{"name": "\xff\xfe"}'])
def test_validate_suite_reports_an_unparseable_fixture_file_instead_of_raising(
    suite_factory, name: str, content: bytes
) -> None:
    suite = suite_factory()
    (suite.fixture_dir("synth_basics") / name).write_bytes(content)

    errors = validate_suite(suite.root)
    assert len(errors) == 1
    assert errors[0].startswith(f"fixtures/synth_basics/{name}: not valid JSON: ")
