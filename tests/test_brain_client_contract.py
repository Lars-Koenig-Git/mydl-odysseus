from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.parse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

from mydl_odysseus import brain_client_contract as client


BRAIN = "brain-contract-token-secret"
MCP = "mcp-contract-token-secret"


def _envelope(*, deleted: bool = False, ciphertext: str = "AQID") -> dict[str, Any]:
    return {
        "version": 1,
        "record_id": "record-1",
        "key_id": client.BRAIN_STORE_KEY_ID,
        "nonce": "AAAAAAAAAAAAAAAA",
        "ciphertext": ciphertext,
        "auth_tag": "AQEBAQEBAQEBAQEBAQEBAQ",
        "associated_data": {
            "schema": client.BRAIN_RECORD_SCHEMA,
            "record_id": "record-1",
            "user_identity": "user:contract",
            "created_device_id": "device:contract",
        },
        "conflict": {
            "home_server_epoch": 0,
            "logical_clock": 1,
            "updated_at_ms": 2,
            "parent_record_version": None,
            "deleted": deleted,
        },
    }


def _contract_dict(url: str, *, brain: str = BRAIN, mcp: str = MCP) -> dict[str, Any]:
    envelope = _envelope()
    return {
        "contract_version": client.CONTRACT_VERSION,
        "brain_store_url": url,
        "brain_store_bearer": brain,
        "brain_store_key_id": client.BRAIN_STORE_KEY_ID,
        "mcp_bearer": mcp,
        "test_envelope": envelope,
        "expected": {
            "initial_get": {
                "method": "GET",
                "status": 200,
                "body": {"envelopes": []},
            },
            "post": {
                "method": "POST",
                "status": 200,
                "body": {"envelopes": [envelope]},
            },
            "final_get": {
                "method": "GET",
                "status": 200,
                "body": {"envelopes": [envelope]},
            },
        },
    }


def _write_contract(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class _BrainStoreServer(ThreadingHTTPServer):
    brain_bearer: str
    stored: list[dict[str, Any]]
    requests_seen: list[tuple[str, str | None, str]]


class _BrainStoreHandler(BaseHTTPRequestHandler):
    server: _BrainStoreServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _bearer(self) -> str | None:
        header = self.headers.get("authorization")
        if not header or not header.startswith("Bearer "):
            return None
        return header.removeprefix("Bearer ")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        bearer = self._bearer()
        self.server.requests_seen.append(("GET", bearer, ""))
        if path != "/odysseus/brain-store/v1" or bearer != self.server.brain_bearer:
            self._send_json(401, {"detail": {"code": "unauthorized"}})
            return
        self._send_json(200, {"envelopes": self.server.stored})

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        bearer = self._bearer()
        raw = self.rfile.read(int(self.headers.get("content-length", "0"))).decode(
            "utf-8",
        )
        self.server.requests_seen.append(("POST", bearer, raw))
        if path != "/odysseus/brain-store/v1" or bearer != self.server.brain_bearer:
            self._send_json(401, {"detail": {"code": "unauthorized"}})
            return
        payload = json.loads(raw)
        self.server.stored = payload["envelopes"]
        self._send_json(200, {"envelopes": self.server.stored})


@contextmanager
def _fake_store(*, brain_bearer: str = BRAIN) -> Iterator[_BrainStoreServer]:
    server = _BrainStoreServer(("127.0.0.1", 0), _BrainStoreHandler)
    server.brain_bearer = brain_bearer
    server.stored = []
    server.requests_seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _server_url(server: _BrainStoreServer) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}/odysseus/brain-store/v1"


def test_contract_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_contract(
        tmp_path,
        _contract_dict("http://127.0.0.1:9/odysseus/brain-store/v1"),
    )
    monkeypatch.setenv(client.CONTRACT_ENV, str(path))

    contract = client.load_contract()

    assert contract.contract_version == client.CONTRACT_VERSION
    assert contract.brain_store_url.endswith("/odysseus/brain-store/v1")
    assert contract.brain_store_bearer == BRAIN
    assert contract.mcp_bearer == MCP


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:1/odysseus/brain-store/v1",
        "http://localhost:1/odysseus/brain-store/v1",
        "http://[::1]:1/odysseus/brain-store/v1",
    ],
)
def test_loopback_url_validation_accepts_loopback_http(url: str) -> None:
    assert client.validate_loopback_http_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:1/odysseus/brain-store/v1",
        "http://192.168.1.2:1/odysseus/brain-store/v1",
        "http://example.com/odysseus/brain-store/v1",
        "not-a-url",
    ],
)
def test_loopback_url_validation_rejects_non_loopback_http(url: str) -> None:
    with pytest.raises(client.ContractError):
        client.validate_loopback_http_url(url)


def test_envelope_validation_success() -> None:
    assert client.validate_envelope(_envelope())["record_id"] == "record-1"
    assert client.validate_envelope(_envelope(deleted=True, ciphertext=""))["ciphertext"] == ""


@pytest.mark.parametrize(
    "mutate",
    [
        lambda env: {**env, "extra": True},
        lambda env: {**env, "version": 2},
        lambda env: {**env, "key_id": "wrong"},
        lambda env: {**env, "nonce": "AAAAAAAAAAAAAAAA=="},
        lambda env: {**env, "nonce": "AAAA"},
        lambda env: {**env, "auth_tag": "AAAA"},
        lambda env: {**env, "ciphertext": ""},
        lambda env: {
            **env,
            "associated_data": {**env["associated_data"], "memory": "raw"},
        },
        lambda env: {
            **env,
            "conflict": {**env["conflict"], "deleted": "false"},
        },
    ],
)
def test_envelope_validation_failure(mutate: Any) -> None:
    with pytest.raises(client.ContractError):
        client.validate_envelope(mutate(_envelope()))


def test_get_post_get_success_against_fake_server(tmp_path: Path) -> None:
    with _fake_store() as server:
        contract = client.load_contract(_write_contract(tmp_path, _contract_dict(_server_url(server))))
        client.run_contract(contract)

    assert [item[0] for item in server.requests_seen] == ["GET", "POST", "GET"]
    assert all(item[1] == BRAIN for item in server.requests_seen)
    assert server.stored == [contract.test_envelope]


def test_mcp_bearer_misuse_prevention(tmp_path: Path) -> None:
    path = _write_contract(
        tmp_path,
        _contract_dict("http://127.0.0.1:9/odysseus/brain-store/v1", brain=MCP, mcp=MCP),
    )

    with pytest.raises(client.ContractError):
        client.load_contract(path)


def test_command_output_redacts_contract_secrets(tmp_path: Path) -> None:
    with _fake_store() as server:
        path = _write_contract(tmp_path, _contract_dict(_server_url(server)))
        env = os.environ.copy()
        env[client.CONTRACT_ENV] = str(path)
        proc = subprocess.run(
            [sys.executable, "-m", "mydl_odysseus.brain_client_contract"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert proc.stdout.strip() == "Odysseus brain client contract OK"
    assert BRAIN not in combined
    assert MCP not in combined
    for forbidden_label in (
        "brain_store_bearer",
        "mcp_bearer",
        "brain_key",
        "brainKey",
        "brainStoreKey",
        "brain_store_key",
        "brain_store_key_hex",
        "mnemonic",
        "seed",
    ):
        assert forbidden_label not in combined
