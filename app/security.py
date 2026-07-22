"""
Security layer for Nexus MCP Server.

- HostValidator      : validate host names to prevent SSRF
- SearchParamValidator: sanitize search parameters to prevent URL/CRLF injection
- OutputSanitizer    : truncate and clean Nexus API responses
"""

from __future__ import annotations

import re
from typing import Any

# ── Limits ─────────────────────────────────────────────────────────────────────

MAX_PARAM_LENGTH = 256          # max chars for any search parameter value
MAX_STRING_VALUE_LENGTH = 4_000 # max chars for a single string in the output
MAX_OUTPUT_ITEMS = 100          # absolute cap on items returned to the LLM
MAX_NESTED_LIST_ITEMS = 50      # cap for nested lists inside a response item

# ── Patterns ───────────────────────────────────────────────────────────────────

# RFC 1123 hostname with optional :port  (e.g. nexus.example.com:8081)
_HOST_RE = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9.\-]{0,252}(:\d{1,5})?$"
)
# IPv4 address with optional :port  (e.g. 10.244.20.62:8081)
_IP_RE = re.compile(
    r"^(\d{1,3}\.){3}\d{1,3}(:\d{1,5})?$"
)
# Nexus search param: alphanumeric + . - _ / * : (covers image paths and wildcards)
_SEARCH_PARAM_RE = re.compile(r"^[a-zA-Z0-9._\-/:*]{1,256}$")

# Allowed Nexus repository formats (open list — validated for known values,
# unknown formats are passed through after basic character check)
_KNOWN_FORMATS: frozenset[str] = frozenset(
    {"docker", "maven2", "npm", "pypi", "raw", "nuget", "helm", "conda",
     "apt", "yum", "go", "rubygems", "cocoapods", "conan", "r"}
)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class HostValidationError(ValueError):
    """Raised when a host string fails SSRF / format validation."""


class SearchParamValidationError(ValueError):
    """Raised when a search parameter fails validation."""


# ── HostValidator ──────────────────────────────────────────────────────────────

class HostValidator:
    """
    Validates the Nexus host parameter to prevent SSRF attacks.

    Blocks:
    - Loopback addresses (localhost, 127.0.0.1, ::1)
    - AWS/GCP/Azure metadata endpoints (169.254.169.254)
    - Wildcard / unspecified addresses (0.0.0.0)
    - Malformed hostnames containing path separators, whitespace, or injection chars

    Allows:
    - RFC 1123 hostnames with optional :port
    - IPv4 addresses with optional :port
    """

    _BLOCKED_HOSTS: frozenset[str] = frozenset(
        {
            "localhost",
            "127.0.0.1",
            "::1",
            "0.0.0.0",
            "169.254.169.254",   # AWS / GCP / Azure IMDS
            "metadata.google.internal",
        }
    )

    @staticmethod
    def validate(host: str) -> str:
        if not isinstance(host, str) or not host.strip():
            raise HostValidationError("host must be a non-empty string")
        h = host.strip()

        # Reject null bytes, whitespace, CRLF
        if any(c in h for c in ("\x00", "\r", "\n", " ", "\t")):
            raise HostValidationError(
                f"host '{h}' contains invalid characters"
            )

        # Block known dangerous targets (check hostname without port)
        hostname_only = h.split(":")[0].lower()
        if hostname_only in HostValidator._BLOCKED_HOSTS:
            raise HostValidationError(f"host '{h}' is not permitted")

        # Validate format (RFC 1123 or IPv4, with optional :port)
        if not (_HOST_RE.match(h) or _IP_RE.match(h)):
            raise HostValidationError(
                f"Invalid host '{h}': expected hostname[:port] or IPv4[:port]"
            )

        # Validate port range if present
        if ":" in h:
            port_part = h.rsplit(":", 1)[-1]
            try:
                port = int(port_part)
                if not (1 <= port <= 65535):
                    raise HostValidationError(
                        f"Invalid port {port}: must be between 1 and 65535"
                    )
            except ValueError:
                raise HostValidationError(
                    f"Invalid port '{port_part}' in host '{h}'"
                )

        return h


# ── SearchParamValidator ───────────────────────────────────────────────────────

class SearchParamValidator:
    """
    Validates search parameters sent as URL query-string values to Nexus.

    Prevents:
    - CRLF injection (\\r\\n could split HTTP headers)
    - Null byte injection
    - Excessively long values
    - Characters outside the expected charset for Nexus search fields
    """

    # Characters that must never appear in any search parameter
    _FORBIDDEN_CHARS = frozenset("\x00\r\n\t")

    @staticmethod
    def validate(value: str | None, label: str) -> str | None:
        """Return the validated (stripped) value, or None if empty/None."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise SearchParamValidationError(f"{label} must be a string, got {type(value).__name__}")

        v = value.strip()
        if not v:
            return None

        if len(v) > MAX_PARAM_LENGTH:
            raise SearchParamValidationError(
                f"{label} too long: {len(v)} chars (max {MAX_PARAM_LENGTH})"
            )

        if any(c in v for c in SearchParamValidator._FORBIDDEN_CHARS):
            raise SearchParamValidationError(
                f"{label} contains forbidden characters (null byte or CRLF)"
            )

        if not _SEARCH_PARAM_RE.match(v):
            raise SearchParamValidationError(
                f"Invalid {label} '{v}': only alphanumeric, '.', '-', '_', '/', ':', '*' allowed"
            )

        return v

    @staticmethod
    def validate_format(fmt: str | None) -> str | None:
        """Validate and return a Nexus repository format string."""
        v = SearchParamValidator.validate(fmt, "format")
        if v is None:
            return None
        if v.lower() not in _KNOWN_FORMATS:
            # Unknown format — apply stricter alphanumeric-only check
            if not re.match(r"^[a-zA-Z0-9]{1,32}$", v):
                raise SearchParamValidationError(
                    f"Unknown format '{v}': must be alphanumeric (max 32 chars)"
                )
        return v.lower()

    @staticmethod
    def validate_max_results(value: int, default: int, min_val: int = 1, max_val: int = 100) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise SearchParamValidationError("max_results must be an integer")
        if not (min_val <= value <= max_val):
            raise SearchParamValidationError(
                f"max_results must be between {min_val} and {max_val}, got {value}"
            )
        return value


# ── OutputSanitizer ────────────────────────────────────────────────────────────

class OutputSanitizer:
    """
    Sanitizes Nexus REST API responses before returning them to the LLM.

    - Caps the number of returned items
    - Truncates long string values
    - Caps nested list lengths
    - Ensures everything is JSON-serializable (str/int/float/bool/None/list/dict)
    - Removes asset download URLs that could leak internal infrastructure paths
    """

    # Asset fields that expose internal infrastructure URLs — strip them
    _STRIP_ASSET_FIELDS: frozenset[str] = frozenset(
        {"downloadUrl", "blobStoreName", "checksum"}
    )

    @classmethod
    def sanitize_items(
        cls, items: list[Any], max_items: int = MAX_OUTPUT_ITEMS
    ) -> list[Any]:
        capped = items[:max_items]
        return [cls.sanitize_value(item) for item in capped]

    @classmethod
    def sanitize_value(cls, value: Any) -> Any:  # noqa: ANN401
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            if len(value) > MAX_STRING_VALUE_LENGTH:
                return value[:MAX_STRING_VALUE_LENGTH] + "…[truncated]"
            return value
        if isinstance(value, list):
            return [cls.sanitize_value(v) for v in value[:MAX_NESTED_LIST_ITEMS]]
        if isinstance(value, dict):
            return cls.sanitize_dict(value)
        # Fallback: convert unknown types to string
        return str(value)[:MAX_STRING_VALUE_LENGTH]

    @classmethod
    def sanitize_dict(cls, d: dict[str, Any]) -> dict[str, Any]:
        return {
            k: cls.sanitize_value(v)
            for k, v in d.items()
            if k not in cls._STRIP_ASSET_FIELDS
        }
