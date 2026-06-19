from __future__ import annotations

import types
from pathlib import Path

from mydl_odysseus.config import (
    BRAIN_STORE_KEY_ID,
    MODEL_SOURCE_ODYSSEUS_COOKBOOK,
    RuntimeConfig,
)
from mydl_odysseus.model_loader import load_configured_model

MCP_SECRET = "loader-mcp-secret"
BRAIN_SECRET = "loader-brain-secret"
MODEL_REPO_ID = "Qwen/Qwen3-4B-GGUF"
MODEL_FILE = "Qwen3-4B-Q4_K_M.gguf"
FORBIDDEN_OUTPUT = (
    MCP_SECRET,
    BRAIN_SECRET,
    "mcp_bearer",
    "brain_store_bearer",
    "brain_key",
    "brainKey",
    "brainStoreKey",
    "brain_store_key",
    "brain_store_key_hex",
    "mnemonic",
    "seed",
)


def _model_path(tmp_path: Path, *, snapshot: str = "abc123") -> Path:
    return (
        tmp_path
        / "hub"
        / "models--Qwen--Qwen3-4B-GGUF"
        / "snapshots"
        / snapshot
        / MODEL_FILE
    )


def _config(model_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        bind_address="127.0.0.1",
        port=0,
        model_source=MODEL_SOURCE_ODYSSEUS_COOKBOOK,
        model_repo_id=MODEL_REPO_ID,
        model_file=MODEL_FILE,
        model_path=model_path,
        model_cache_root=None,
        model_snapshot_path=None,
        hub_mcp_url="http://127.0.0.1:8010/hub/ai/mcp",
        social_mcp_url="http://127.0.0.1:8010/social/ai/mcp",
        mcp_bearer=MCP_SECRET,
        brain_store_url="http://127.0.0.1:8010/odysseus/brain-store/v1",
        brain_store_bearer=BRAIN_SECRET,
        brain_store_key_id=BRAIN_STORE_KEY_ID,
    )


def test_missing_model_file_fails_closed(tmp_path: Path) -> None:
    result = load_configured_model(_config(_model_path(tmp_path, snapshot="missing")))

    assert result.loaded is False
    assert result.model is None
    assert result.reason == "managed_model_missing"


def test_arbitrary_model_file_fails_closed(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"model")

    result = load_configured_model(_config(model))

    assert result.loaded is False
    assert result.model is None
    assert result.reason == "managed_model_missing"


def test_unavailable_backend_fails_closed(tmp_path: Path, monkeypatch) -> None:
    model = _model_path(tmp_path)
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"model")

    def missing_backend(name: str) -> object:
        assert name == "llama_cpp"
        raise ImportError(name)

    monkeypatch.setattr("mydl_odysseus.model_loader.importlib.import_module", missing_backend)

    result = load_configured_model(_config(model))

    assert result.loaded is False
    assert result.model is None
    assert result.reason == "backend_unavailable"


def test_fake_llama_cpp_backend_initializes_and_sets_loaded(tmp_path: Path, monkeypatch) -> None:
    model = _model_path(tmp_path)
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"model")
    calls: list[str] = []

    class FakeLlama:
        def __init__(self, *, model_path: str) -> None:
            calls.append(model_path)

    monkeypatch.setattr(
        "mydl_odysseus.model_loader.importlib.import_module",
        lambda name: types.SimpleNamespace(Llama=FakeLlama),
    )

    result = load_configured_model(_config(model))

    assert result.loaded is True
    assert result.reason == "ok"
    assert result.model is not None
    assert result.model.backend == "llama_cpp"
    assert result.model.model_path == model
    assert calls == [str(model)]


def test_backend_load_exception_fails_closed_without_secret_leaks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    model = _model_path(tmp_path)
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"model")

    class FailingLlama:
        def __init__(self, *, model_path: str) -> None:
            raise RuntimeError(
                f"boom {model_path} {MCP_SECRET} {BRAIN_SECRET} mcp_bearer seed",
            )

    monkeypatch.setattr(
        "mydl_odysseus.model_loader.importlib.import_module",
        lambda name: types.SimpleNamespace(Llama=FailingLlama),
    )

    result = load_configured_model(_config(model))
    rendered = repr(result)

    assert result.loaded is False
    assert result.model is None
    assert result.reason == "load_failed"
    for forbidden in FORBIDDEN_OUTPUT:
        assert forbidden not in rendered
