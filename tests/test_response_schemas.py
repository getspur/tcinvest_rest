"""OpenAPI describes the JSON rows clients actually receive, not SQL strings."""

import json
import tomllib
from pathlib import Path
from urllib.request import urlopen

import pytest
from jsonschema.exceptions import ValidationError
from openapi_schema_validator import OAS30Validator

from tcinvest_rest import FakeMcpBackend, RestConfig, build_openapi, serve_in_thread

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = json.loads((ROOT / "tcinvest.openapi.yaml").read_text())
TABLES = tomllib.loads((ROOT / "tcinvest.connection.toml").read_text())["table"]
TOOLS = [
    {
        "name": path.rsplit("/", 1)[-1],
        "description": item["get"]["summary"],
        "inputSchema": item["post"]["requestBody"]["content"]["application/json"]["schema"],
    }
    for path, item in SNAPSHOT["paths"].items()
    if path.startswith("/tools/")
]
FIXTURES = json.loads((ROOT / "tests/fixtures/response_rows.json").read_text())


def response_schema(spec, path, method="get"):
    return spec["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]


@pytest.mark.parametrize("table", TABLES, ids=lambda table: table["name"])
def test_runtime_schema_describes_every_projected_json_field(table):
    schema = response_schema(build_openapi(TOOLS), table["path"])
    rows = schema["properties"][table["response_path"].removeprefix("$.")]["items"]
    for column in table["columns"].values():
        field = rows
        for part in column["json"].removeprefix("$.").split("."):
            assert part in field.get("properties", {}), column["json"]
            field = field["properties"][part]


def test_response_schema_has_numeric_nullable_and_nested_types():
    spec = build_openapi(TOOLS)
    ratio = response_schema(spec, "/tools/tcinvest-getStockRatio")["properties"]["rows"]["items"]
    assert ratio["properties"]["priceToEarning"]["type"] == "number"
    assert ratio["properties"]["priceToEarning"]["nullable"] is True
    assert ratio["properties"]["payload"]["type"] == "string"
    gauge = response_schema(spec, "/tools/tcinvest-getGaugeChart")["properties"]["rows"]["items"]
    assert gauge["properties"]["summary"]["type"] == "object"
    assert "buy" in gauge["properties"]["summary"]["properties"]
    volatility = response_schema(spec, "/tools/tcinvest-getMarketVolatility")["properties"]["rows"]["items"]
    assert volatility["properties"]["data"]["type"] == "array"
    assert volatility["properties"]["data"]["items"]["type"] == "object"


@pytest.mark.parametrize("ticker,ratio", [("TCB", 12.5), ("FPT", None)])
def test_http_rows_validate_and_incorrect_numeric_type_is_rejected(ticker, ratio):
    tool = next(tool for tool in TOOLS if tool["name"] == "tcinvest-getStockRatio")
    raw = {"ticker": ticker, "priceToEarning": ratio, "roe": None, "capitalize": 1000}
    backend = FakeMcpBackend(tools=[tool], results={tool["name"]: raw})
    server = serve_in_thread(RestConfig(port=0), backend)
    try:
        base = f"http://127.0.0.1:{server.port}"
        with urlopen(base + "/openapi.json") as response:
            spec = json.load(response)
        with urlopen(base + f"/tools/getStockRatio?ticker={ticker}") as response:
            body = json.load(response)
        schema = response_schema(spec, "/tools/tcinvest-getStockRatio")
        assert set(body) <= schema["properties"].keys()
        validator = OAS30Validator(schema)
        validator.validate(body)
        assert body["result"] == raw
        assert json.loads(body["rows"][0]["payload"]) == raw
        body["rows"][0]["priceToEarning"] = "not a number"
        with pytest.raises(ValidationError):
            validator.validate(body)
        body["rows"] = []
        validator.validate(body)
        body["rows"] = [{"payload": "{}"}]
        validator.validate(body)
    finally:
        server.shutdown()


def test_catalog_examples_and_post_schemas_match():
    spec = build_openapi(TOOLS)
    for tool in TOOLS:
        path = "/tools/" + tool["name"]
        schema = response_schema(spec, path)
        assert schema == response_schema(spec, path, "post")
        row = schema["properties"]["rows"]["items"]
        assert row.get("description")
        assert "example" in row
        OAS30Validator.check_schema(row)
        OAS30Validator(row).validate(row["example"])
        for field in row["properties"].values():
            assert field.get("description")


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fixture: fixture["tool"])
def test_observed_response_shapes_match_declared_types(fixture):
    spec = build_openapi(TOOLS)
    row = response_schema(spec, "/tools/" + fixture["tool"])["properties"]["rows"]["items"]
    assert fixture["row"].keys() <= row["properties"].keys()
    OAS30Validator(row).validate(fixture["row"])


def test_openapi_snapshot_matches_all_runtime_response_definitions():
    assert build_openapi(TOOLS) == SNAPSHOT


def test_new_upstream_tools_keep_generic_payload_fallback():
    spec = build_openapi([{"name": "tcinvest-futureTool", "inputSchema": {"type": "object"}}])
    row = response_schema(spec, "/tools/tcinvest-futureTool")["properties"]["rows"]["items"]
    assert row["properties"]["payload"]["type"] == "string"
    OAS30Validator(row).validate({"payload": '{"newField":1}', "newField": 1})


def test_openapi_definitions_are_independent_of_cwd_and_callers(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    first = build_openapi(TOOLS)
    first_row = response_schema(first, "/tools/tcinvest-getStockRatio")["properties"]["rows"]["items"]
    first_row["properties"]["priceToEarning"] = {"type": "boolean"}
    second = build_openapi(TOOLS)
    row = response_schema(second, "/tools/tcinvest-getStockRatio")["properties"]["rows"]["items"]
    assert row["properties"]["priceToEarning"]["type"] == "number"
