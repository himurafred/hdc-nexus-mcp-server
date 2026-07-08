"""
FastAPI application entry point for the Nexus MCP server.

Mounts the FastMCP server at /mcp (stateless HTTP, JSON transport).
Provides /health endpoint (unauthenticated).
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ── OpenTelemetry — set up before any other import ─────────────────────────────
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_OTEL_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector.monitoring.svc.cluster.local:4317"
)
_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "nexus-mcp-server")
_RESOURCE_ATTRS = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "")

_resource_kv: dict = {"service.name": _SERVICE_NAME}
for pair in _RESOURCE_ATTRS.split(","):
    if "=" in pair:
        k, v = pair.split("=", 1)
        _resource_kv[k.strip()] = v.strip()

_resource = Resource.create(_resource_kv)
_tracer_provider = TracerProvider(resource=_resource)
_tracer_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=_OTEL_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(_tracer_provider)

_meter_provider = MeterProvider(
    resource=_resource,
    metric_readers=[
        PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=_OTEL_ENDPOINT, insecure=True),
            export_interval_millis=30_000,
        )
    ],
)
metrics.set_meter_provider(_meter_provider)

_logger_provider = LoggerProvider(resource=_resource)
_logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=_OTEL_ENDPOINT, insecure=True))
)
set_logger_provider(_logger_provider)

LoggingInstrumentor().instrument(set_logging_format=True)
_otel_log_handler = LoggingHandler(level=logging.NOTSET, logger_provider=_logger_provider)
logging.getLogger().addHandler(_otel_log_handler)

# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

from app import nexus_client
from app.mcp_server import mcp, _request_info


@asynccontextmanager
async def _lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield
    logger.info("nexus-mcp-server shutting down — closing HTTP clients")
    await nexus_client.close_all()


app = FastAPI(
    title="nexus-mcp-server",
    redirect_slashes=False,
    lifespan=_lifespan,
)


def _server_request_hook(span, scope):
    """Rename OTel spans for MCP endpoints to include method + path."""
    if span and span.is_recording():
        path = scope.get("path", "")
        method = scope.get("method", "")
        if path and method and path != "/health":
            span.update_name(f"{method} {path}")


FastAPIInstrumentor.instrument_app(
    app,
    excluded_urls="/health",
    server_request_hook=_server_request_hook,
)


# ── Audit middleware — capture client IP + Kong consumer ─────────────────────

@app.middleware("http")
async def _audit_middleware(request: Request, call_next):
    ip = (
        request.headers.get("X-Real-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or (request.client.host if request.client else "")
    )
    _request_info.set({
        "client_ip": ip,
        "consumer": request.headers.get("X-Consumer-Username", "")
                    or request.headers.get("X-Consumer-Id", ""),
        "authenticated_user": request.headers.get("X-Authenticated-User", ""),
    })
    return await call_next(request)


# ── Health probe ──────────────────────────────────────────────────────────────

@app.get("/health", include_in_schema=False)
async def health():
    return JSONResponse({"status": "ok", "service": "nexus-mcp-server"})


# ── Mount MCP at root so FastMCP's internal /mcp route is exposed at /mcp ────

app.mount("/", mcp.streamable_http_app())
