"""Shared machinery for the runner's own tests.

Everything here exists so the tests in this repository need nothing this repository
does not contain: no fixtures checkout, no backend plugin, no ROOT. A test gets a
synthetic suite written into ``tmp_path`` and a fake backend that answers the
contract in ``docs/reference-backend-contract.md``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest

from hs3suite.manifest import canonical_sha256, raw_sha256, write_json


CODE_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------------------
# Loading the tools, which are scripts rather than an importable package
# --------------------------------------------------------------------------------------


def load_tool(name: str) -> ModuleType:
    """Import ``tools/<name>.py`` by path.

    The tools stay scripts on purpose: the published RooFit image's
    ``generate-fixtures`` execs ``.hs3suite/tools/build_manifest_and_expected.py`` by
    path, so they are not moved into the package just to be importable here.
    """
    path = CODE_ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"hs3suite_tools_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def build_tool() -> ModuleType:
    return load_tool("build_manifest_and_expected")


@pytest.fixture(scope="session")
def upconvert_tool() -> ModuleType:
    return load_tool("upconvert_hs3")


# --------------------------------------------------------------------------------------
# Synthetic suites
# --------------------------------------------------------------------------------------


def hs3_payload() -> dict[str, Any]:
    """A minimal but structurally honest HS3 model: one pdf, one function, one dataset."""
    return {
        "distributions": [
            {"name": "gauss", "type": "gaussian_dist", "mean": "mu", "sigma": "sigma", "x": "x"}
        ],
        "functions": [{"name": "double_mu", "type": "product", "factors": ["mu", "two"]}],
        "data": [{"name": "gaussData", "type": "unbinned", "entries": [[0.0], [1.0]]}],
        "domains": [
            {
                "name": "default_domain",
                "type": "product_domain",
                "axes": [{"name": "x", "min": -10.0, "max": 10.0}],
            }
        ],
        "parameter_points": [
            {
                "name": "default_values",
                "parameters": [
                    {"name": "mu", "value": 1.0},
                    {"name": "sigma", "value": 2.0},
                    {"name": "two", "value": 2.0},
                ],
            }
        ],
        "metadata": {"hs3_version": "0.2"},
    }


def scan_check(check_id: str, kind: str, **overrides: Any) -> dict[str, Any]:
    """One quantitative check, valid against ``expected.schema.json`` for ``kind``."""
    targets = {
        "twice_delta_nll_scan": {"pdf": "gauss", "data": "gaussData"},
        "pdf_scan": {"pdf": "gauss", "observables": ["x"]},
        "function_scan": {"function": "double_mu"},
    }
    check = {
        "id": check_id,
        "kind": kind,
        "target": targets[kind],
        "reference_point": {"mu": 1.0, "sigma": 2.0},
        "scan_parameters": ["mu"],
        "scan_points": [[0.0], [1.0], [2.0]],
        "expected": [1.0, 0.0, 1.0],
        "tolerance": {"rtol": 1e-8, "atol": 1e-7},
        "comparison": {
            "type": "pointwise_comp",
            "rule": "|evaluated - expected| <= atol + rtol * |expected|",
        },
    }
    check.update(overrides)
    return check


def default_checks() -> list[dict[str, Any]]:
    """One check of every kind the runner dispatches, in the generator's own order."""
    return [
        {"id": "static_integrity", "kind": "static_integrity"},
        {
            "id": "structure_import",
            "kind": "structure_import",
            "target": {"pdfs": ["gauss"], "functions": ["double_mu"], "data": ["gaussData"]},
        },
        scan_check("twice_delta_nll_scan", "twice_delta_nll_scan"),
        scan_check("pdf_scan", "pdf_scan"),
        scan_check("function_scan", "function_scan"),
    ]


def default_metadata(test_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "test_id": test_id,
        "title": f"synthetic fixture {test_id}",
        "source": "hand-written for the runner's tests",
        "description": "A synthetic model that exercises the runner without a backend.",
        "notes": [],
        "modified_from_source": False,
        "reference_backend": "fake 1.0",
    }


@dataclass
class SyntheticSuite:
    """A fixtures-repository-shaped directory tree, plus the knobs to corrupt it."""

    root: Path
    manifest: dict[str, Any] = field(default_factory=dict)

    def fixture_dir(self, test_id: str) -> Path:
        return self.root / "fixtures" / test_id

    def read(self, test_id: str, name: str) -> Any:
        return json.loads((self.fixture_dir(test_id) / name).read_text())

    def write(self, test_id: str, name: str, payload: Any) -> None:
        """Overwrite one fixture file. Hashes are *not* refreshed — that is the point."""
        write_json(self.fixture_dir(test_id) / name, payload)

    def rewrite_manifest(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        write_json(self.root / "manifest.json", manifest)


@pytest.fixture
def suite_factory(tmp_path: Path) -> Callable[..., SyntheticSuite]:
    """Build a schema-valid suite in ``tmp_path``; override one piece to break one rule.

    Hashes go through the same ``raw_sha256``/``canonical_sha256`` the runner verifies
    with, so a suite this returns is valid by construction and any invalidity in a test
    is the one the test asked for.
    """
    counter = {"n": 0}

    def build(
        test_ids: tuple[str, ...] = ("synth_basics",),
        payload: dict[str, Any] | None = None,
        checks: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        backend_expectations: dict[str, dict[str, Any]] | None = None,
        manifest_extra: dict[str, Any] | None = None,
    ) -> SyntheticSuite:
        counter["n"] += 1
        root = tmp_path / f"suite{counter['n']}"
        suite = SyntheticSuite(root=root)
        entries = []

        for test_id in test_ids:
            fixture_dir = suite.fixture_dir(test_id)
            write_json(fixture_dir / "hs3.json", payload if payload is not None else hs3_payload())
            write_json(
                fixture_dir / "metadata.json",
                metadata if metadata is not None else default_metadata(test_id),
            )
            fixture_checks = default_checks() if checks is None else checks
            write_json(
                fixture_dir / "expected.json",
                {"schema_version": 1, "test_id": test_id, "checks": fixture_checks},
            )

            hs3_path = fixture_dir / "hs3.json"
            entry: dict[str, Any] = {
                "test_id": test_id,
                "path": f"fixtures/{test_id}",
                "hashes": {
                    "sha256": raw_sha256(hs3_path),
                    "canonical_sha256": canonical_sha256(hs3_path),
                },
                "features": {"sections": [], "types": [], "semantic": []},
                "tags": ["synthetic"],
                "conformance": ["basic"],
                "checks": [check["id"] for check in fixture_checks],
            }
            if backend_expectations:
                entry["backend_expectations"] = backend_expectations
            entries.append(entry)

        manifest = {"schema_version": 1, "generated_by": "tests", "fixtures": entries}
        manifest.update(manifest_extra or {})
        suite.rewrite_manifest(manifest)
        return suite

    return build


# --------------------------------------------------------------------------------------
# A backend that implements the contract without importing anything
# --------------------------------------------------------------------------------------


class FakeBackend:
    """An in-process plugin: every contract method, plus a record of how it was called.

    ``load_workspace`` returning a fresh object per call is what lets a test assert the
    runner loads one workspace per fixture rather than one per check.
    """

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.loads: list[Path] = []
        self.results: dict[str, list[float]] = {}
        self.fail_on: dict[str, Exception] = {}

    def _record(self, method: str, check: dict[str, Any]) -> list[float]:
        self.calls.append((method, check.get("id")))
        if method in self.fail_on:
            raise self.fail_on[method]
        return self.results.get(method, list(check.get("expected", [])))

    def load_workspace(self, hs3_path: Path) -> object:
        self.loads.append(Path(hs3_path))
        if "load_workspace" in self.fail_on:
            raise self.fail_on["load_workspace"]
        return object()

    def structure(self, workspace: object) -> dict[str, list[str]]:
        return {"pdfs": ["gauss"], "functions": ["double_mu"], "data": ["gaussData"]}

    def run_structure_check(self, workspace: object, check: dict[str, Any]) -> None:
        self.calls.append(("run_structure_check", check.get("id")))
        if "run_structure_check" in self.fail_on:
            raise self.fail_on["run_structure_check"]

    def run_twice_delta_nll_scan(
        self, workspace: object, check: dict[str, Any], hs3_path: Path
    ) -> list[float]:
        return self._record("run_twice_delta_nll_scan", check)

    def run_pdf_scan(
        self, workspace: object, check: dict[str, Any], hs3_path: Path
    ) -> list[float]:
        return self._record("run_pdf_scan", check)

    def run_function_scan(
        self, workspace: object, check: dict[str, Any], hs3_path: Path
    ) -> list[float]:
        return self._record("run_function_scan", check)

    def upconvert(self, input_path: Path, output_path: Path, *, verbose: bool = False) -> None:
        self.calls.append(("upconvert", str(input_path)))
        if "upconvert" in self.fail_on:
            raise self.fail_on["upconvert"]
        Path(output_path).write_text(Path(input_path).read_text(), encoding="utf-8")


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend()


PLUGIN_SOURCE = '''
"""A stand-alone backend plugin, exactly as an image would install it."""


class HS3TestSuiteBackend:
    name = "installed-fake"


class AlternativeBackend:
    name = "alternative-fake"
'''


@pytest.fixture
def install_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """Write a real module onto ``sys.path`` under a given name.

    ``load_backend_class`` does a genuine ``import_module``, so the only honest way to
    test it is to give it something genuinely importable.
    """

    def install(module_name: str = "hs3suite_backend", source: str = PLUGIN_SOURCE) -> Path:
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir(exist_ok=True)
        path = plugin_dir / f"{module_name}.py"
        path.write_text(source, encoding="utf-8")
        monkeypatch.syspath_prepend(str(plugin_dir))
        monkeypatch.delitem(sys.modules, module_name, raising=False)
        return path

    return install
