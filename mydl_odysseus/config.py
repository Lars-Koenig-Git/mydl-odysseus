"""MyDL-generated Odysseus runtime configuration."""

from __future__ import annotations

import ipaddress
import tomllib
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BRAIN_STORE_KEY_ID = "mydl.odysseus.brain-store.v1"

REQUIRED_CONFIG_KEYS = frozenset(
    {
        "bind_address",
        "port",
        "model_path",
        "hub_mcp_url",
        "social_mcp_url",
        "mcp_bearer",
        "brain_store_url",
        "brain_store_bearer",
        "brain_store_key_id",
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
    model_path: Path
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
            model_path=_validate_model_path(data["model_path"]),
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
    unknown = set(data) - REQUIRED_CONFIG_KEYS
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
