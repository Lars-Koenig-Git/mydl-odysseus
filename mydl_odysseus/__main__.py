"""CLI entrypoint for the MyDL Odysseus runtime scaffold."""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

from .config import ConfigError, RuntimeConfig, load_runtime_config, sanitized_config_error
from .mcp_probe import probe_configured_mcp_tools
from .model_loader import load_configured_model
from .runtime import create_mydl_runtime_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m mydl_odysseus",
        description="Run or validate the MyDL Odysseus runtime scaffold.",
    )
    parser.add_argument("--config", required=True, type=Path, help="Path to odysseus.toml")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate config and exit without starting the runtime server",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_runtime_config(args.config)
    except ConfigError as exc:
        print(sanitized_config_error(exc), file=sys.stderr)
        return 2

    if args.check_config:
        print("MyDL Odysseus runtime config: valid")
        return 0

    return _serve(config)


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
        return "model_file_missing"
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
