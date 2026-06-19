from __future__ import annotations

import types
from pathlib import Path

from mydl_odysseus.config import BRAIN_STORE_KEY_ID, RuntimeConfig
from mydl_odysseus.model_loader import load_configured_model

MCP_SECRET = "loader-mcp-secret"
BRAIN_SECRET = "loader-brain-secret"
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


def _config(model_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        bind_address="127.0.0.1",
        port=0,
        model_path=model_path,
        hub_mcp_url="http://127.0.0.1:8010/hub/ai/mcp",
        social_mcp_url="http://127.0.0.1:8010/social/ai/mcp",
        mcp_bearer=MCP_SECRET,
        brain_store_url="http://127.0.0.1:8010/odysseus/brain-store/v1",
        brain_store_bearer=BRAIN_SECRET,
        brain_store_key_id=BRAIN_STORE_KEY_ID,
    )


def test_missing_model_file_fails_closed(tmp_path: Path) -> None:
    result = load_configured_model(_config(tmp_path / "missing.gguf"))

    assert result.loaded is False
    assert result.model is None
    assert result.reason == "model_file_missing"


def test_unavailable_backend_fails_closed(tmp_path: Path, monkeypatch) -> None:
    model = tmp_path / "model.gguf"
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
    model = tmp_path / "model.gguf"
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
    model = tmp_path / "model.gguf"
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
