"""Odysseus-owned managed GGUF model selection state."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import (
    BRAIN_STORE_KEY_ID,
    MODEL_SOURCE_ODYSSEUS_COOKBOOK,
    ConfigError,
    RuntimeConfig,
    validate_managed_model_provenance,
)

STATE_FILE_NAME = "mydl_managed_model_selection.json"


@dataclass(frozen=True, slots=True)
class ManagedModelSelection:
    model_source: str
    model_repo_id: str
    model_file: str
    model_path: Path
    model_cache_root: Path | None = None
    model_snapshot_path: Path | None = None

    def to_json(self) -> dict[str, str | None]:
        return {
            "model_source": self.model_source,
            "model_repo_id": self.model_repo_id,
            "model_file": self.model_file,
            "model_path": str(self.model_path),
            "model_cache_root": str(self.model_cache_root) if self.model_cache_root else None,
            "model_snapshot_path": str(self.model_snapshot_path)
            if self.model_snapshot_path
            else None,
        }

    @classmethod
    def from_json(cls, value: Any) -> "ManagedModelSelection":
        if not isinstance(value, dict):
            raise ConfigError("managed model selection must be an object")
        try:
            return cls(
                model_source=_expect_text(value["model_source"], "model_source"),
                model_repo_id=_expect_text(value["model_repo_id"], "model_repo_id"),
                model_file=_expect_text(value["model_file"], "model_file"),
                model_path=Path(_expect_text(value["model_path"], "model_path")),
                model_cache_root=_optional_path(value.get("model_cache_root"), "model_cache_root"),
                model_snapshot_path=_optional_path(
                    value.get("model_snapshot_path"),
                    "model_snapshot_path",
                ),
            )
        except KeyError as exc:
            raise ConfigError("managed model selection is missing a required field") from exc


@dataclass(frozen=True, slots=True)
class ManagedModelCandidate:
    selection: ManagedModelSelection
    size_bytes: int

    def to_json(self) -> dict[str, str | int | None]:
        payload = self.selection.to_json()
        payload["size_bytes"] = self.size_bytes
        return payload


def default_state_path() -> Path:
    data_dir = os.environ.get("ODYSSEUS_DATA_DIR") or os.environ.get("DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / STATE_FILE_NAME
    return Path(__file__).resolve().parents[1] / "data" / STATE_FILE_NAME


def discover_managed_model_candidates(cache_roots: list[Path] | None = None) -> list[ManagedModelCandidate]:
    roots = cache_roots or default_cache_roots()
    candidates: list[ManagedModelCandidate] = []
    seen: set[Path] = set()
    for root in roots:
        hub = root.expanduser()
        if not hub.is_dir():
            continue
        for repo_dir in sorted(hub.glob("models--*")):
            if not repo_dir.is_dir():
                continue
            repo_id = _repo_id_from_cache_dir(repo_dir.name)
            if repo_id is None:
                continue
            snapshots = repo_dir / "snapshots"
            if not snapshots.is_dir():
                continue
            for snapshot in sorted(snapshots.iterdir()):
                if not snapshot.is_dir():
                    continue
                for model_path in sorted(snapshot.rglob("*.gguf")):
                    resolved = model_path.resolve(strict=False)
                    if resolved in seen or not _is_complete_main_gguf(model_path):
                        continue
                    selection = ManagedModelSelection(
                        model_source=MODEL_SOURCE_ODYSSEUS_COOKBOOK,
                        model_repo_id=repo_id,
                        model_file=str(model_path.relative_to(snapshot)),
                        model_path=model_path,
                        model_cache_root=hub,
                        model_snapshot_path=snapshot,
                    )
                    try:
                        validate_selection(selection)
                    except ConfigError:
                        continue
                    seen.add(resolved)
                    candidates.append(
                        ManagedModelCandidate(
                            selection=selection,
                            size_bytes=model_path.stat().st_size,
                        ),
                    )
    return candidates


def default_cache_roots() -> list[Path]:
    roots: list[Path] = []
    for raw in _cookbook_model_dirs():
        root = _normalize_cache_root(Path(os.path.expandvars(raw)).expanduser())
        if root not in roots:
            roots.append(root)

    hf_home = Path(os.environ.get("HF_HOME") or "~/.cache/huggingface").expanduser()
    for root in (
        hf_home / "hub",
        Path(__file__).resolve().parents[1] / "data" / "huggingface" / "hub",
    ):
        if root not in roots:
            roots.append(root)
    return roots


def load_selected_model(state_path: Path | None = None) -> ManagedModelSelection | None:
    path = state_path or default_state_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("managed model selection state is invalid") from exc
    if not isinstance(raw, dict):
        raise ConfigError("managed model selection state is invalid")
    selected = raw.get("selected")
    if selected is None:
        return None
    return ManagedModelSelection.from_json(selected)


def save_selected_model(selection: ManagedModelSelection, state_path: Path | None = None) -> Path:
    validate_selection(selection)
    path = state_path or default_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"version": 1, "selected": selection.to_json()}, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def clear_selected_model(state_path: Path | None = None) -> None:
    path = state_path or default_state_path()
    if path.exists():
        path.unlink()


def validate_selected_model(state_path: Path | None = None) -> ManagedModelSelection:
    selected = load_selected_model(state_path)
    if selected is None:
        raise ConfigError("no managed model selected")
    validate_selection(selected)
    if not selected.model_path.is_file():
        raise ConfigError("selected managed model file is missing")
    return selected


def select_candidate_by_path(
    model_path: Path,
    *,
    cache_roots: list[Path] | None = None,
    state_path: Path | None = None,
) -> ManagedModelSelection:
    requested = model_path.expanduser().resolve(strict=False)
    for candidate in discover_managed_model_candidates(cache_roots):
        if candidate.selection.model_path.expanduser().resolve(strict=False) == requested:
            save_selected_model(candidate.selection, state_path)
            return candidate.selection
    raise ConfigError("model path is not a discovered Odysseus-managed candidate")


def validate_selection(selection: ManagedModelSelection) -> None:
    validate_managed_model_provenance(
        RuntimeConfig(
            bind_address="127.0.0.1",
            port=0,
            model_source=selection.model_source,
            model_repo_id=selection.model_repo_id,
            model_file=selection.model_file,
            model_path=selection.model_path,
            model_cache_root=selection.model_cache_root,
            model_snapshot_path=selection.model_snapshot_path,
            hub_mcp_url="http://127.0.0.1:1/hub/ai/mcp",
            social_mcp_url="http://127.0.0.1:1/social/ai/mcp",
            mcp_bearer="mcp",
            brain_store_url="http://127.0.0.1:1/odysseus/brain-store/v1",
            brain_store_bearer="brain",
            brain_store_key_id=BRAIN_STORE_KEY_ID,
        ),
    )


def _cookbook_model_dirs() -> list[str]:
    state_path = Path(os.environ.get("DATA_DIR", "data")) / "cookbook_state.json"
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    env = raw.get("env")
    if not isinstance(env, dict):
        return []
    model_dirs = env.get("modelPaths")
    out = [str(path) for path in model_dirs if path] if isinstance(model_dirs, list) else []
    servers = env.get("servers")
    if isinstance(servers, list):
        for server in servers:
            if not isinstance(server, dict):
                continue
            for path in server.get("modelDirs") or []:
                if path:
                    out.append(str(path))
    return out


def _normalize_cache_root(path: Path) -> Path:
    return path / "hub" if path.name == "huggingface" else path


def _repo_id_from_cache_dir(name: str) -> str | None:
    if not name.startswith("models--"):
        return None
    repo = name[len("models--") :].replace("--", "/")
    return repo if repo.count("/") == 1 else None


def _is_complete_main_gguf(path: Path) -> bool:
    name = path.name.lower()
    if name.startswith("mmproj-") or name.endswith(".incomplete") or path.name.endswith(".tmp"):
        return False
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _expect_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{field} must be non-empty text")
    return value


def _optional_path(value: Any, field: str) -> Path | None:
    if value is None:
        return None
    return Path(_expect_text(value, field))
