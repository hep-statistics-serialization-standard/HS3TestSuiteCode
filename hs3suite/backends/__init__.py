"""Backend adapters for HS3TestSuite."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_BACKENDS = {
    "pyhs3": ("hs3suite.backends.pyhs3", "PyHS3Backend"),
    "roofit": ("hs3suite.backends.roofit", "RooFitBackend"),
}


def available_backends() -> tuple[str, ...]:
    return tuple(sorted(_BACKENDS))


def build_backend(name: str) -> Any:
    try:
        module_name, class_name = _BACKENDS[name]
    except KeyError:
        available = ", ".join(available_backends())
        raise ValueError(f"unsupported backend {name!r}; available: {available}") from None

    module = import_module(module_name)
    backend_class = getattr(module, class_name)
    return backend_class()
