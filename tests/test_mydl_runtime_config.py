from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mydl_odysseus.config import (
    BRAIN_STORE_KEY_ID,
    FORBIDDEN_RAW_SECRET_KEYS,
    MODEL_SOURCE_ODYSSEUS_COOKBOOK,
    ConfigError,
    load_runtime_config,
)

MCP_SECRET = "mcp-token-secret-value"
BRAIN_SECRET = "brain-store-token-secret-value"
MODEL_REPO_ID = "Qwen/Qwen3-4B-GGUF"
MODEL_FILE = "Qwen3-4B-Q4_K_M.gguf"


def _model_path(tmp_path: Path, *, file_name: str = MODEL_FILE) -> Path:
    return (
        tmp_path
        / "hub"
        / "models--Qwen--Qwen3-4B-GGUF"
        / "snapshots"
        / "abc123"
        / file_name
    )


def _write_config(tmp_path: Path, **overrides: object) -> Path:
    model = _model_path(tmp_path)
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"model")
    values: dict[str, object] = {
        "bind_address": "127.0.0.1",
        "port": 0,
        "model_source": MODEL_SOURCE_ODYSSEUS_COOKBOOK,
        "model_repo_id": MODEL_REPO_ID,
        "model_file": MODEL_FILE,
        "model_path": str(model),
        "hub_mcp_url": "http://127.0.0.1:8000/hub/ai/mcp",
        "social_mcp_url": "http://localhost:8000/social/ai/mcp",
        "mcp_bearer": MCP_SECRET,
        "brain_store_url": "http://127.0.0.1:8000/odysseus/brain-store/v1",
        "brain_store_bearer": BRAIN_SECRET,
        "brain_store_key_id": BRAIN_STORE_KEY_ID,
    }
    for key, value in overrides.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    body = "\n".join(f"{key} = {_toml_value(value)}" for key, value in values.items()) + "\n"
    path = tmp_path / "odysseus.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _toml_value(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    return f'"{value}"'


def test_valid_config_parsing(tmp_path: Path) -> None:
    config = load_runtime_config(_write_config(tmp_path))

    assert config.bind_address == "127.0.0.1"
    assert config.port == 0
    assert config.model_source == MODEL_SOURCE_ODYSSEUS_COOKBOOK
    assert config.model_repo_id == MODEL_REPO_ID
    assert config.model_file == MODEL_FILE
    assert config.model_path.is_file()
    assert config.brain_store_key_id == BRAIN_STORE_KEY_ID


def test_model_path_existence_is_checked_by_loader_not_config(tmp_path: Path) -> None:
    missing = (
        tmp_path
        / "hub"
        / "models--Qwen--Qwen3-4B-GGUF"
        / "snapshots"
        / "missing"
        / MODEL_FILE
    )

    config = load_runtime_config(_write_config(tmp_path, model_path=missing))

    assert config.model_path == missing
    assert not config.model_path.exists()


def test_missing_required_fields_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_runtime_config(_write_config(tmp_path, hub_mcp_url=None))


def test_arbitrary_model_path_rejected_even_when_file_exists(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"model")

    with pytest.raises(ConfigError):
        load_runtime_config(_write_config(tmp_path, model_path=model))


def test_projector_model_file_rejected(tmp_path: Path) -> None:
    model = _model_path(tmp_path, file_name="mmproj-qwen.gguf")
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"projector")

    with pytest.raises(ConfigError):
        load_runtime_config(
            _write_config(
                tmp_path,
                model_file="mmproj-qwen.gguf",
                model_path=model,
            ),
        )


def test_model_snapshot_path_can_prove_provenance(tmp_path: Path) -> None:
    snapshot = (
        tmp_path
        / "hub"
        / "models--Qwen--Qwen3-4B-GGUF"
        / "snapshots"
        / "abc123"
    )
    model = snapshot / MODEL_FILE
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"model")

    config = load_runtime_config(
        _write_config(tmp_path, model_path=model, model_snapshot_path=snapshot),
    )

    assert config.model_snapshot_path == snapshot


def test_model_cache_root_must_contain_model_path(tmp_path: Path) -> None:
    outside = tmp_path / "outside"

    with pytest.raises(ConfigError):
        load_runtime_config(_write_config(tmp_path, model_cache_root=outside))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bind_address", "0.0.0.0"),
        ("hub_mcp_url", "http://192.168.1.3:8000/hub/ai/mcp"),
        ("social_mcp_url", "https://127.0.0.1:8000/social/ai/mcp"),
        ("brain_store_url", "http://example.com/odysseus/brain-store/v1"),
    ],
)
def test_non_loopback_values_rejected(tmp_path: Path, field: str, value: str) -> None:
    with pytest.raises(ConfigError):
        load_runtime_config(_write_config(tmp_path, **{field: value}))


def test_same_mcp_and_brain_store_bearer_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_runtime_config(_write_config(tmp_path, brain_store_bearer=MCP_SECRET))


def test_unsupported_brain_store_key_id_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_runtime_config(_write_config(tmp_path, brain_store_key_id="mydl.unsupported"))


@pytest.mark.parametrize("field", sorted(FORBIDDEN_RAW_SECRET_KEYS))
def test_raw_brain_key_like_fields_rejected(tmp_path: Path, field: str) -> None:
    with pytest.raises(ConfigError):
        load_runtime_config(_write_config(tmp_path, **{field: "forbidden"}))


def test_redacted_summary_does_not_leak_secrets(tmp_path: Path) -> None:
    config = load_runtime_config(_write_config(tmp_path))

    summary = repr(config.redacted_summary())

    assert MCP_SECRET not in summary
    assert BRAIN_SECRET not in summary
    assert "mcp_bearer" not in summary
    assert "brain_store_bearer" not in summary
    for field in FORBIDDEN_RAW_SECRET_KEYS:
        assert field not in summary


def test_cli_check_config_accepts_valid_config(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mydl_odysseus", "--config", str(_write_config(tmp_path)), "--check-config"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "valid" in proc.stdout


def test_cli_check_config_rejects_invalid_config_without_secret_leaks(tmp_path: Path) -> None:
    path = _write_config(tmp_path, brain_store_bearer=MCP_SECRET, brain_store_key="raw-secret")

    proc = subprocess.run(
        [sys.executable, "-m", "mydl_odysseus", "--config", str(path), "--check-config"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    combined = proc.stdout + proc.stderr

    assert proc.returncode != 0
    assert MCP_SECRET not in combined
    assert BRAIN_SECRET not in combined
    assert "mcp_bearer" not in combined
    assert "brain_store_bearer" not in combined
    for field in FORBIDDEN_RAW_SECRET_KEYS:
        assert field not in combined
