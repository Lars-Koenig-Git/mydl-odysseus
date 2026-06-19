from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mydl_odysseus.config import ConfigError
from mydl_odysseus.managed_model_selection import (
    ManagedModelSelection,
    discover_managed_model_candidates,
    load_selected_model,
    save_selected_model,
    validate_selected_model,
)

MODEL_REPO_ID = "Qwen/Qwen3-4B-GGUF"
MODEL_FILE = "Qwen3-4B-Q4_K_M.gguf"


def _snapshot(tmp_path: Path) -> Path:
    return tmp_path / "hub" / "models--Qwen--Qwen3-4B-GGUF" / "snapshots" / "abc123"


def _model(tmp_path: Path, name: str = MODEL_FILE, content: bytes = b"model") -> Path:
    path = _snapshot(tmp_path) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_discovers_only_complete_main_gguf_candidates(tmp_path: Path) -> None:
    model = _model(tmp_path)
    _model(tmp_path, "mmproj-qwen.gguf")
    _model(tmp_path, "empty.gguf", b"")
    (_snapshot(tmp_path) / "Qwen3-4B-Q4_K_M.gguf.incomplete").write_bytes(b"partial")

    candidates = discover_managed_model_candidates([tmp_path / "hub"])

    assert [candidate.selection.model_path for candidate in candidates] == [model]
    assert candidates[0].selection.model_source == "odysseus-cookbook"
    assert candidates[0].selection.model_repo_id == MODEL_REPO_ID
    assert candidates[0].selection.model_file == MODEL_FILE
    assert candidates[0].selection.model_cache_root == tmp_path / "hub"
    assert candidates[0].selection.model_snapshot_path == _snapshot(tmp_path)
    assert candidates[0].size_bytes == len(b"model")


def test_selected_state_is_explicit_and_validated(tmp_path: Path) -> None:
    model = _model(tmp_path)
    state = tmp_path / "state.json"
    selection = ManagedModelSelection(
        model_source="odysseus-cookbook",
        model_repo_id=MODEL_REPO_ID,
        model_file=MODEL_FILE,
        model_path=model,
        model_cache_root=tmp_path / "hub",
    )

    assert load_selected_model(state) is None
    save_selected_model(selection, state)

    assert load_selected_model(state) == selection
    assert validate_selected_model(state) == selection


def test_selected_state_rejects_arbitrary_model_path(tmp_path: Path) -> None:
    model = tmp_path / MODEL_FILE
    model.write_bytes(b"model")
    state = tmp_path / "state.json"
    selection = ManagedModelSelection(
        model_source="odysseus-cookbook",
        model_repo_id=MODEL_REPO_ID,
        model_file=MODEL_FILE,
        model_path=model,
    )

    with pytest.raises(ConfigError):
        save_selected_model(selection, state)


def test_cli_managed_model_selection_flow(tmp_path: Path) -> None:
    model = _model(tmp_path)
    state = tmp_path / "state.json"
    root = Path(__file__).resolve().parents[1]
    base = [
        sys.executable,
        "-m",
        "mydl_odysseus",
        "--managed-model-state",
        str(state),
        "--managed-model-cache-root",
        str(tmp_path / "hub"),
    ]

    selected = subprocess.run(
        [*base, "--selected-managed-model"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert selected.returncode == 0, selected.stderr
    assert json.loads(selected.stdout) is None

    listed = subprocess.run(
        [*base, "--list-managed-models"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout)["models"][0]["model_path"] == str(model)

    selected = subprocess.run(
        [*base, "--select-managed-model", str(model)],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert selected.returncode == 0, selected.stderr
    assert json.loads(selected.stdout)["model_path"] == str(model)

    validated = subprocess.run(
        [*base, "--validate-selected-managed-model"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)["model_repo_id"] == MODEL_REPO_ID


def test_cli_validate_selected_model_fails_when_absent(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mydl_odysseus",
            "--managed-model-state",
            str(tmp_path / "state.json"),
            "--validate-selected-managed-model",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 2
    assert "managed model selection" in proc.stderr


def test_odysseus_dispatcher_passes_model_selection_flags_to_runtime(tmp_path: Path) -> None:
    dispatcher = Path(__file__).resolve().parents[1] / "scripts" / "odysseus"
    proc = subprocess.run(
        [
            str(dispatcher),
            "--managed-model-state",
            str(tmp_path / "state.json"),
            "--selected-managed-model",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) is None
