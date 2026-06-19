"""CLI entrypoint for the MyDL Odysseus runtime scaffold."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

from .config import ConfigError, RuntimeConfig, load_runtime_config, sanitized_config_error
from .managed_model_selection import (
    clear_selected_model,
    discover_managed_model_candidates,
    load_selected_model,
    select_candidate_by_path,
    validate_selected_model,
)
from .mcp_probe import probe_configured_mcp_tools
from .model_loader import load_configured_model
from .runtime import create_mydl_runtime_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m mydl_odysseus",
        description="Run or validate the MyDL Odysseus runtime scaffold.",
    )
    parser.add_argument("--config", type=Path, help="Path to odysseus.toml")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate config and exit without starting the runtime server",
    )
    parser.add_argument(
        "--list-managed-models",
        action="store_true",
        help="List valid Odysseus-managed GGUF model candidates as JSON",
    )
    parser.add_argument(
        "--selected-managed-model",
        action="store_true",
        help="Print the selected Odysseus-managed model as JSON, or null",
    )
    parser.add_argument(
        "--validate-selected-managed-model",
        action="store_true",
        help="Validate the selected Odysseus-managed model and print it as JSON",
    )
    parser.add_argument(
        "--select-managed-model",
        type=Path,
        help="Store an explicit Odysseus-managed model selection by discovered GGUF path",
    )
    parser.add_argument(
        "--clear-managed-model-selection",
        action="store_true",
        help="Clear the Odysseus-managed model selection",
    )
    parser.add_argument(
        "--managed-model-state",
        type=Path,
        help="Override the Odysseus managed model selection state path",
    )
    parser.add_argument(
        "--managed-model-cache-root",
        action="append",
        type=Path,
        default=[],
        help="Additional Hugging Face hub cache root to scan for managed GGUF models",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selection_command_count = sum(
        bool(value)
        for value in (
            args.list_managed_models,
            args.selected_managed_model,
            args.validate_selected_managed_model,
            args.select_managed_model,
            args.clear_managed_model_selection,
        )
    )
    if selection_command_count > 1:
        print("choose only one managed model selection command", file=sys.stderr)
        return 2
    if selection_command_count:
        return _managed_model_command(args)
    if args.config is None:
        print("--config is required unless using a managed model selection command", file=sys.stderr)
        return 2
    try:
        config = load_runtime_config(args.config)
    except ConfigError as exc:
        print(sanitized_config_error(exc), file=sys.stderr)
        return 2

    if args.check_config:
        print("MyDL Odysseus runtime config: valid")
        return 0

    return _serve(config)


def _managed_model_command(args: argparse.Namespace) -> int:
    state_path = args.managed_model_state
    cache_roots = args.managed_model_cache_root or None
    try:
        if args.list_managed_models:
            _emit_json(
                {
                    "models": [
                        candidate.to_json()
                        for candidate in discover_managed_model_candidates(cache_roots)
                    ],
                },
            )
            return 0
        if args.selected_managed_model:
            selected = load_selected_model(state_path)
            _emit_json(selected.to_json() if selected else None)
            return 0
        if args.validate_selected_managed_model:
            _emit_json(validate_selected_model(state_path).to_json())
            return 0
        if args.select_managed_model:
            selected = select_candidate_by_path(
                args.select_managed_model,
                cache_roots=cache_roots,
                state_path=state_path,
            )
            _emit_json(selected.to_json())
            return 0
        if args.clear_managed_model_selection:
            clear_selected_model(state_path)
            _emit_json({"ok": True})
            return 0
    except ConfigError:
        print("invalid Odysseus managed model selection", file=sys.stderr)
        return 2
    return 2


def _emit_json(value: object) -> None:
    print(json.dumps(value, sort_keys=True))


def _serve(config: RuntimeConfig) -> int:
    import uvicorn

    mcp_readiness = probe_configured_mcp_tools(config)
    model_load_result = load_configured_model(config)
    app = create_mydl_runtime_app(
        config,
        mcp_readiness=mcp_readiness,
        model_load_result=model_load_result,
    )
    with _bound_socket(config.bind_address, config.port) as sock:
        host, port = sock.getsockname()[:2]
        health = app.state.mydl_runtime.health_payload()
        if health["status"] == "ok":
            print(f"ODYSSEUS_READY bind={host}:{port}", flush=True)
        else:
            print(
                f"ODYSSEUS_NOT_READY bind={host}:{port} "
                f"reason={_not_ready_reason(model_load_result.reason, health)}",
                flush=True,
            )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=str(host),
                port=int(port),
                log_level="warning",
                access_log=False,
            ),
        )
        server.run(sockets=[sock])
    return 0


def _not_ready_reason(model_reason: str, health: dict[str, bool | str]) -> str:
    if health.get("model_configured") is not True:
        return "managed_model_missing"
    if health.get("model_loaded") is not True:
        return model_reason
    if health.get("hub_mcp_tools_ready") is not True:
        return "hub_mcp_tools_not_ready"
    if health.get("social_mcp_tools_ready") is not True:
        return "social_mcp_tools_not_ready"
    if health.get("brain_store_configured") is not True:
        return "brain_store_not_configured"
    return "runtime_not_ready"


def _bound_socket(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(socket.SOMAXCONN)
    return sock


if __name__ == "__main__":
    raise SystemExit(main())
