"""MyDL-generated Odysseus runtime configuration."""

from __future__ import annotations

import ipaddress
import tomllib
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BRAIN_STORE_KEY_ID = "mydl.odysseus.brain-store.v1"
MODEL_SOURCE_ODYSSEUS_COOKBOOK = "odysseus-cookbook"

REQUIRED_CONFIG_KEYS = frozenset(
    {
        "bind_address",
        "port",
        "model_source",
        "model_repo_id",
        "model_file",
        "model_path",
        "hub_mcp_url",
        "social_mcp_url",
        "mcp_bearer",
        "brain_store_url",
        "brain_store_bearer",
        "brain_store_key_id",
    },
)
OPTIONAL_CONFIG_KEYS = frozenset(
    {
        "model_cache_root",
        "model_snapshot_path",
    },
)
FORBIDDEN_RAW_SECRET_KEYS = frozenset(
    {
        "brain_key",
        "brainKey",
        "brainStoreKey",
        "brain_store_key",
        "brain_store_key_hex",
        "mnemonic",
        "seed",
    },
)


class ConfigError(Exception):
    """Raised when a MyDL Odysseus runtime config is invalid."""


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    bind_address: str
    port: int
    model_source: str
    model_repo_id: str
    model_file: str
    model_path: Path
    model_cache_root: Path | None
    model_snapshot_path: Path | None
    hub_mcp_url: str
    social_mcp_url: str
    mcp_bearer: str
    brain_store_url: str
    brain_store_bearer: str
    brain_store_key_id: str

    def redacted_summary(self) -> dict[str, str | int | bool]:
        return {
            "bind_address": self.bind_address,
            "port": self.port,
            "model_source": self.model_source,
            "model_repo_id": self.model_repo_id,
            "model_file": self.model_file,
            "model_path": str(self.model_path),
            "hub_mcp_origin": _origin(self.hub_mcp_url),
            "social_mcp_origin": _origin(self.social_mcp_url),
            "brain_store_origin": _origin(self.brain_store_url),
            "brain_store_contract": "v1",
            "credentials": "configured",
        }


def load_runtime_config(path: str | Path) -> RuntimeConfig:
    config_path = Path(path)
    try:
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError("config file could not be read") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("config TOML is invalid") from exc
    data = _expect_flat_table(parsed)
    _reject_key_material(data)
    _reject_unknown_keys(data)

    try:
        config = RuntimeConfig(
            bind_address=_validate_loopback_address(data["bind_address"]),
            port=_validate_port(data["port"]),
            model_source=_validate_model_source(data["model_source"]),
            model_repo_id=_validate_model_repo_id(data["model_repo_id"]),
            model_file=_validate_model_file(data["model_file"]),
            model_path=_validate_model_path(data["model_path"]),
            model_cache_root=_validate_optional_path(data.get("model_cache_root"), "model_cache_root"),
            model_snapshot_path=_validate_optional_path(
                data.get("model_snapshot_path"),
                "model_snapshot_path",
            ),
            hub_mcp_url=validate_loopback_http_url(data["hub_mcp_url"], "hub_mcp_url"),
            social_mcp_url=validate_loopback_http_url(
                data["social_mcp_url"],
                "social_mcp_url",
            ),
            mcp_bearer=_expect_non_empty_text(data["mcp_bearer"], "mcp_bearer"),
            brain_store_url=validate_loopback_http_url(
                data["brain_store_url"],
                "brain_store_url",
            ),
            brain_store_bearer=_expect_non_empty_text(
                data["brain_store_bearer"],
                "brain_store_bearer",
            ),
            brain_store_key_id=_expect_non_empty_text(
                data["brain_store_key_id"],
                "brain_store_key_id",
            ),
        )
    except KeyError as exc:
        raise ConfigError("config is missing a required field") from exc

    if config.mcp_bearer == config.brain_store_bearer:
        raise ConfigError("MCP and brain-store credentials must be distinct")
    if config.brain_store_key_id != BRAIN_STORE_KEY_ID:
        raise ConfigError("brain-store key id is unsupported")
    validate_managed_model_provenance(config)
    return config


def validate_loopback_http_url(value: Any, field: str = "url") -> str:
    text = _expect_non_empty_text(value, field)
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ConfigError(f"{field} must be a loopback HTTP URL")
    if not _is_loopback_host(parsed.hostname):
        raise ConfigError(f"{field} must be a loopback HTTP URL")
    return text


def sanitized_config_error(exc: BaseException) -> str:
    """Return a CLI-safe error that avoids secret labels and values."""
    if isinstance(exc, ConfigError):
        return "invalid MyDL Odysseus runtime config"
    return "MyDL Odysseus runtime config failed"


def _expect_flat_table(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or any(isinstance(child, dict) for child in value.values()):
        raise ConfigError("config must be a flat TOML table")
    return value


def _reject_key_material(data: dict[str, Any]) -> None:
    forbidden = sorted(key for key in data if key in FORBIDDEN_RAW_SECRET_KEYS)
    if forbidden:
        raise ConfigError("config contains forbidden raw brain-key material")


def _reject_unknown_keys(data: dict[str, Any]) -> None:
    missing = REQUIRED_CONFIG_KEYS - set(data)
    if missing:
        raise ConfigError("config is missing a required field")
    unknown = set(data) - REQUIRED_CONFIG_KEYS - OPTIONAL_CONFIG_KEYS
    if unknown:
        raise ConfigError("config contains unknown fields")


def _expect_non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{field} must be non-empty text")
    return value


def _validate_loopback_address(value: Any) -> str:
    text = _expect_non_empty_text(value, "bind_address")
    if not _is_loopback_host(text):
        raise ConfigError("bind_address must be loopback")
    return text


def _validate_port(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 65535:
        raise ConfigError("port must be between 0 and 65535")
    return value


def _validate_model_path(value: Any) -> Path:
    text = _expect_non_empty_text(value, "model_path")
    path = Path(text)
    return path


def _validate_model_source(value: Any) -> str:
    text = _expect_non_empty_text(value, "model_source")
    if text != MODEL_SOURCE_ODYSSEUS_COOKBOOK:
        raise ConfigError("model_source is unsupported")
    return text


def _validate_model_repo_id(value: Any) -> str:
    text = _expect_non_empty_text(value, "model_repo_id")
    parts = text.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        raise ConfigError("model_repo_id must be a Hugging Face repo id")
    if any("\\" in part for part in parts):
        raise ConfigError("model_repo_id must be a Hugging Face repo id")
    return text


def _validate_model_file(value: Any) -> str:
    text = _expect_non_empty_text(value, "model_file")
    path = Path(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ConfigError("model_file must be a relative GGUF filename")
    if path.suffix.lower() != ".gguf":
        raise ConfigError("model_file must be a GGUF file")
    if path.name.lower().startswith("mmproj-"):
        raise ConfigError("projector GGUF files are not valid main models")
    return text


def _validate_optional_path(value: Any, field: str) -> Path | None:
    if value is None:
        return None
    return Path(_expect_non_empty_text(value, field))


def validate_managed_model_provenance(config: RuntimeConfig) -> None:
    """Reject arbitrary GGUF paths that are not Odysseus Cookbook/HF cache output."""

    if config.model_source != MODEL_SOURCE_ODYSSEUS_COOKBOOK:
        raise ConfigError("model_source is unsupported")
    expected_model_file = Path(config.model_file)
    if config.model_path.name.lower().startswith("mmproj-"):
        raise ConfigError("projector GGUF files are not valid main models")
    if config.model_path.suffix.lower() != ".gguf":
        raise ConfigError("model_path must point to a GGUF file")
    if not _path_ends_with(config.model_path, expected_model_file.parts):
        raise ConfigError("model_path does not match model_file")
    if config.model_snapshot_path is not None:
        _validate_snapshot_path(
            config.model_snapshot_path,
            model_path=config.model_path,
            repo_id=config.model_repo_id,
            model_file=expected_model_file,
        )
        return
    _validate_hf_cache_path(
        config.model_path,
        repo_id=config.model_repo_id,
        model_file=expected_model_file,
        cache_root=config.model_cache_root,
    )


def is_existing_managed_model(config: RuntimeConfig) -> bool:
    try:
        validate_managed_model_provenance(config)
    except ConfigError:
        return False
    return config.model_path.is_file()


def _validate_snapshot_path(
    snapshot_path: Path,
    *,
    model_path: Path,
    repo_id: str,
    model_file: Path,
) -> None:
    if not _path_ends_with(model_path, model_file.parts):
        raise ConfigError("model_path does not match model_file")
    if not _path_is_relative_to(model_path, snapshot_path):
        raise ConfigError("model_path is outside model_snapshot_path")
    if not _path_ends_with(model_path, (*snapshot_path.parts, *model_file.parts)):
        raise ConfigError("model_path does not match model_snapshot_path")
    parts = snapshot_path.parts
    repo_cache_dir = _repo_cache_dir(repo_id)
    if len(parts) < 3 or parts[-2] != "snapshots" or parts[-3] != repo_cache_dir:
        raise ConfigError("model_snapshot_path is not a Hugging Face cache snapshot")


def _validate_hf_cache_path(
    model_path: Path,
    *,
    repo_id: str,
    model_file: Path,
    cache_root: Path | None,
) -> None:
    if cache_root is not None and not _path_is_relative_to(model_path, cache_root):
        raise ConfigError("model_path is outside model_cache_root")
    parts = model_path.parts
    repo_cache_dir = _repo_cache_dir(repo_id)
    for index, part in enumerate(parts):
        if part != repo_cache_dir:
            continue
        snapshot_index = index + 1
        revision_index = index + 2
        file_index = index + 3
        if (
            len(parts) > file_index
            and parts[snapshot_index] == "snapshots"
            and parts[revision_index]
            and tuple(parts[file_index:]) == model_file.parts
        ):
            return
    raise ConfigError("model_path is not an Odysseus Cookbook/HF cache snapshot file")


def _repo_cache_dir(repo_id: str) -> str:
    return "models--" + repo_id.replace("/", "--")


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _path_ends_with(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = path.parts
    return len(parts) >= len(suffix_parts) and tuple(parts[-len(suffix_parts) :]) == suffix_parts


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _origin(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
