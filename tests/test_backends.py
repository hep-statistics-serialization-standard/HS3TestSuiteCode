"""The plugin seam and the scan specs: the two places that reject bad input early."""

from __future__ import annotations

import pytest

from hs3suite.backends import (
    DEFAULT_CLASS,
    DEFAULT_MODULE,
    ENV_VAR,
    backend_name,
    build_backend,
    default_spec,
    load_backend_class,
    split_spec,
)
from hs3suite.specs import FixtureSpec, FunctionScanSpec, NLLScanSpec, PdfScanSpec


def test_split_spec_defaults_the_class_when_only_a_module_is_given() -> None:
    assert split_spec("mybackend") == ("mybackend", DEFAULT_CLASS)
    assert split_spec("mybackend:Other") == ("mybackend", "Other")


@pytest.mark.parametrize("spec", ["", ":Class", "mybackend:"])
def test_split_spec_rejects_malformed_specs(spec: str) -> None:
    with pytest.raises(ValueError, match="malformed backend spec"):
        split_spec(spec)


def test_default_spec_prefers_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert default_spec() == DEFAULT_MODULE
    monkeypatch.setenv(ENV_VAR, "other_backend:Other")
    assert default_spec() == "other_backend:Other"


def test_load_backend_class_imports_the_installed_plugin(install_plugin) -> None:
    install_plugin()
    assert load_backend_class("hs3suite_backend").name == "installed-fake"


def test_load_backend_class_uses_the_default_spec(
    install_plugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_plugin()
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert build_backend().name == "installed-fake"


def test_missing_plugin_points_at_the_contract() -> None:
    with pytest.raises(RuntimeError, match="cannot import backend module 'nosuchbackend'"):
        load_backend_class("nosuchbackend")


def test_a_plugin_whose_own_imports_fail_is_not_reported_as_missing(install_plugin) -> None:
    """A plugin that cannot ``import ROOT`` must not look like an absent plugin."""
    install_plugin("broken_backend", "import definitely_not_installed_xyz\n")
    with pytest.raises(RuntimeError) as excinfo:
        load_backend_class("broken_backend")
    message = str(excinfo.value)
    assert "cannot import backend module 'broken_backend'" in message
    assert "definitely_not_installed_xyz" in message


def test_missing_class_lists_the_candidates_it_found(install_plugin) -> None:
    install_plugin("classless_backend", "class AlternativeBackend:\n    name = 'alt'\n")
    with pytest.raises(RuntimeError) as excinfo:
        load_backend_class("classless_backend")
    message = str(excinfo.value)
    assert f"defines no {DEFAULT_CLASS}" in message
    assert "AlternativeBackend" in message
    assert "--backend classless_backend:<Class>" in message


def test_backend_name_falls_back_to_the_spec_for_a_nameless_plugin() -> None:
    class Nameless:
        pass

    assert backend_name(Nameless(), "some_module:Some") == "some_module:Some"


def test_backend_name_prefers_the_plugins_own_name(fake_backend) -> None:
    """The manifest keys ``backend_expectations`` by this, not by the CLI spec."""
    assert backend_name(fake_backend, "whatever_module") == "fake"


# --------------------------------------------------------------------------------------
# Scan specs
# --------------------------------------------------------------------------------------


def test_scan_specs_reject_incomplete_targets() -> None:
    points = ((1.0,),)
    with pytest.raises(ValueError, match="observables must be non-empty"):
        PdfScanSpec(parameters=("x",), points=points, pdf="g")
    with pytest.raises(ValueError, match="pdf is required"):
        PdfScanSpec(parameters=("x",), points=points, observables=("x",))
    with pytest.raises(ValueError, match="function is required"):
        FunctionScanSpec(parameters=("x",), points=points)
    with pytest.raises(ValueError, match="exactly one of"):
        NLLScanSpec(parameters=("x",), points=points, pdf="p", data="d", likelihood="l")
    with pytest.raises(ValueError, match="pdf and data must both be set or both omitted"):
        NLLScanSpec(parameters=("x",), points=points, pdf="p")


def test_scan_specs_validate_point_arity_from_the_base_class() -> None:
    """Arity checking lives on ScanSpec, so every subclass inherits it."""
    for make in (
        lambda: PdfScanSpec(parameters=("x",), points=((1.0, 2.0),), pdf="g", observables=("x",)),
        lambda: FunctionScanSpec(parameters=("x",), points=((1.0, 2.0),), function="f"),
        lambda: NLLScanSpec(parameters=("x",), points=((1.0, 2.0),), likelihood="l"),
    ):
        with pytest.raises(ValueError, match="expected 1"):
            make()


def test_scan_specs_require_parameters() -> None:
    with pytest.raises(ValueError, match="parameters must be non-empty"):
        FunctionScanSpec(parameters=(), points=(), function="f")


def test_nll_scan_target_switches_on_likelihood_versus_pdf_and_data() -> None:
    pdf_data = NLLScanSpec.one_dim("gauss", "gaussData", "mu", (0.0, 1.0))
    assert pdf_data.target() == {"pdf": "gauss", "data": "gaussData"}
    assert NLLScanSpec(parameters=("mu",), points=((0.0,),), likelihood="L").target() == {
        "likelihood": "L"
    }


def test_scan_kind_matches_the_backend_method_name(fake_backend) -> None:
    """build_expected dispatches via getattr(backend, f"run_{kind}"); keep that true."""
    for cls in (NLLScanSpec, PdfScanSpec, FunctionScanSpec):
        assert hasattr(fake_backend, f"run_{cls.kind}")


def test_pdf_and_function_scans_use_a_tighter_atol_than_nll_scans() -> None:
    """Densities span orders of magnitude, so the NLL scan's atol would be far too slack."""
    assert PdfScanSpec.tolerance()["atol"] < NLLScanSpec.tolerance()["atol"]
    assert FunctionScanSpec.tolerance()["atol"] < NLLScanSpec.tolerance()["atol"]


def test_fixture_spec_rejects_check_id_collision_across_kinds() -> None:
    """Check ids must be unique across the whole expected.json, not just within one kind."""
    with pytest.raises(ValueError, match="duplicate check ids"):
        FixtureSpec(
            test_id="dupe",
            title="t",
            source="s",
            description="d",
            nll_scans=(NLLScanSpec.one_dim("p", "d", "x", (1.0,), id="shared"),),
            reference_backend="roofit",
            pdf_scans=(PdfScanSpec.one_dim("p", ("x",), "x", (1.0,), id="shared"),),
        )


def test_all_scans_reports_every_check_producing_spec_in_emission_order() -> None:
    spec = FixtureSpec(
        test_id="multi",
        title="t",
        source="s",
        description="d",
        nll_scans=(NLLScanSpec.one_dim("p", "d", "x", (1.0,)),),
        reference_backend="roofit",
        pdf_scans=(PdfScanSpec.one_dim("p", ("x",), "x", (1.0,)),),
        function_scans=(FunctionScanSpec.one_dim("f", "x", (1.0,)),),
    )
    assert [scan.id for scan in spec.all_scans()] == [
        "twice_delta_nll_scan",
        "pdf_scan",
        "function_scan",
    ]
