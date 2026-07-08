"""
FastMCP server exposing Nexus Repository Manager tools.

Every tool requires **host** — the Nexus server hostname/IP supplied by the
user (e.g. `nexus.example.com`).  Credentials are loaded from environment
variables (injected by ExternalSecret from AWS Secrets Manager).

Tools:
  - list_repositories   : list all visible repositories
  - search_components   : generic component search (Maven, npm, PyPI, raw…)
  - search_docker_tags  : list Docker image tags sorted newest first
  - get_latest_version  : return the single latest version of a component
"""

import logging
import time as _time
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP
from opentelemetry import trace as _otel_trace
from pydantic import Field

from app import nexus_client as nexus
from app.nexus_client import DEFAULT_HOST

logger = logging.getLogger(__name__)

_request_info: ContextVar[dict] = ContextVar("request_info", default={})


def _audit(tool: str, status: str, duration_ms: int, **kv) -> None:
    span = _otel_trace.get_current_span()
    ctx = span.get_span_context()
    trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else ""

    req = _request_info.get({})
    caller = ""
    if trace_id:
        caller += f" trace_id={trace_id}"
    if req.get("client_ip"):
        caller += f" client_ip={req['client_ip']}"
    if req.get("consumer"):
        caller += f" consumer={req['consumer']}"

    extra = "".join(f" {k}={v}" for k, v in kv.items() if v is not None)
    logger.info("AUDIT tool=%s status=%s duration_ms=%d%s%s", tool, status, duration_ms, caller, extra)


_HOST_DOC = (
    "**host**: Nexus Repository Manager hostname or IP "
    "(e.g. `nexus.example.com` or `10.244.20.62:8081`). "
    f"Defaults to `{DEFAULT_HOST}` — only override if targeting a different Nexus instance."
)

mcp = FastMCP(
    "nexus-mcp-server",
    instructions=(
        "Read-only access to Nexus Repository Manager 3 at `registry-nexus.orbis.dedalus.com`. "
        "Use these tools to search for Docker images, Maven artifacts, npm packages, "
        "or any other component stored in the Nexus registry. "
        "The **host** parameter defaults to the configured Nexus instance — "
        "only ask the user if they want to query a different Nexus host. "
        "Credentials are pre-configured — do not ask for them."
    ),
)


# ── Tool: list_repositories ────────────────────────────────────────────────────

@mcp.tool(
    description=(
        _HOST_DOC + "\n\n"
        "List all repositories visible to the configured user."
    )
)
async def list_repositories(host: str = DEFAULT_HOST) -> list[dict]:
    _t0 = _time.monotonic()
    try:
        repos = await nexus.list_repositories(host)
        _audit("list_repositories", "success", int((_time.monotonic() - _t0) * 1000),
               host=host, count=len(repos))
        return repos
    except PermissionError as exc:
        _audit("list_repositories", "permission_error", int((_time.monotonic() - _t0) * 1000),
               host=host, error=str(exc)[:120])
        raise ValueError(str(exc)) from exc
    except Exception as exc:
        _audit("list_repositories", "error", int((_time.monotonic() - _t0) * 1000),
               host=host, error=str(exc)[:120])
        raise


# ── Tool: search_components ────────────────────────────────────────────────────

@mcp.tool(
    description=(
        _HOST_DOC + "\n\n"
        "Search for components (Docker, Maven, npm, PyPI, raw…) in Nexus.\n\n"
        "**repository**: repository name filter (optional).\n\n"
        "**name**: component name filter — supports wildcards `*` (optional).\n\n"
        "**group**: group/namespace filter, e.g. Maven groupId (optional).\n\n"
        "**version**: exact version filter (optional).\n\n"
        "**format**: `docker`, `maven2`, `npm`, `pypi`, `raw`… (optional).\n\n"
        "**max_results**: max items to return (1–100, default 25)."
    )
)
async def search_components(
    host: str = DEFAULT_HOST,
    repository: str | None = None,
    name: str | None = None,
    group: str | None = None,
    version: str | None = None,
    format: str | None = None,
    max_results: int = Field(default=25, ge=1, le=100),
) -> list[dict]:
    _t0 = _time.monotonic()
    try:
        results = await nexus.search_components(
            host=host,
            repository=repository,
            name=name,
            group=group,
            version=version,
            format=format,
            max_results=max_results,
        )
        _audit("search_components", "success", int((_time.monotonic() - _t0) * 1000),
               host=host, repository=repository, name=name, format=format, count=len(results))
        return results
    except PermissionError as exc:
        _audit("search_components", "permission_error", int((_time.monotonic() - _t0) * 1000),
               host=host, error=str(exc)[:120])
        raise ValueError(str(exc)) from exc
    except Exception as exc:
        _audit("search_components", "error", int((_time.monotonic() - _t0) * 1000),
               host=host, error=str(exc)[:120])
        raise


# ── Tool: search_docker_tags ───────────────────────────────────────────────────

@mcp.tool(
    description=(
        _HOST_DOC + "\n\n"
        "List Docker image tags for a given image, sorted newest first.\n\n"
        "**repository**: Docker repository name in Nexus (e.g. `docker-hosted`, "
        "`local-docker-repository`).\n\n"
        "**image_name**: image name to search (e.g. `hdc-oracle-mcp-server`). "
        "Wildcards `*` supported.\n\n"
        "**max_results**: max tags to return (1–100, default 20)."
    )
)
async def search_docker_tags(
    host: str = DEFAULT_HOST,
    repository: str = "docker-hosted",
    image_name: str,
    max_results: int = Field(default=20, ge=1, le=100),
) -> list[dict]:
    _t0 = _time.monotonic()
    try:
        results = await nexus.search_docker_tags(
            host=host,
            repository=repository,
            image_name=image_name,
            max_results=max_results,
        )
        _audit("search_docker_tags", "success", int((_time.monotonic() - _t0) * 1000),
               host=host, repository=repository, image=image_name, count=len(results))
        return results
    except PermissionError as exc:
        _audit("search_docker_tags", "permission_error", int((_time.monotonic() - _t0) * 1000),
               host=host, error=str(exc)[:120])
        raise ValueError(str(exc)) from exc
    except Exception as exc:
        _audit("search_docker_tags", "error", int((_time.monotonic() - _t0) * 1000),
               host=host, error=str(exc)[:120])
        raise


# ── Tool: get_latest_version ───────────────────────────────────────────────────

@mcp.tool(
    description=(
        _HOST_DOC + "\n\n"
        "Return the single latest version of a component.\n\n"
        "**repository**: repository name in Nexus.\n\n"
        "**name**: exact component name (or wildcard).\n\n"
        "**group**: group/namespace (optional).\n\n"
        "**format**: `docker`, `maven2`, `npm`, `pypi`, `raw`… (optional).\n\n"
        "Returns null if no component is found."
    )
)
async def get_latest_version(
    host: str = DEFAULT_HOST,
    repository: str = "",
    name: str,
    group: str | None = None,
    format: str | None = None,
) -> dict | None:
    _t0 = _time.monotonic()
    try:
        result = await nexus.get_latest_version(
            host=host,
            repository=repository,
            name=name,
            group=group,
            format=format,
        )
        _audit("get_latest_version", "success", int((_time.monotonic() - _t0) * 1000),
               host=host, repository=repository, name=name,
               version=result.get("version") if result else None)
        return result
    except PermissionError as exc:
        _audit("get_latest_version", "permission_error", int((_time.monotonic() - _t0) * 1000),
               host=host, error=str(exc)[:120])
        raise ValueError(str(exc)) from exc
    except Exception as exc:
        _audit("get_latest_version", "error", int((_time.monotonic() - _t0) * 1000),
               host=host, error=str(exc)[:120])
        raise
