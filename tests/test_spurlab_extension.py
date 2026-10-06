"""Opt-in integration test with the spur_rest extension installed by SpurLab.

Runs against a local fixture server; no TCBS credentials or market data needed.
"""

import ctypes
import os
import tomllib
from pathlib import Path

import pytest

from tcinvest_rest import FakeMcpBackend, RestConfig, serve_in_thread


@pytest.mark.skipif(
    not os.environ.get("TCINVEST_TEST_EXTENSION"),
    reason="set TCINVEST_TEST_EXTENSION to a matching spur_rest.duckdb_extension",
)
def test_all_functions_register_and_sql_reaches_rest(tmp_path, monkeypatch):
    import _duckdb
    import duckdb

    host_symbols = ctypes.CDLL(_duckdb.__file__, mode=ctypes.RTLD_GLOBAL)
    assert host_symbols
    tool = {
        "name": "tcinvest-getTickerOverview",
        "description": "Fixture overview",
        "inputSchema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
    }
    backend = FakeMcpBackend(
        tools=[tool],
        results={tool["name"]: {"ticker": "TCB", "exchange": "HOSE"}},
    )
    server = serve_in_thread(RestConfig(port=0), backend)
    try:
        root = Path(__file__).resolve().parents[1]
        manifest_text = (root / "tcinvest.connection.toml").read_text()
        manifest = tomllib.loads(manifest_text)
        (tmp_path / "tcinvest.connection.toml").write_text(
            manifest_text.replace("http://127.0.0.1:8788", f"http://127.0.0.1:{server.port}")
        )
        monkeypatch.setenv("SPUR_REST_MANIFEST_DIR", str(tmp_path))
        monkeypatch.delenv("SPUR_REST_MANIFEST", raising=False)
        extension = str(Path(os.environ["TCINVEST_TEST_EXTENSION"]).expanduser().resolve()).replace("'", "''")
        with duckdb.connect(config={"allow_unsigned_extensions": "true"}) as con:
            con.execute(f"LOAD '{extension}'")
            functions = con.execute(
                "SELECT function_name FROM duckdb_functions() "
                "WHERE function_type = 'table' AND starts_with(function_name, 'tcinvest_')"
            ).fetchall()
            expected = {"tcinvest_" + table["name"] for table in manifest["table"]}
            assert {row[0] for row in functions} == expected
            assert con.execute("SELECT name FROM tcinvest_list_tools()").fetchall() == [(tool["name"],)]
            assert con.execute(
                "SELECT ticker, exchange FROM tcinvest_get_ticker_overview(ticker := 'TCB')"
            ).fetchall() == [("TCB", "HOSE")]
            assert (tool["name"], {"ticker": "TCB"}) in backend.calls
    finally:
        server.shutdown()
