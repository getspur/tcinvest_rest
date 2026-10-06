"""Keep the distributable SQL manifest aligned with the MCP/OpenAPI contract."""

import json
import tomllib
from pathlib import Path

import pytest

from tcinvest_rest import _operation_id, build_openapi

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "tcinvest.openapi.yaml").read_text())
MANIFEST = tomllib.loads((ROOT / "tcinvest.connection.toml").read_text())
TABLES = MANIFEST["table"]


def test_every_read_operation_has_exactly_one_table_function():
    operations = [
        item["get"]["operationId"]
        for item in SPEC["paths"].values()
        if "operationId" in item.get("get", {})
    ]
    names = [table["name"] for table in TABLES]
    assert len(names) == len(set(names))
    assert len(operations) == len(set(operations))
    assert set(names) == set(operations)
    assert "get_ticker_overview" in names
    assert "list_tools" in names


@pytest.mark.parametrize("table", TABLES, ids=lambda table: table["name"])
def test_table_path_arguments_and_response_match_openapi(table):
    operation = SPEC["paths"][table["path"]]["get"]
    assert table["name"] == operation["operationId"]
    params = {p["name"]: p for p in operation.get("parameters", [])}
    filters = table.get("filters", {})
    assert set(filters) == set(params)
    for name, config in filters.items():
        assert config["param"] == name
        assert config.get("required", False) == params[name].get("required", False)
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    row_key = table["response_path"].removeprefix("$.")
    assert response["properties"][row_key]["type"] == "array"
    assert table["columns"]
    if table["name"] != "list_tools":
        assert "payload" in table["columns"]
        assert table["name"] == _operation_id(table["path"].split("/")[-1])


def test_snapshot_matches_runtime_openapi_generation():
    tools = []
    for path, item in SPEC["paths"].items():
        if path.startswith("/tools/"):
            tools.append({
                "name": path.split("/")[-1],
                "description": item["get"].get("summary", ""),
                "inputSchema": item["post"]["requestBody"]["content"]["application/json"]["schema"],
            })
    generated = build_openapi(tools)
    assert set(generated["paths"]) == set(SPEC["paths"])
    for path in generated["paths"]:
        for method, operation in generated["paths"][path].items():
            assert operation.get("operationId") == SPEC["paths"][path][method].get("operationId")
            assert operation.get("parameters") == SPEC["paths"][path][method].get("parameters")


def test_public_manifest_uses_local_oauth_session():
    assert MANIFEST["source"] == {
        "name": "tcinvest",
        "base_url": "http://127.0.0.1:8788",
        "auth": {"scheme": "none"},
    }
    assert not MANIFEST.get("action")
