from __future__ import annotations

import selectors
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import mydl_odysseus.__main__ as cli
from mydl_odysseus.config import BRAIN_STORE_KEY_ID, RuntimeConfig
from mydl_odysseus.mcp_probe import (
    HUB_REQUIRED_TOOLS,
    SOCIAL_REQUIRED_TOOLS,
    probe_configured_mcp_tools,
)
from mydl_odysseus.runtime import create_mydl_runtime_app
from test_mydl_mcp_probe import MCP_SECRET as PROBE_MCP_SECRET
from test_mydl_mcp_probe import Route, fake_mcp_server

MCP_SECRET = "mcp-runtime-secret"
BRAIN_SECRET = "brain-runtime-secret"
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


def _config(
    tmp_path: Path,
    *,
    hub_mcp_url: str = "http://127.0.0.1:8010/hub/ai/mcp",
    social_mcp_url: str = "http://127.0.0.1:8010/social/ai/mcp",
    mcp_bearer: str = MCP_SECRET,
) -> RuntimeConfig:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"model")
    return RuntimeConfig(
        bind_address="127.0.0.1",
        port=0,
        model_path=model,
        hub_mcp_url=hub_mcp_url,
        social_mcp_url=social_mcp_url,
        mcp_bearer=mcp_bearer,
        brain_store_url="http://127.0.0.1:8010/odysseus/brain-store/v1",
        brain_store_bearer=BRAIN_SECRET,
        brain_store_key_id=BRAIN_STORE_KEY_ID,
    )


def test_health_reports_not_ready_until_real_model_loaded(tmp_path: Path) -> None:
    client = TestClient(create_mydl_runtime_app(_config(tmp_path)))

    response = client.get("/health")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] != "ok"
    assert payload["model_configured"] is True
    assert payload["model_loaded"] is False
    assert payload["hub_mcp_tools_ready"] is False
    assert payload["social_mcp_tools_ready"] is False
    assert payload["brain_store_configured"] is True


def test_health_reports_mcp_flags_true_when_fake_endpoints_pass(tmp_path: Path) -> None:
    routes = {
        "/hub/ai/mcp": Route(HUB_REQUIRED_TOOLS),
        "/social/ai/mcp": Route(SOCIAL_REQUIRED_TOOLS),
    }
    with fake_mcp_server(routes, bearer=PROBE_MCP_SECRET) as (base_url, _state):
        config = _config(
            tmp_path,
            hub_mcp_url=f"{base_url}/hub/ai/mcp",
            social_mcp_url=f"{base_url}/social/ai/mcp",
            mcp_bearer=PROBE_MCP_SECRET,
        )
        readiness = probe_configured_mcp_tools(config)
        client = TestClient(create_mydl_runtime_app(config, mcp_readiness=readiness))
        payload = client.get("/health").json()

    assert payload["status"] == "not_ready"
    assert payload["model_loaded"] is False
    assert payload["hub_mcp_tools_ready"] is True
    assert payload["social_mcp_tools_ready"] is True


def test_health_reports_mcp_flags_true_when_fake_endpoints_pass_after_retry(tmp_path: Path) -> None:
    routes = {
        "/hub/ai/mcp": Route(HUB_REQUIRED_TOOLS, tools_statuses=(503, 200)),
        "/social/ai/mcp": Route(SOCIAL_REQUIRED_TOOLS, initialize_statuses=(503, 200)),
    }
    with fake_mcp_server(routes, bearer=PROBE_MCP_SECRET) as (base_url, _state):
        config = _config(
            tmp_path,
            hub_mcp_url=f"{base_url}/hub/ai/mcp",
            social_mcp_url=f"{base_url}/social/ai/mcp",
            mcp_bearer=PROBE_MCP_SECRET,
        )
        readiness = probe_configured_mcp_tools(config)
        client = TestClient(create_mydl_runtime_app(config, mcp_readiness=readiness))
        payload = client.get("/health").json()

    assert payload["status"] == "not_ready"
    assert payload["model_loaded"] is False
    assert payload["hub_mcp_tools_ready"] is True
    assert payload["social_mcp_tools_ready"] is True


def test_health_reports_only_failed_mcp_flag_false(tmp_path: Path) -> None:
    routes = {
        "/hub/ai/mcp": Route(HUB_REQUIRED_TOOLS),
        "/social/ai/mcp": Route(SOCIAL_REQUIRED_TOOLS, tools_status=503),
    }
    with fake_mcp_server(routes, bearer=PROBE_MCP_SECRET) as (base_url, _state):
        config = _config(
            tmp_path,
            hub_mcp_url=f"{base_url}/hub/ai/mcp",
            social_mcp_url=f"{base_url}/social/ai/mcp",
            mcp_bearer=PROBE_MCP_SECRET,
        )
        readiness = probe_configured_mcp_tools(config)
        client = TestClient(create_mydl_runtime_app(config, mcp_readiness=readiness))
        payload = client.get("/health").json()

    assert payload["status"] == "not_ready"
    assert payload["model_loaded"] is False
    assert payload["hub_mcp_tools_ready"] is True
    assert payload["social_mcp_tools_ready"] is False


def test_iframe_routes_are_embeddable_non_empty_and_secret_free(tmp_path: Path) -> None:
    client = TestClient(create_mydl_runtime_app(_config(tmp_path)))

    for path in ("/", "/?mode=dm"):
        response = client.get(path)
        body = response.text
        csp = response.headers.get("content-security-policy", "")
        xfo = response.headers.get("x-frame-options", "").lower()

        assert response.status_code == 200
        assert body.strip()
        assert "frame-ancestors" in csp
        assert "*" not in csp.split("frame-ancestors", 1)[1].split(";", 1)[0]
        assert xfo not in {"deny", "sameorigin"}
        for forbidden in FORBIDDEN_OUTPUT:
            assert forbidden not in body


def test_cli_check_config_does_not_attempt_network_calls(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    path = _write_config(tmp_path)

    def fail_probe(_config: RuntimeConfig) -> object:
        raise AssertionError("check-config must not probe MCP endpoints")

    monkeypatch.setattr(cli, "probe_configured_mcp_tools", fail_probe)

    assert cli.main(["--config", str(path), "--check-config"]) == 0
    captured = capsys.readouterr()
    assert "valid" in captured.out


def test_server_startup_with_fake_mcp_endpoints_still_prints_not_ready(
    tmp_path: Path,
) -> None:
    routes = {
        "/hub/ai/mcp": Route(HUB_REQUIRED_TOOLS),
        "/social/ai/mcp": Route(SOCIAL_REQUIRED_TOOLS),
    }
    with fake_mcp_server(routes, bearer=PROBE_MCP_SECRET) as (base_url, _state):
        config_path = _write_config(
            tmp_path,
            hub_mcp_url=f"{base_url}/hub/ai/mcp",
            social_mcp_url=f"{base_url}/social/ai/mcp",
            mcp_bearer=PROBE_MCP_SECRET,
        )
        proc = subprocess.Popen(
            [sys.executable, "-m", "mydl_odysseus", "--config", str(config_path)],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            line = _read_stdout_line(proc)
            assert line.startswith("ODYSSEUS_NOT_READY bind=127.0.0.1:")
            assert "ODYSSEUS_READY" not in line
            assert PROBE_MCP_SECRET not in line
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            stderr = proc.stderr.read() if proc.stderr is not None else ""
            assert "ODYSSEUS_READY" not in stderr
            assert PROBE_MCP_SECRET not in stderr


def _write_config(tmp_path: Path, **overrides: object) -> Path:
    config = _config(tmp_path, **overrides)
    path = tmp_path / "odysseus.toml"
    path.write_text(
        "\n".join(
            [
                f'bind_address = "{config.bind_address}"',
                f"port = {config.port}",
                f'model_path = "{config.model_path}"',
                f'hub_mcp_url = "{config.hub_mcp_url}"',
                f'social_mcp_url = "{config.social_mcp_url}"',
                f'mcp_bearer = "{config.mcp_bearer}"',
                f'brain_store_url = "{config.brain_store_url}"',
                f'brain_store_bearer = "{config.brain_store_bearer}"',
                f'brain_store_key_id = "{config.brain_store_key_id}"',
            ],
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _read_stdout_line(proc: subprocess.Popen[str]) -> str:
    assert proc.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        for _attempt in range(100):
            if proc.poll() is not None:
                raise AssertionError(f"runtime exited early with {proc.returncode}")
            for _key, _events in selector.select(timeout=0.1):
                line = proc.stdout.readline()
                if line:
                    return line.strip()
    finally:
        selector.close()
    raise AssertionError("timed out waiting for runtime banner")
