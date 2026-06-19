from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mydl_odysseus.config import BRAIN_STORE_KEY_ID, RuntimeConfig
from mydl_odysseus.runtime import create_mydl_runtime_app

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


def _config(tmp_path: Path) -> RuntimeConfig:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"model")
    return RuntimeConfig(
        bind_address="127.0.0.1",
        port=0,
        model_path=model,
        hub_mcp_url="http://127.0.0.1:8010/hub/ai/mcp",
        social_mcp_url="http://127.0.0.1:8010/social/ai/mcp",
        mcp_bearer=MCP_SECRET,
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
