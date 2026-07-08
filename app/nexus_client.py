"""
Read-only Nexus Repository Manager 3 client.

All operations use the Nexus REST API v1 (GET only).
Credentials are loaded once at startup from environment variables
(NEXUS_USER / NEXUS_PASSWORD) injected via ExternalSecret.
"""
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_USER = os.environ.get("NEXUS_USER", "")
_PASSWORD = os.environ.get("NEXUS_PASSWORD", "")
DEFAULT_HOST = os.environ.get("NEXUS_HOST", "registry-nexus.orbis.dedalus.com")

# Per-host HTTP client cache (host supplied at call-time)
_clients: dict[str, httpx.AsyncClient] = {}


def _client(host: str) -> httpx.AsyncClient:
    if host not in _clients:
        _clients[host] = httpx.AsyncClient(
            base_url=f"https://{host}",
            auth=(_USER, _PASSWORD),
            timeout=30.0,
            verify=False,  # internal registry — self-signed cert common
        )
    return _clients[host]


async def close_all() -> None:
    for c in _clients.values():
        await c.aclose()
    _clients.clear()


# ── helpers ────────────────────────────────────────────────────────────────────

def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code == 401:
        raise PermissionError("Nexus authentication failed — check NEXUS_USER/NEXUS_PASSWORD")
    if resp.status_code == 403:
        raise PermissionError(f"Nexus access denied: {resp.text[:200]}")
    resp.raise_for_status()


# ── public API ─────────────────────────────────────────────────────────────────

async def list_repositories(host: str) -> list[dict[str, Any]]:
    """Return all repositories visible to the configured user."""
    resp = await _client(host).get("/service/rest/v1/repositories")
    _raise_for_status(resp)
    repos = resp.json()
    logger.info("list_repositories host=%s count=%d", host, len(repos))
    return repos


async def search_components(
    host: str,
    repository: str | None,
    name: str | None,
    group: str | None,
    version: str | None,
    format: str | None,
    sort: str = "version",
    direction: str = "desc",
    max_results: int = 25,
) -> list[dict[str, Any]]:
    """
    Search for components using the Nexus search API.
    Returns at most *max_results* items.
    """
    params: dict[str, str] = {"sort": sort, "direction": direction}
    if repository:
        params["repository"] = repository
    if name:
        params["name"] = name
    if group:
        params["group"] = group
    if version:
        params["version"] = version
    if format:
        params["format"] = format

    items: list[dict] = []
    continuation_token: str | None = None

    while len(items) < max_results:
        if continuation_token:
            params["continuationToken"] = continuation_token

        resp = await _client(host).get("/service/rest/v1/search", params=params)
        _raise_for_status(resp)
        data = resp.json()

        batch = data.get("items", [])
        items.extend(batch)

        continuation_token = data.get("continuationToken")
        if not continuation_token or not batch:
            break

    result = items[:max_results]
    logger.info(
        "search_components host=%s name=%s repository=%s format=%s returned=%d",
        host, name, repository, format, len(result),
    )
    return result


async def search_docker_tags(
    host: str,
    repository: str,
    image_name: str,
    max_results: int = 20,
) -> list[dict[str, Any]]:
    """
    Return Docker image tags for *image_name* in *repository*, newest first.
    Each item contains: name, version, assets (with digest + size).
    """
    components = await search_components(
        host=host,
        repository=repository,
        name=image_name,
        group=None,
        version=None,
        format="docker",
        sort="version",
        direction="desc",
        max_results=max_results,
    )
    return components


async def get_latest_version(
    host: str,
    repository: str,
    name: str,
    group: str | None = None,
    format: str | None = None,
) -> dict[str, Any] | None:
    """
    Return the single latest version of a component (first result sorted by
    version desc).  Returns None if nothing is found.
    """
    results = await search_components(
        host=host,
        repository=repository,
        name=name,
        group=group,
        version=None,
        format=format,
        sort="version",
        direction="desc",
        max_results=1,
    )
    return results[0] if results else None
