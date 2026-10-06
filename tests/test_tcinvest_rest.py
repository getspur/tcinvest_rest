"""REST facade over the tcinvest MCP server."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import ssl
import subprocess
import sys
import threading
import urllib.error
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tcinvest_rest as rest

from tcinvest_rest import (
    FakeMcpBackend,
    RestConfig,
    _parse_sse_json,
    normalize_rows,
    serve_in_thread,
)


TICKER_TOOL = {
    "name": "tcinvest-getTickerOverview",
    "description": "Ticker overview",
    "inputSchema": {
        "type": "object",
        "properties": {"ticker": {"type": "string"}},
        "required": ["ticker"],
    },
}


@pytest.fixture
def backend() -> FakeMcpBackend:
    return FakeMcpBackend(
        tools=[TICKER_TOOL],
        results={
            "tcinvest-getTickerOverview": {
                "ticker": "TCB",
                "exchange": "HOSE",
            }
        },
    )


@pytest.fixture
def server(backend: FakeMcpBackend):
    config = RestConfig(host="127.0.0.1", port=0, bearer="test-token")
    handle = serve_in_thread(config, backend)
    try:
        yield handle
    finally:
        handle.shutdown()


def _request(
    server,
    method: str,
    path: str,
    *,
    token: str | None = "test-token",
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | list[Any] | str]:
    conn = HTTPConnection("127.0.0.1", server.port, timeout=5)
    headers = {"Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    raw = response.read().decode("utf-8")
    conn.close()
    try:
        parsed: dict[str, Any] | list[Any] | str = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw
    return response.status, parsed


def test_health_rejects_missing_bearer(server) -> None:
    status, body = _request(server, "GET", "/health", token=None)
    assert status == 401
    assert body["error"] == "missing or invalid bearer"


def test_oauth_loopback_does_not_need_rest_token(backend: FakeMcpBackend) -> None:
    handle = serve_in_thread(RestConfig(host="127.0.0.1", port=0, bearer=""), backend)
    try:
        status, body = _request(handle, "GET", "/health", token="minted-tcbs-access")
        assert status == 200
        assert body["ok"] is True
        status, body = _request(handle, "GET", "/health", token=None)
        assert status == 200
    finally:
        handle.shutdown()


def test_health_ok_with_bearer(server) -> None:
    status, body = _request(server, "GET", "/health")
    assert status == 200
    assert body == {"ok": True, "server": "tcinvest-rest"}


def test_list_tools(server) -> None:
    status, body = _request(server, "GET", "/tools")
    assert status == 200
    assert body["tools"][0]["name"] == "tcinvest-getTickerOverview"
    assert body["tools"][0]["path"] == "/tools/tcinvest-getTickerOverview"


def test_post_tool_forwards_json_body(server, backend: FakeMcpBackend) -> None:
    status, body = _request(
        server,
        "POST",
        "/tools/tcinvest-getTickerOverview",
        body={"ticker": "TCB"},
    )
    assert status == 200
    assert body["ok"] is True
    assert body["result"]["ticker"] == "TCB"
    assert body["rows"][0]["ticker"] == "TCB"
    assert json.loads(body["rows"][0]["payload"])["exchange"] == "HOSE"
    assert backend.calls == [
        ("tcinvest-getTickerOverview", {"ticker": "TCB"}),
    ]


def test_get_tool_accepts_short_name_and_query(server, backend: FakeMcpBackend) -> None:
    status, body = _request(server, "GET", "/tools/getTickerOverview?ticker=TCB")
    assert status == 200
    assert body["result"]["exchange"] == "HOSE"
    assert backend.calls[0][0] == "tcinvest-getTickerOverview"


def test_unknown_tool_is_404(server) -> None:
    status, body = _request(server, "POST", "/tools/nope", body={})
    assert status == 404
    assert body["error"] == "unknown tool"


def test_openapi_includes_tool_path(server) -> None:
    status, body = _request(server, "GET", "/openapi.json")
    assert status == 200
    assert "/tools/tcinvest-getTickerOverview" in body["paths"]
    post = body["paths"]["/tools/tcinvest-getTickerOverview"]["post"]
    assert post["requestBody"]["content"]["application/json"]["schema"]["required"] == [
        "ticker"
    ]


def test_wrong_bearer_is_401(server) -> None:
    status, body = _request(server, "GET", "/tools", token="nope")
    assert status == 401
    assert body["error"] == "missing or invalid bearer"


def test_parse_sse_crlf_message() -> None:
    body = (
        'event: message\r\n'
        'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\r\n\r\n'
    )
    assert _parse_sse_json(body)["result"]["ok"] is True


def test_normalize_rows_explodes_activity_news() -> None:
    rows = normalize_rows(
        {
            "listActivityNews": [
                {"id": 1, "title": "A", "price": 100},
                {"id": 2, "title": "B", "price": 200},
            ]
        }
    )
    assert [row["title"] for row in rows] == ["A", "B"]
    assert rows[0]["price"] == 100
    assert json.loads(rows[0]["payload"])["id"] == 1


def test_normalize_rows_explodes_result_and_carries_parent() -> None:
    rows = normalize_rows(
        {
            "ex": "ALL",
            "ind": "8300",
            "b": [{"t": "01/01/2026", "a": 10, "d": 5, "s": 1}],
        }
    )
    assert len(rows) == 1
    assert rows[0]["ex"] == "ALL"
    assert rows[0]["ind"] == "8300"
    assert rows[0]["a"] == 10


def test_normalize_rows_keeps_two_record_lists_as_one_row() -> None:
    rows = normalize_rows(
        {
            "topInc": 14.03,
            "listInc": [{"ticker": "LPB"}],
            "listDesc": [{"ticker": "VCB"}],
        }
    )
    assert len(rows) == 1
    assert rows[0]["topInc"] == 14.03
    assert rows[0]["listInc"][0]["ticker"] == "LPB"


def _write_tokens(root: Path, url: str, access_token: Any) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(url.encode()).hexdigest()
    path = root / f"{key}_tokens.json"
    path.write_text(json.dumps({"access_token": access_token}))
    return path


def test_cached_token_belongs_to_tcinvest_not_newest_provider(tmp_path) -> None:
    own = _write_tokens(tmp_path, rest.DEFAULT_MCP_URL, "tcinvest-fixture")
    other = _write_tokens(tmp_path, "https://other.example/mcp", "other-fixture")
    os.utime(own, (1, 1))
    os.utime(other, (2, 2))
    assert rest.load_mcp_remote_tokens(tmp_path)["access_token"] == "tcinvest-fixture"


def test_missing_tcinvest_cache_does_not_use_another_provider(tmp_path) -> None:
    _write_tokens(tmp_path, "https://other.example/mcp", "other-fixture")
    with pytest.raises(FileNotFoundError):
        rest.load_mcp_remote_tokens(tmp_path)


@pytest.mark.parametrize("token", ["", None, 42])
def test_cached_token_must_be_nonempty_text(tmp_path, token) -> None:
    _write_tokens(tmp_path, rest.DEFAULT_MCP_URL, token)
    with pytest.raises(ValueError, match="access token"):
        rest.load_mcp_remote_tokens(tmp_path)


def test_token_cache_honors_mcp_remote_config_and_server_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MCP_REMOTE_CONFIG_DIR", str(tmp_path))
    url = "https://custom.example/mcp"
    _write_tokens(tmp_path / "mcp-remote-v1", url, "custom-fixture")
    assert rest.load_mcp_remote_tokens(mcp_url=url)["access_token"] == "custom-fixture"


@pytest.mark.parametrize("suffix", ["", "/"])
def test_token_cache_preserves_exact_url_identity(tmp_path, suffix) -> None:
    base = "https://mcp.tcbs.com.vn/mcp/tcinvest"
    _write_tokens(tmp_path, base, "without-slash-fixture")
    _write_tokens(tmp_path, base + "/", "with-slash-fixture")
    expected = "with-slash-fixture" if suffix else "without-slash-fixture"
    assert rest.load_mcp_remote_tokens(tmp_path, mcp_url=base + suffix)["access_token"] == expected


def test_http_backend_preserves_documented_url_and_its_cache(tmp_path, monkeypatch) -> None:
    url = "https://mcp.tcbs.com.vn/mcp/tcinvest/"
    monkeypatch.setenv("MCP_REMOTE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("TCINVEST_MCP_TOKEN", raising=False)
    _write_tokens(tmp_path / "mcp-remote-v1", url, "with-slash-fixture")
    _write_tokens(tmp_path / "mcp-remote-v1", url.rstrip("/"), "without-slash-fixture")
    backend = rest.HttpMcpBackend(url=url)
    assert backend.url == url
    assert backend.access_token == "with-slash-fixture"


@pytest.fixture
def https_mcp(tmp_path, monkeypatch):
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("local HTTPS regression tests require openssl")
    cert = tmp_path / "server.pem"
    key = tmp_path / "server.key"
    subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=fixture.invalid", "-addext", "subjectAltName=IP:127.0.0.1",
         "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )
    ca_bundle = tmp_path / "ephemeral-certifi.pem"
    shutil.copyfile(cert, ca_bundle)
    monkeypatch.setitem(sys.modules, "certifi", SimpleNamespace(where=lambda: str(ca_bundle)))
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(message)
            if message["method"] == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            results = {
                "initialize": {"protocolVersion": "2025-11-25", "capabilities": {}},
                "tools/list": {"tools": [TICKER_TOOL]},
                "tools/call": {"ticker": "TCB", "exchange": "HOSE"},
            }
            body = json.dumps({"jsonrpc": "2.0", "id": message["id"],
                               "result": results[message["method"]]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    upstream.socket = context.wrap_socket(upstream.socket, server_side=True)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{upstream.server_port}/mcp", ca_bundle, calls
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join()


@pytest.mark.parametrize("initialized", [False, True])
def test_http_backend_survives_ca_bundle_removal(https_mcp, initialized) -> None:
    url, ca_bundle, calls = https_mcp
    backend = rest.HttpMcpBackend(url=url, access_token="test-token")
    if initialized:
        assert backend.list_tools() == [TICKER_TOOL]
    # Reproduce a uv cache cleanup after backend startup, including after bootstrap.
    ca_bundle.unlink()
    handle = serve_in_thread(RestConfig(host="127.0.0.1", port=0, bearer="test-token"), backend)
    try:
        status, tools = _request(handle, "GET", "/tools")
        assert status == 200, tools
        assert [tool["name"] for tool in tools["tools"]] == [TICKER_TOOL["name"]]
        status, rows = _request(handle, "GET", "/tools/getTickerOverview?ticker=TCB")
        assert status == 200, rows
        assert rows["rows"][0]["ticker"] == "TCB"
        assert calls[-1]["params"] == {
            "name": TICKER_TOOL["name"], "arguments": {"ticker": "TCB"},
        }
    finally:
        handle.shutdown()


@pytest.mark.parametrize("failure", ["untrusted", "hostname"])
def test_http_backend_still_verifies_tls(https_mcp, monkeypatch, failure) -> None:
    url, ca_bundle, calls = https_mcp
    if failure == "untrusted":
        monkeypatch.setattr(rest, "ssl_context", ssl.create_default_context)
    else:
        url = url.replace("127.0.0.1", "localhost")
    backend = rest.HttpMcpBackend(url=url, access_token="test-token")
    with pytest.raises(urllib.error.URLError) as exc:
        backend.list_tools()
    assert isinstance(exc.value.reason, ssl.SSLCertVerificationError)
    assert calls == []


def _bootstrap_fakes(tmp_path, monkeypatch, expected_url=None, expected_version="0.14.3"):
    expected_url = expected_url or rest.DEFAULT_MCP_URL
    events = []
    monkeypatch.setenv("MCP_REMOTE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("TCINVEST_MCP_TOKEN", "old-env-fixture")

    def authorize(argv, **kwargs):
        assert argv == [
            "npx", "--yes", f"--package=mcp-remote@{expected_version}", "mcp-remote-client",
            expected_url, "3334", "--auth-timeout", "600",
        ]
        assert kwargs["check"] is True
        events.append("oauth")
        _write_tokens(tmp_path / "mcp-remote-v1", expected_url, "new-fixture")
        return subprocess.CompletedProcess(argv, 0)

    class Backend:
        def __init__(self, *, url, access_token=None):
            assert url == expected_url
            assert access_token == "new-fixture"
            events.append("backend")

        def list_tools(self):
            events.append("verify")
            return [TICKER_TOOL]

    class Handle:
        port = 8788

        def shutdown(self):
            events.append("shutdown")

    def serve(config, backend):
        events.append("listen")
        return Handle()

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "run", authorize)
    monkeypatch.setattr(rest, "HttpMcpBackend", Backend)
    monkeypatch.setattr(rest, "serve_in_thread", serve)
    monkeypatch.setattr(rest.time, "sleep", interrupt)
    return events, Backend


def test_bootstrap_authorizes_verifies_then_listens(tmp_path, monkeypatch, capsys) -> None:
    events, _ = _bootstrap_fakes(tmp_path, monkeypatch)
    assert rest.main(["--bootstrap"]) == 0
    assert events == ["oauth", "backend", "verify", "listen", "shutdown"]
    captured = capsys.readouterr()
    assert "[1/3]" in captured.out and "[2/3]" in captured.out and "[3/3]" in captured.out
    assert "new-fixture" not in captured.out + captured.err


@pytest.mark.parametrize("explicit_url", [None, "https://mcp.tcbs.com.vn/mcp/tcinvest/", "https://mcp.tcbs.com.vn/mcp/tcinvest"])
def test_bootstrap_preserves_documented_or_explicit_url(tmp_path, monkeypatch, explicit_url) -> None:
    monkeypatch.delenv("TCINVEST_MCP_URL", raising=False)
    expected_url = explicit_url or "https://mcp.tcbs.com.vn/mcp/tcinvest/"
    events, _ = _bootstrap_fakes(tmp_path, monkeypatch, expected_url)
    args = ["--bootstrap"]
    if explicit_url is not None:
        args.extend(["--mcp-url", explicit_url])
    assert rest.main(args) == 0
    assert events == ["oauth", "backend", "verify", "listen", "shutdown"]


@pytest.mark.parametrize("version", ["0.14.3", "0.8.7"])
def test_bootstrap_explicit_client_version(tmp_path, monkeypatch, version) -> None:
    events, _ = _bootstrap_fakes(tmp_path, monkeypatch, expected_version=version)
    assert rest.main(["--bootstrap", "--mcp-remote-version", version]) == 0
    assert events == ["oauth", "backend", "verify", "listen", "shutdown"]


@pytest.mark.parametrize("version", [None, "0.8.7"])
@pytest.mark.parametrize("failure", ["auth_exit", "npx_missing", "cache_missing", "verify", "empty_tools"])
def test_bootstrap_failure_never_listens(tmp_path, monkeypatch, capsys, failure, version) -> None:
    events, backend = _bootstrap_fakes(tmp_path, monkeypatch, expected_version=version or "0.14.3")
    if failure == "empty_tools":
        monkeypatch.setattr(backend, "list_tools", lambda self: [])
    elif failure == "verify":
        def fail_verify(self):
            raise RuntimeError("MCP HTTP 401")
        monkeypatch.setattr(backend, "list_tools", fail_verify)
    else:
        def fail_auth(argv, **kwargs):
            if failure == "auth_exit":
                raise subprocess.CalledProcessError(1, argv)
            if failure == "npx_missing":
                raise FileNotFoundError("npx")
            return subprocess.CompletedProcess(argv, 0)
        monkeypatch.setattr(subprocess, "run", fail_auth)
    args = ["--bootstrap"]
    if version is not None:
        args.extend(["--mcp-remote-version", version])
    assert rest.main(args) == 1
    assert "listen" not in events
    assert "\"ok\": true" not in capsys.readouterr().out


def test_normal_start_missing_token_explains_bootstrap(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MCP_REMOTE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("TCINVEST_MCP_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert rest.main([]) == 1
    assert "--bootstrap" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["--auth-port", "0"], ["--auth-port", "65536"], ["--auth-timeout", "0"], ["--mcp-remote-version", "latest"]])
def test_invalid_bootstrap_options_fail_before_oauth(monkeypatch, args) -> None:
    def unexpected(*_args, **_kwargs):
        pytest.fail("invalid CLI options must not start OAuth")
    monkeypatch.setattr(subprocess, "run", unexpected)
    with pytest.raises(SystemExit) as exc:
        rest.main(["--bootstrap", *args])
    assert exc.value.code == 2
