"""MyDL MCP readiness probes for the Odysseus runtime scaffold."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

HUB_REQUIRED_TOOLS = frozenset(
    {
        "mydl.bubble.list",
        "mydl.bubble.evaluate_local",
    },
)

SOCIAL_REQUIRED_TOOLS = frozenset(
    {
        "mydl.ads.fill",
        "mydl.ads.record_render",
        "mydl.ads.receipt_view",
        "mydl.ads.receipt_click",
        "mydl.ads.receipt_completion",
        "mydl.feed.preview_ask",
        "mydl.feed.submit_ask",
        "mydl.feed.preview_post",
        "mydl.feed.submit_post",
        "mydl.feed.preview_review",
        "mydl.feed.submit_review",
        "mydl.bubble.preview_learned_facts",
        "mydl.bubble.submit_learned_facts",
    },
)

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 0.25


@dataclass(frozen=True, slots=True)
class McpProbeResult:
    """Secret-free result for a single MCP endpoint readiness probe."""

    endpoint: str
    ready: bool
    reason: str
    observed_tool_count: int = 0
    missing_required_tools: tuple[str, ...] = ()

    @classmethod
    def success(cls, endpoint: str, observed_tool_count: int) -> McpProbeResult:
        return cls(
            endpoint=endpoint,
            ready=True,
            reason="ok",
            observed_tool_count=observed_tool_count,
        )

    @classmethod
    def failure(
        cls,
        endpoint: str,
        reason: str,
        *,
        observed_tool_count: int = 0,
        missing_required_tools: tuple[str, ...] = (),
    ) -> McpProbeResult:
        return cls(
            endpoint=endpoint,
            ready=False,
            reason=reason,
            observed_tool_count=observed_tool_count,
            missing_required_tools=missing_required_tools,
        )


@dataclass(frozen=True, slots=True)
class McpReadiness:
    hub: McpProbeResult
    social: McpProbeResult


def probe_configured_mcp_tools(config: Any) -> McpReadiness:
    """Probe the configured MyDL Hub and Social MCP tool contracts."""

    return McpReadiness(
        hub=probe_mcp_tools(
            config.hub_mcp_url,
            config.mcp_bearer,
            HUB_REQUIRED_TOOLS,
            endpoint="hub",
        ),
        social=probe_mcp_tools(
            config.social_mcp_url,
            config.mcp_bearer,
            SOCIAL_REQUIRED_TOOLS,
            endpoint="social",
        ),
    )


def probe_mcp_tools(
    url: str,
    bearer: str,
    required_tools: frozenset[str],
    *,
    endpoint: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> McpProbeResult:
    init_status, init_payload = _post_json_rpc(
        url,
        bearer,
        _json_rpc(f"{endpoint}-initialize", "initialize"),
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    init_failure = _response_failure_reason(init_status, init_payload)
    if init_failure is not None:
        return McpProbeResult.failure(endpoint, init_failure)
    if not isinstance(init_payload.get("result"), dict):
        return McpProbeResult.failure(endpoint, "missing_result")

    tools_status, tools_payload = _post_json_rpc(
        url,
        bearer,
        _json_rpc(f"{endpoint}-tools", "tools/list"),
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    tools_failure = _response_failure_reason(tools_status, tools_payload)
    if tools_failure is not None:
        return McpProbeResult.failure(endpoint, tools_failure)

    result = tools_payload.get("result")
    if not isinstance(result, dict):
        return McpProbeResult.failure(endpoint, "missing_result")

    tools = result.get("tools")
    if not isinstance(tools, list):
        return McpProbeResult.failure(endpoint, "invalid_tools")

    names = {
        tool.get("name")
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }
    missing = tuple(sorted(required_tools - names))
    if missing:
        return McpProbeResult.failure(
            endpoint,
            "missing_required_tools",
            observed_tool_count=len(names),
            missing_required_tools=missing,
        )
    return McpProbeResult.success(endpoint, observed_tool_count=len(names))


def _post_json_rpc(
    url: str,
    bearer: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    attempts = max(1, max_attempts)
    last_status = 0
    last_payload: dict[str, Any] = {}
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        status, response_payload = _post_json_rpc_once(
            request,
            timeout_seconds=timeout_seconds,
        )
        last_status = status
        last_payload = response_payload
        if not _is_retryable_status(status) or attempt == attempts - 1:
            return status, response_payload
        time.sleep(retry_backoff_seconds)
    return last_status, last_payload


def _post_json_rpc_once(
    request: urllib.request.Request,
    *,
    timeout_seconds: float,
) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, _decode_json_object(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (OSError, TimeoutError, urllib.error.URLError):
        return 0, {}
    except ValueError:
        return 200, {}


def _decode_json_object(raw: bytes) -> dict[str, Any]:
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("MCP response must be a JSON object")
    return decoded


def _response_failure_reason(status: int, payload: dict[str, Any]) -> str | None:
    if status != 200:
        return "http_status"
    if not payload or payload.get("jsonrpc") != "2.0":
        return "invalid_json_rpc"
    if "error" in payload:
        return "json_rpc_error"
    return None


def _is_retryable_status(status: int) -> bool:
    return status == 0 or 500 <= status <= 599


def _json_rpc(request_id: str, method: str) -> dict[str, str]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method}
