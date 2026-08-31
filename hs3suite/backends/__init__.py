"""Loading of the backend plugin that executes HS3 models.

This suite ships no backend implementation. A backend arrives as a plugin module on
``PYTHONPATH`` — in practice installed by a reference-backend Docker image, see
``docs/reference-backend-contract.md``. Both the runner and
``tools/build_manifest_and_expected.py`` resolve it through here, so a broken plugin
fails the same way everywhere.
"""

from __future__ import annotations

import os
from importlib import import_module
from typing import Any


DEFAULT_MODULE = "hs3suite_backend"
DEFAULT_CLASS = "HS3TestSuiteBackend"
ENV_VAR = "HS3SUITE_BACKEND"


def default_spec() -> str:
    return os.environ.get(ENV_VAR) or DEFAULT_MODULE


def split_spec(spec: str) -> tuple[str, str]:
    """Split a ``module`` or ``module:Class`` spec into its two halves."""
    module_name, separator, class_name = spec.partition(":")
    if not module_name or (separator and not class_name):
        raise ValueError(f"malformed backend spec {spec!r}; expected 'module' or 'module:Class'")
    return module_name, class_name or DEFAULT_CLASS


def load_backend_class(spec: str | None = None) -> type:
    """Import the backend class named by ``spec``.

    The two failure modes are reported separately on purpose: a plugin whose own
    dependencies are missing (``import ROOT`` inside the plugin) must not look like a
    missing plugin.
    """
    module_name, class_name = split_spec(spec or default_spec())

    try:
        module = import_module(module_name)
    except ImportError as exc:
        raise RuntimeError(
            f"cannot import backend module {module_name!r}: {exc}. "
            "A backend is supplied as a plugin module on PYTHONPATH, usually by a "
            "reference-backend image; see docs/reference-backend-contract.md."
        ) from exc

    try:
        backend_class = getattr(module, class_name)
    except AttributeError:
        candidates = sorted(
            name for name, value in vars(module).items() if isinstance(value, type)
        )
        hint = (
            f" It defines: {', '.join(candidates)}. Select one with --backend "
            f"{module_name}:<Class>."
            if candidates
            else ""
        )
        origin = getattr(module, "__file__", None) or "unknown location"
        raise RuntimeError(
            f"backend module {module_name!r} ({origin}) defines no {class_name}.{hint}"
        ) from None

    return backend_class


def build_backend(spec: str | None = None) -> Any:
    """Import and instantiate the backend named by ``spec``."""
    return load_backend_class(spec)()


def backend_name(backend: Any, spec: str | None = None) -> str:
    """The backend's own name, used as the ``backend_expectations`` key.

    Falls back to the spec so a plugin that declares no ``name`` still gets a stable,
    reportable label.
    """
    return getattr(backend, "name", None) or spec or default_spec()
