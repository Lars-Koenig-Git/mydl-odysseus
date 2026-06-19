"""Standalone MyDL runtime surface for the Odysseus fork."""

from __future__ import annotations

import html
import urllib.parse
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import RuntimeConfig
from .mcp_probe import McpReadiness
from .model_loader import ModelLoadResult


@dataclass(frozen=True, slots=True)
class RuntimeState:
    config: RuntimeConfig
    mcp_readiness: McpReadiness | None = None
    model_load_result: ModelLoadResult | None = None

    @property
    def model_configured(self) -> bool:
        return self.config.model_path.is_file()

    @property
    def model_loaded(self) -> bool:
        return (
            self.model_load_result.loaded
            if self.model_load_result is not None
            else False
        )

    @property
    def hub_mcp_tools_ready(self) -> bool:
        return self.mcp_readiness.hub.ready if self.mcp_readiness is not None else False

    @property
    def social_mcp_tools_ready(self) -> bool:
        return self.mcp_readiness.social.ready if self.mcp_readiness is not None else False

    @property
    def brain_store_configured(self) -> bool:
        return True

    def health_payload(self) -> dict[str, bool | str]:
        ready = (
            self.model_configured
            and self.model_loaded
            and self.hub_mcp_tools_ready
            and self.social_mcp_tools_ready
            and self.brain_store_configured
        )
        return {
            "status": "ok" if ready else "not_ready",
            "model_configured": self.model_configured,
            "model_loaded": self.model_loaded,
            "hub_mcp_tools_ready": self.hub_mcp_tools_ready,
            "social_mcp_tools_ready": self.social_mcp_tools_ready,
            "brain_store_configured": self.brain_store_configured,
        }


def create_mydl_runtime_app(
    config: RuntimeConfig,
    *,
    mcp_readiness: McpReadiness | None = None,
    model_load_result: ModelLoadResult | None = None,
) -> FastAPI:
    state = RuntimeState(
        config=config,
        mcp_readiness=mcp_readiness,
        model_load_result=model_load_result,
    )
    app = FastAPI(title="MyDL Odysseus Runtime", version="0.1.0")
    app.state.mydl_runtime = state
    app.state.mydl_mcp_readiness = mcp_readiness
    app.state.mydl_loaded_model = (
        model_load_result.model if model_load_result is not None else None
    )

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(state.health_payload(), headers=_embed_headers(config))

    @app.get("/")
    async def surface(request: Request) -> HTMLResponse:
        mode = request.query_params.get("mode", "workspace")
        return HTMLResponse(
            _surface_html(mode),
            headers=_embed_headers(config),
        )

    return app


def _embed_headers(config: RuntimeConfig) -> dict[str, str]:
    ancestors = " ".join(_frame_ancestors(config))
    return {
        "Content-Security-Policy": (
            "default-src 'self'; "
            "base-uri 'none'; "
            "object-src 'none'; "
            f"frame-ancestors {ancestors}"
        ),
        "Referrer-Policy": "no-referrer",
    }


def _frame_ancestors(config: RuntimeConfig) -> list[str]:
    values = {"'self'"}
    for raw_url in (
        config.hub_mcp_url,
        config.social_mcp_url,
        config.brain_store_url,
    ):
        parsed = urllib.parse.urlparse(raw_url)
        if parsed.scheme and parsed.netloc:
            values.add(urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")))
    return sorted(values)


def _surface_html(mode: str) -> str:
    label = "Direct messages" if mode == "dm" else "Workspace"
    escaped_label = html.escape(label)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Odysseus {escaped_label}</title>
    <style>
      :root {{
        color-scheme: light dark;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: #101820;
        color: #f7f4ea;
      }}
      main {{
        width: min(760px, calc(100vw - 32px));
        padding: 28px;
        border: 1px solid rgba(247, 244, 234, 0.18);
        border-radius: 8px;
        background: rgba(16, 24, 32, 0.86);
      }}
      h1 {{
        margin: 0 0 12px;
        font-size: 28px;
        font-weight: 700;
        letter-spacing: 0;
      }}
      p {{
        margin: 0;
        line-height: 1.5;
        color: #d8dee6;
      }}
    </style>
  </head>
  <body>
    <main>
      <h1>Odysseus {escaped_label}</h1>
      <p>Runtime surface is available for embedding.</p>
    </main>
  </body>
</html>
"""
