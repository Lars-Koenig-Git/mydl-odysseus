"""Local GGUF model loading boundary for the MyDL Odysseus runtime."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import RuntimeConfig


@dataclass(frozen=True, slots=True)
class LoadedModel:
    """Opaque loaded backend handle kept alive by runtime state."""

    backend: str
    model_path: Path
    handle: Any


@dataclass(frozen=True, slots=True)
class ModelLoadResult:
    """Secret-free model load result."""

    loaded: bool
    reason: str
    model: LoadedModel | None = None

    @classmethod
    def success(cls, model: LoadedModel) -> ModelLoadResult:
        return cls(loaded=True, reason="ok", model=model)

    @classmethod
    def failure(cls, reason: str) -> ModelLoadResult:
        return cls(loaded=False, reason=reason, model=None)


def load_configured_model(config: RuntimeConfig) -> ModelLoadResult:
    """Initialize the configured GGUF model with a real local backend.

    File existence is necessary but never sufficient for readiness. The runtime
    is ready only after the backend constructor returns a live model object.
    """

    model_path = config.model_path
    if not model_path.is_file():
        return ModelLoadResult.failure("model_file_missing")

    try:
        llama_cpp = importlib.import_module("llama_cpp")
    except ImportError:
        return ModelLoadResult.failure("backend_unavailable")

    llama_class = getattr(llama_cpp, "Llama", None)
    if llama_class is None:
        return ModelLoadResult.failure("backend_unavailable")

    try:
        handle = llama_class(model_path=str(model_path))
    except Exception:
        return ModelLoadResult.failure("load_failed")

    return ModelLoadResult.success(
        LoadedModel(backend="llama_cpp", model_path=model_path, handle=handle),
    )
