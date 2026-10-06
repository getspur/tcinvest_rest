# Extracted from spur-notebook; launcher documentation updated for this repository.
"""Local REST facade over the tcinvest MCP server.

Another application queries HTTP:

    GET  /health
    GET  /tools
    GET  /openapi.json
    GET  /tools/{name}?arg=value
    POST /tools/{name}   JSON body = tool arguments

Auth is TCBS OAuth. The notebook gateway mints an access token via
``oauth2_refresh`` and sends ``Authorization: Bearer <access_token>``.
That token is forwarded to ``https://mcp.tcbs.com.vn/mcp/tcinvest/``.
If the request has no bearer, the facade uses the stored mcp-remote OAuth session.

Bootstrap login and then serve (run from the repository root):

    uv run --no-project --with certifi python tcinvest_rest.py --bootstrap

The bootstrap opens browser authorization when needed, waits for the callback on
port 3334 (up to 600 seconds), verifies tools/list, then serves REST on port 8788.
Use --auth-port / --auth-timeout to change those OAuth settings. Node.js/npx is
required for bootstrap. Without --bootstrap, an existing token is required;
the REST facade does not refresh expired tokens itself. Restart with --bootstrap
to refresh or sign in again. Tokens are never printed by this launcher.
For a fresh login without changing existing sessions, set MCP_REMOTE_CONFIG_DIR
to a new directory before running --bootstrap.
The MCP URL is preserved exactly, including its trailing slash, to match
mcp-remote's credential cache. To reuse a session created for the legacy URL
without a slash, pass --mcp-url https://mcp.tcbs.com.vn/mcp/tcinvest explicitly.

TCBS compatibility bootstrap (verified with browser consent and tools/list):

    MCP_REMOTE_CONFIG_DIR="$HOME/.mcp-auth/tcinvest-compat-0.8.7" uv run --no-project --with certifi python tcinvest_rest.py --bootstrap --mcp-remote-version 0.8.7

The default client remains 0.14.3. Version 0.8.7 retains PKCE but does not check
metadata issuer equality; it works around TCBS's inconsistent issuer metadata.
Use this explicit compatibility option until TCBS's metadata is corrected.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

DEFAULT_MCP_URL = "https://mcp.tcbs.com.vn/mcp/tcinvest/"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8788
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
USER_AGENT = "tcinvest-rest/0.1"
DEFAULT_MCP_REMOTE_VERSION = "0.14.3"
MCP_REMOTE_VERSIONS = (DEFAULT_MCP_REMOTE_VERSION, "0.8.7")


def _operation_id(tool_name: str) -> str:
    raw = tool_name.removeprefix("tcinvest-")
    out: list[str] = []
    for char in raw:
        if char.isupper():
            if out and out[-1] != "_":
                out.append("_")
            out.append(char.lower())
        elif char == "-":
            if out and out[-1] != "_":
                out.append("_")
        else:
            out.append(char.lower())
    ident = "".join(out).strip("_")
    return ident or "tool"


class McpBackend(Protocol):
    def list_tools(self) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


@dataclass
class RestConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    bearer: str = ""


@dataclass
class FakeMcpBackend:
    tools: list[dict[str, Any]]
    results: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def list_tools(self) -> list[dict[str, Any]]:
        return list(self.tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if name not in self.results:
            raise KeyError(name)
        return self.results[name]


@dataclass
class RestHandle:
    port: int
    _httpd: ThreadingHTTPServer
    _thread: threading.Thread

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=5)
        self._httpd.server_close()


def resolve_tool_name(requested: str, tools: list[dict[str, Any]]) -> str | None:
    if not TOOL_NAME_RE.fullmatch(requested):
        return None
    names = [str(tool["name"]) for tool in tools]
    if requested in names:
        return requested
    prefixed = f"tcinvest-{requested}"
    if prefixed in names:
        return prefixed
    return None


def coerce_query_value(raw: str, schema: dict[str, Any] | None) -> Any:
    typ = (schema or {}).get("type")
    if typ == "integer":
        return int(raw)
    if typ == "number":
        return float(raw)
    if typ == "boolean":
        return raw.lower() in {"1", "true", "yes"}
    if typ == "array":
        return [item for item in raw.split(",") if item]
    return raw


# Single record-array fields to unnest into SQL rows. Skip envelopes that
# carry two sibling arrays (e.g. listInc + listDesc) so both stay intact.
_ROW_LIST_KEYS = (
    "listActivityNews",
    "listEventNews",
    "listShareHolder",
    "listKeyOfficer",
    "listIndustry",
    "listDividendPaymentHis",
    "listInsiderDealing",
    "listSubCompany",
    "listVolumeForeignInfoDto",
    "listTechnicalIndicator",
    "listAuditFirm",
    "listHisRecomItem",
    "seriesList",
    "result",
    "value",
    "data",
    "items",
    "rows",
    "body",
    "b",
    "c",
)


def _is_record_list(value: Any) -> bool:
    return isinstance(value, list) and (not value or isinstance(value[0], dict))


def normalize_rows(result: Any) -> list[dict[str, Any]]:
    parent: dict[str, Any] = {}
    if isinstance(result, list):
        items: list[Any] = result
    elif isinstance(result, dict):
        record_lists = [key for key, value in result.items() if _is_record_list(value)]
        chosen = next((key for key in _ROW_LIST_KEYS if key in record_lists), None)
        if chosen is not None:
            items = result[chosen]
            parent = {
                key: value
                for key, value in result.items()
                if key != chosen and not isinstance(value, (list, dict))
            }
        else:
            items = [result]
    else:
        items = [result]
    rows: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            row = dict(parent)
            row.update(item)
            row["payload"] = json.dumps(item, ensure_ascii=False)
            rows.append(row)
        else:
            rows.append({"payload": json.dumps(item, ensure_ascii=False), "value": item})
    return rows


def arguments_from_query(query: str, tool: dict[str, Any]) -> dict[str, Any]:
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    parsed = parse_qs(query, keep_blank_values=True)
    out: dict[str, Any] = {}
    for key, values in parsed.items():
        if not values:
            continue
        out[key] = coerce_query_value(values[-1], properties.get(key))
    return out


@cache
def _response_schemas() -> dict[str, Any]:
    """Reviewed JSON row contracts shipped alongside this standalone server."""
    path = Path(__file__).resolve().with_name("tcinvest.response-schemas.json")
    return json.loads(path.read_text(encoding="utf-8"))


def build_openapi(tools: list[dict[str, Any]]) -> dict[str, Any]:
    paths: dict[str, Any] = {
        "/health": {
            "get": {
                "summary": "Liveness",
                "responses": {"200": {"description": "ok"}},
            }
        },
        "/tools": {
            "get": {
                "operationId": "list_tools",
                "summary": "List tcinvest MCP tools",
                "responses": {
                    "200": {
                        "description": "tool catalog",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "tools": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "name": {"type": "string"},
                                                    "path": {"type": "string"},
                                                    "description": {"type": "string"},
                                                    "inputSchema": {"type": "object", "additionalProperties": True},
                                                },
                                            },
                                        }
                                    },
                                }
                            }
                        },
                    }
                },
            }
        },
    }
    for tool in tools:
        name = tool["name"]
        schema = tool.get("inputSchema") or {"type": "object"}
        path = f"/tools/{name}"
        row_schema = copy.deepcopy(_response_schemas().get(name))
        if row_schema is None:
            # A newly added upstream tool remains callable before its detailed
            # row contract has been reviewed and added to the catalog.
            row_schema = {
                "type": "object",
                "description": "Normalized row; a detailed schema is not yet available for this tool.",
                "required": ["payload"],
                "additionalProperties": True,
                "properties": {
                    "payload": {"type": "string", "description": "JSON-encoded source record."},
                },
            }
        response_schema = {
            "type": "object",
            "required": ["ok", "tool", "result", "rows"],
            "properties": {
                "ok": {"type": "boolean"},
                "tool": {"type": "string"},
                "result": {"description": "Tool result before row normalization; its shape varies by operation."},
                "rows": {"type": "array", "items": row_schema},
            },
        }
        response = {
            "200": {
                "description": "MCP tool result",
                "content": {"application/json": {"schema": response_schema}},
            }
        }
        paths[path] = {
            "get": {
                "operationId": _operation_id(name),
                "summary": tool.get("description") or name,
                "parameters": [
                    {
                        "name": key,
                        "in": "query",
                        "required": key in (schema.get("required") or []),
                        "schema": prop if isinstance(prop, dict) else {"type": "string"},
                    }
                    for key, prop in (schema.get("properties") or {}).items()
                ],
                "responses": response,
            },
            "post": {
                "operationId": _operation_id(name) + "_post",
                "summary": tool.get("description") or name,
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": schema}},
                },
                "responses": response,
            },
        }
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "tcinvest REST",
            "version": "0.1.0",
            "description": "REST facade over the tcinvest MCP server.",
        },
        "servers": [{"url": f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearer": {"type": "http", "scheme": "bearer"},
            }
        },
        "security": [{"bearer": []}],
    }


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def serve_in_thread(config: RestConfig, backend: McpBackend) -> RestHandle:

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def _send(self, status: int, payload: Any, extra_headers: list[tuple[str, str]] | None = None) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for key, value in extra_headers or []:
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _incoming_bearer(self) -> str | None:
            header = self.headers.get("Authorization", "")
            prefix = "Bearer "
            if not header.startswith(prefix):
                return None
            token = header[len(prefix) :].strip()
            return token or None

        def _authorized(self, incoming: str | None) -> bool:
            if not config.bearer:
                return True
            return incoming == config.bearer

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            value = json.loads(raw.decode("utf-8"))
            if value is None:
                return {}
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            incoming = self._incoming_bearer()
            if not self._authorized(incoming):
                self._send(
                    401,
                    {"error": "missing or invalid bearer"},
                    extra_headers=[("WWW-Authenticate", "Bearer")],
                )
                return
            mcp_token = incoming if incoming and incoming != config.bearer else None
            if mcp_token and hasattr(backend, "set_access_token"):
                backend.set_access_token(mcp_token)
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            try:
                if path == "/health" and method == "GET":
                    self._send(200, {"ok": True, "server": "tcinvest-rest"})
                    return
                if path == "/tools" and method == "GET":
                    tools = backend.list_tools()
                    self._send(
                        200,
                        {
                            "tools": [
                                {
                                    "name": tool["name"],
                                    "description": tool.get("description", ""),
                                    "path": f"/tools/{tool['name']}",
                                    "inputSchema": tool.get("inputSchema") or {},
                                }
                                for tool in tools
                            ]
                        },
                    )
                    return
                if path == "/openapi.json" and method == "GET":
                    self._send(200, build_openapi(backend.list_tools()))
                    return
                if path.startswith("/tools/"):
                    requested = path[len("/tools/") :]
                    tools = backend.list_tools()
                    resolved = resolve_tool_name(requested, tools)
                    if resolved is None:
                        self._send(404, {"error": "unknown tool"})
                        return
                    tool = next(item for item in tools if item["name"] == resolved)
                    if method == "GET":
                        arguments = arguments_from_query(parsed.query, tool)
                    else:
                        arguments = self._read_json()
                    result = backend.call_tool(resolved, arguments)
                    self._send(
                        200,
                        {
                            "ok": True,
                            "tool": resolved,
                            "result": result,
                            "rows": normalize_rows(result),
                        },
                    )
                    return
                self._send(404, {"error": "not found"})
            except json.JSONDecodeError:
                self._send(400, {"error": "invalid JSON body"})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except KeyError:
                self._send(404, {"error": "unknown tool"})
            except Exception as exc:  # noqa: BLE001 — surface upstream MCP failures
                self._send(502, {"error": "mcp_call_failed", "detail": str(exc)})

    httpd = ThreadingHTTPServer((config.host, config.port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    return RestHandle(port=int(port), _httpd=httpd, _thread=thread)


def load_mcp_remote_tokens(
    auth_dir: Path | None = None, *, mcp_url: str = DEFAULT_MCP_URL
) -> dict[str, Any]:
    config_dir = os.environ.get("MCP_REMOTE_CONFIG_DIR")
    root = auth_dir or (
        (Path(config_dir) if config_dir else Path.home() / ".mcp-auth") / "mcp-remote-v1"
    )
    # mcp-remote 0.14.3/0.8.7 getServerUrlHash, with no custom headers/resource/params.
    # This is a cache filename, not a cryptographic authentication check.
    server_hash = hashlib.md5(mcp_url.encode(), usedforsecurity=False).hexdigest()
    path = root / f"{server_hash}_tokens.json"
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise FileNotFoundError("No OAuth session for this MCP server. Run with --bootstrap to sign in.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid cached access token. Run with --bootstrap to sign in.") from exc
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise ValueError("Missing or invalid cached access token. Run with --bootstrap to sign in.")
    return payload


def bootstrap_oauth(
    mcp_url: str, *, auth_port: int, auth_timeout: int,
    mcp_remote_version: str = DEFAULT_MCP_REMOTE_VERSION,
) -> str:
    print(
        f"[1/3] Authorizing TCInvest with mcp-remote {mcp_remote_version}. Complete browser login if prompted; "
        f"waiting up to {auth_timeout}s for callback on port {auth_port}.",
        flush=True,
    )
    try:
        subprocess.run(
            [
                "npx", "--yes", f"--package=mcp-remote@{mcp_remote_version}", "mcp-remote-client",
                mcp_url, str(auth_port), "--auth-timeout", str(auth_timeout),
            ],
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("OAuth bootstrap requires Node.js/npx on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"OAuth bootstrap exited with status {exc.returncode}; REST was not started.") from exc
    return load_mcp_remote_tokens(mcp_url=mcp_url)["access_token"]


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _parse_sse_json(body: str) -> Any:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("data:"):
            data = stripped[5:].strip()
            if data and data != "[DONE]":
                return json.loads(data)
    raise ValueError("empty SSE payload")


class HttpMcpBackend:
    """JSON-RPC client for a Streamable HTTP MCP server (tcinvest)."""

    def __init__(
        self,
        url: str = DEFAULT_MCP_URL,
        access_token: str | None = None,
        timeout_sec: float = 60.0,
    ) -> None:
        self.url = url
        self.access_token = access_token or os.environ.get("TCINVEST_MCP_TOKEN")
        if not self.access_token:
            tokens = load_mcp_remote_tokens(mcp_url=self.url)
            self.access_token = str(tokens["access_token"])
        self.timeout_sec = timeout_sec
        # Keep trust roots in memory if uv's temporary certifi bundle is removed.
        self._ssl_context = ssl_context()
        self._id = 0
        self._session_id: str | None = None
        self._initialized = False
        self._lock = threading.Lock()

    def set_access_token(self, token: str) -> None:
        with self._lock:
            if token and token != self.access_token:
                self.access_token = token
                self._initialized = False
                self._session_id = None

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": USER_AGENT,
            "MCP-Protocol-Version": "2025-11-25",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _rpc(self, method: str, params: dict[str, Any] | None = None, *, notify: bool = False) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = self._next_id()
        if params is not None:
            payload["params"] = params
        request = urllib.request.Request(
            self.url,
            data=_json_bytes(payload),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_sec, context=self._ssl_context
            ) as response:
                session = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
                if session:
                    self._session_id = session
                raw = response.read().decode("utf-8")
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"MCP HTTP {exc.code}: {detail[:500]}") from exc
        if not raw.strip():
            return None
        if "text/event-stream" in content_type:
            message = _parse_sse_json(raw)
        else:
            message = json.loads(raw)
        if notify:
            return None
        if "error" in message:
            error = message["error"]
            raise RuntimeError(f"MCP {method} error: {error}")
        return message.get("result")

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "tcinvest-rest", "version": "0.1.0"},
            },
        )
        self._rpc("notifications/initialized", {}, notify=True)
        self._initialized = True

    def list_tools(self) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_initialized()
            result = self._rpc("tools/list", {})
        return list((result or {}).get("tools") or [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        with self._lock:
            self._ensure_initialized()
            result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        return unwrap_mcp_result(result)


def unwrap_mcp_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return result
    texts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(str(item.get("text") or ""))
    if len(texts) == 1:
        text = texts[0]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    if texts:
        return texts
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expose tcinvest MCP tools as localhost REST.")
    parser.add_argument("--host", default=os.environ.get("TCINVEST_REST_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TCINVEST_REST_PORT", DEFAULT_PORT)))
    parser.add_argument("--mcp-url", default=os.environ.get("TCINVEST_MCP_URL", DEFAULT_MCP_URL))
    parser.add_argument("--bootstrap", action="store_true", help="authorize with mcp-remote, verify access, then serve")
    parser.add_argument(
        "--mcp-remote-version", choices=MCP_REMOTE_VERSIONS, default=DEFAULT_MCP_REMOTE_VERSION,
        help="bootstrap client version (default: 0.14.3; 0.8.7 is the TCBS compatibility option without issuer-equality validation)",
    )
    parser.add_argument("--auth-port", type=int, default=3334, help="OAuth callback port (default: 3334)")
    parser.add_argument("--auth-timeout", type=int, default=600, help="OAuth callback timeout in seconds (default: 600)")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("bind is restricted to loopback (127.0.0.1 / localhost / ::1)")
    if not 1 <= args.auth_port <= 65535:
        parser.error("--auth-port must be between 1 and 65535")
    if args.auth_timeout <= 0:
        parser.error("--auth-timeout must be positive")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    try:
        if args.bootstrap:
            token = bootstrap_oauth(
                args.mcp_url, auth_port=args.auth_port, auth_timeout=args.auth_timeout,
                mcp_remote_version=args.mcp_remote_version,
            )
            backend = HttpMcpBackend(url=args.mcp_url, access_token=token)
            print("[2/3] Verifying TCInvest access...", flush=True)
            tools = backend.list_tools()
            if not tools:
                raise RuntimeError(
                    "TCInvest returned no tools; access is not ready. "
                    "Use a fresh MCP_REMOTE_CONFIG_DIR with --bootstrap to sign in again."
                )
            print(f"[2/3] Access verified: {len(tools)} tools available.", flush=True)
        else:
            backend = HttpMcpBackend(url=args.mcp_url)
        handle = serve_in_thread(RestConfig(host=args.host, port=args.port, bearer=""), backend)
    except KeyboardInterrupt:
        print("Startup cancelled; REST was not started.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1
    if args.bootstrap:
        print(f"[3/3] REST ready at http://{args.host}:{handle.port}", flush=True)
    print(
        json.dumps(
            {
                "ok": True,
                "listen": f"http://{args.host}:{handle.port}",
                "docs": f"http://{args.host}:{handle.port}/openapi.json",
                "tools": f"http://{args.host}:{handle.port}/tools",
            }
        ),
        flush=True,
    )
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        handle.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
