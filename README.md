# hdc-nexus-mcp-server

Read-only MCP server for searching Nexus Repository Manager 3.

## Tools

| Tool | Description |
|------|-------------|
| `list_repositories` | List all visible repositories |
| `search_components` | Search components (Docker, Maven, npm, PyPI…) by name/group/version/format |
| `search_docker_tags` | List Docker image tags for a given image, newest first |
| `find_docker_image` | Auto-discover a Docker image by base name, probing known namespace prefixes (`oas/`, `orbis-u/`, `hdc/`, `local/`, none) |
| `get_latest_version` | Get the single latest version of a component |

Every tool takes **`host`** as first parameter (Nexus hostname, defaults to `registry-nexus.orbis.dedalus.com`). Only override if targeting a different Nexus instance — do not set it to a repository name.
Credentials (`NEXUS_USER` / `NEXUS_PASSWORD`) are injected via ExternalSecret from AWS Secrets Manager (`nexus/dev/credentials`).

## Build & push

```bash
# Build
docker build --tag 10.244.20.62:8081/local-docker-repository/hdc-nexus-mcp-server:latest .

# Push
docker push 10.244.20.62:8081/local-docker-repository/hdc-nexus-mcp-server:latest
```

## AWS Secret (first-time setup)

```bash
aws secretsmanager create-secret \
  --name nexus/dev/credentials \
  --secret-string '{"user":"<nexus-user>","password":"<nexus-password>"}' \
  --region eu-central-1
```

## Architecture

```
Client → NGF (nginx-gateway) → Kong (key-auth) → nexus-mcp-server:8000/mcp
                                                         ↓
                                             Nexus REST API v1 (GET only)
                                             https://<host>/service/rest/v1/
```

## Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NEXUS_USER` | Nexus username (from secret) | — |
| `NEXUS_PASSWORD` | Nexus password (from secret) | — |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTel collector endpoint | `http://otel-collector...4317` |
| `OTEL_SERVICE_NAME` | Service name for traces/logs | `nexus-mcp-server` |
| `OTEL_RESOURCE_ATTRIBUTES` | Extra OTel resource attributes | — |
| `LOG_LEVEL` | Log level | `INFO` |
