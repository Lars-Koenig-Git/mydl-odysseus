from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from mydl_odysseus.config import FORBIDDEN_RAW_SECRET_KEYS
from mydl_odysseus.mcp_probe import (
    HUB_REQUIRED_TOOLS,
    SOCIAL_REQUIRED_TOOLS,
    McpProbeResult,
    probe_mcp_tools,
)

MCP_SECRET = "probe-mcp-secret-value"
FORBIDDEN_OUTPUT = (
    MCP_SECRET,
    "mcp_bearer",
    "brain_store_bearer",
    *tuple(FORBIDDEN_RAW_SECRET_KEYS),
)


@dataclass
class Route:
    tools: frozenset[str] = field(default_factory=frozenset)
    tools_status: int = 200
    tools_payload: dict[str, Any] | None = None
    initialize_statuses: tuple[int, ...] = ()
    tools_statuses: tuple[int, ...] = ()


@dataclass
class FakeMcpState:
    bearer: str
    routes: dict[str, Route]
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)


@contextlib.contextmanager
def fake_mcp_server(
    routes: dict[str, Route],
    *,
    bearer: str = MCP_SECRET,
) -> Iterator[tuple[str, FakeMcpState]]:
    state = FakeMcpState(bearer=bearer, routes=routes)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            method = None
            try:
                payload = json.loads(raw_body.decode("utf-8"))
                if isinstance(payload, dict):
                    method = payload.get("method")
            except json.JSONDecodeError:
                pass
            auth = self.headers.get("Authorization")
            state.calls.append((self.path, str(method), auth))

            if auth != f"Bearer {state.bearer}":
                self._send_json(401, {"error": {"code": -32001, "message": "unauthorized"}})
                return
            route = state.routes.get(self.path)
            if route is None:
                self._send_json(404, {"error": {"code": -32601, "message": "not found"}})
                return
            if method == "initialize":
                status = _sequenced_status(
                    route.initialize_statuses,
                    _call_count(state, self.path, method),
                    200,
                )
                self._send_json(status, {"jsonrpc": "2.0", "id": "init", "result": {}})
                return
            if method == "tools/list":
                status = _sequenced_status(
                    route.tools_statuses,
                    _call_count(state, self.path, method),
                    route.tools_status,
                )
                if route.tools_payload is not None:
                    self._send_json(status, route.tools_payload)
                    return
                self._send_json(
                    status,
                    {
                        "jsonrpc": "2.0",
                        "id": "tools",
                        "result": {
                            "tools": [{"name": name} for name in sorted(route.tools)],
                        },
                    },
                )
                return
            self._send_json(400, {"error": {"code": -32601, "message": "unsupported"}})

        def log_message(self, _format: str, *args: object) -> None:
            return

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _call_count(state: FakeMcpState, path: str, method: str | None) -> int:
    return sum(
        1
        for call_path, call_method, _auth in state.calls
        if call_path == path and call_method == method
    )


def _sequenced_status(statuses: tuple[int, ...], call_count: int, fallback: int) -> int:
    if not statuses:
        return fallback
    return statuses[min(call_count - 1, len(statuses) - 1)]


def test_successful_hub_probe_with_exact_required_tools() -> None:
    with fake_mcp_server({"/hub/ai/mcp": Route(HUB_REQUIRED_TOOLS)}) as (base_url, state):
        result = probe_mcp_tools(
            f"{base_url}/hub/ai/mcp",
            MCP_SECRET,
            HUB_REQUIRED_TOOLS,
            endpoint="hub",
        )

    assert result == McpProbeResult.success("hub", len(HUB_REQUIRED_TOOLS))
    assert [(path, method) for path, method, _auth in state.calls] == [
        ("/hub/ai/mcp", "initialize"),
        ("/hub/ai/mcp", "tools/list"),
    ]
    assert {auth for _path, _method, auth in state.calls} == {f"Bearer {MCP_SECRET}"}


def test_successful_social_probe_with_exact_required_tools() -> None:
    with fake_mcp_server({"/social/ai/mcp": Route(SOCIAL_REQUIRED_TOOLS)}) as (base_url, _state):
        result = probe_mcp_tools(
            f"{base_url}/social/ai/mcp",
            MCP_SECRET,
            SOCIAL_REQUIRED_TOOLS,
            endpoint="social",
        )

    assert result.ready is True
    assert result.reason == "ok"
    assert result.observed_tool_count == len(SOCIAL_REQUIRED_TOOLS)


def test_missing_authorization_is_rejected_by_fake_server() -> None:
    with fake_mcp_server({"/hub/ai/mcp": Route(HUB_REQUIRED_TOOLS)}) as (base_url, _state):
        request = urllib.request.Request(
            f"{base_url}/hub/ai/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("fake MCP server accepted a missing bearer")


def test_wrong_bearer_is_rejected() -> None:
    with fake_mcp_server({"/hub/ai/mcp": Route(HUB_REQUIRED_TOOLS)}) as (base_url, _state):
        result = probe_mcp_tools(
            f"{base_url}/hub/ai/mcp",
            "wrong-bearer",
            HUB_REQUIRED_TOOLS,
            endpoint="hub",
        )

    assert result.ready is False
    assert result.reason == "http_status"


def test_missing_required_tool_fails() -> None:
    tools = frozenset({"mydl.bubble.list"})
    with fake_mcp_server({"/hub/ai/mcp": Route(tools)}) as (base_url, _state):
        result = probe_mcp_tools(
            f"{base_url}/hub/ai/mcp",
            MCP_SECRET,
            HUB_REQUIRED_TOOLS,
            endpoint="hub",
        )

    assert result.ready is False
    assert result.reason == "missing_required_tools"
    assert result.missing_required_tools == ("mydl.bubble.evaluate_local",)


def test_invalid_json_rpc_shape_fails() -> None:
    bad_payload = {"jsonrpc": "2.0", "id": "tools", "result": {"tools": "not-a-list"}}
    with fake_mcp_server({"/hub/ai/mcp": Route(tools_payload=bad_payload)}) as (base_url, _state):
        result = probe_mcp_tools(
            f"{base_url}/hub/ai/mcp",
            MCP_SECRET,
            HUB_REQUIRED_TOOLS,
            endpoint="hub",
        )

    assert result.ready is False
    assert result.reason == "invalid_tools"


def test_non_200_response_fails() -> None:
    with fake_mcp_server({"/hub/ai/mcp": Route(HUB_REQUIRED_TOOLS, tools_status=503)}) as (
        base_url,
        _state,
    ):
        result = probe_mcp_tools(
            f"{base_url}/hub/ai/mcp",
            MCP_SECRET,
            HUB_REQUIRED_TOOLS,
            endpoint="hub",
        )

    assert result.ready is False
    assert result.reason == "http_status"


def test_transient_initialize_failure_then_success() -> None:
    route = Route(HUB_REQUIRED_TOOLS, initialize_statuses=(503, 200))
    with fake_mcp_server({"/hub/ai/mcp": route}) as (base_url, state):
        result = probe_mcp_tools(
            f"{base_url}/hub/ai/mcp",
            MCP_SECRET,
            HUB_REQUIRED_TOOLS,
            endpoint="hub",
            retry_backoff_seconds=0,
        )

    assert result.ready is True
    assert [(path, method) for path, method, _auth in state.calls] == [
        ("/hub/ai/mcp", "initialize"),
        ("/hub/ai/mcp", "initialize"),
        ("/hub/ai/mcp", "tools/list"),
    ]


def test_transient_tools_5xx_then_success() -> None:
    route = Route(HUB_REQUIRED_TOOLS, tools_statuses=(503, 200))
    with fake_mcp_server({"/hub/ai/mcp": route}) as (base_url, state):
        result = probe_mcp_tools(
            f"{base_url}/hub/ai/mcp",
            MCP_SECRET,
            HUB_REQUIRED_TOOLS,
            endpoint="hub",
            retry_backoff_seconds=0,
        )

    assert result.ready is True
    assert [(path, method) for path, method, _auth in state.calls] == [
        ("/hub/ai/mcp", "initialize"),
        ("/hub/ai/mcp", "tools/list"),
        ("/hub/ai/mcp", "tools/list"),
    ]


def test_timeout_transport_failure_then_success(monkeypatch) -> None:
    calls: list[str] = []

    class Response:
        def __init__(self, status: int, payload: dict[str, Any]) -> None:
            self.status = status
            self._payload = payload

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self._payload, separators=(",", ":")).encode("utf-8")

    def fake_urlopen(request: urllib.request.Request, *, timeout: float) -> Response:
        assert timeout == 5.0
        payload = json.loads(request.data.decode("utf-8"))
        method = payload["method"]
        calls.append(method)
        if len(calls) == 1:
            raise TimeoutError("deterministic timeout")
        if method == "initialize":
            return Response(200, {"jsonrpc": "2.0", "id": "init", "result": {}})
        return Response(
            200,
            {
                "jsonrpc": "2.0",
                "id": "tools",
                "result": {"tools": [{"name": name} for name in sorted(HUB_REQUIRED_TOOLS)]},
            },
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = probe_mcp_tools(
        "http://127.0.0.1:1/hub/ai/mcp",
        MCP_SECRET,
        HUB_REQUIRED_TOOLS,
        endpoint="hub",
        retry_backoff_seconds=0,
    )

    assert result.ready is True
    assert calls == ["initialize", "initialize", "tools/list"]


def test_auth_failure_is_not_retried_into_success() -> None:
    with fake_mcp_server({"/hub/ai/mcp": Route(HUB_REQUIRED_TOOLS)}) as (base_url, state):
        result = probe_mcp_tools(
            f"{base_url}/hub/ai/mcp",
            "wrong-bearer",
            HUB_REQUIRED_TOOLS,
            endpoint="hub",
            retry_backoff_seconds=0,
        )

    assert result.ready is False
    assert result.reason == "http_status"
    assert [(path, method) for path, method, _auth in state.calls] == [
        ("/hub/ai/mcp", "initialize"),
    ]


def test_missing_required_tools_is_not_retried_into_success() -> None:
    route = Route(frozenset({"mydl.bubble.list"}))
    with fake_mcp_server({"/hub/ai/mcp": route}) as (base_url, state):
        result = probe_mcp_tools(
            f"{base_url}/hub/ai/mcp",
            MCP_SECRET,
            HUB_REQUIRED_TOOLS,
            endpoint="hub",
            retry_backoff_seconds=0,
        )

    assert result.ready is False
    assert result.reason == "missing_required_tools"
    assert [(path, method) for path, method, _auth in state.calls] == [
        ("/hub/ai/mcp", "initialize"),
        ("/hub/ai/mcp", "tools/list"),
    ]


def test_probe_failure_does_not_leak_bearer_values_or_forbidden_labels() -> None:
    with fake_mcp_server({"/hub/ai/mcp": Route(HUB_REQUIRED_TOOLS)}) as (base_url, _state):
        result = probe_mcp_tools(
            f"{base_url}/hub/ai/mcp",
            "wrong-bearer",
            HUB_REQUIRED_TOOLS,
            endpoint="hub",
        )

    rendered = repr(result)
    for forbidden in FORBIDDEN_OUTPUT:
        assert forbidden not in rendered
