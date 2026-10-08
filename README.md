# TCInvest REST for SpurLab

Run TCInvest locally with `uv`, sign in through your browser, and query it from
SpurLab SQL cells. This repository contains a standalone Python REST bridge and
**55 DuckDB table functions**: 54 TCInvest operations plus the tool catalog.
You do not need the SpurLab source repository or a local build.

```text
SpurLab SQL → bundled spur_rest extension → localhost:8788 → TCInvest MCP
                                                               ↑
                                                     your TCBS OAuth login
```

Each user needs their own TCBS account with access to the TCInvest MCP service.
This project shares the bridge and table definitions, not an account or data
subscription. The REST bridge and the SpurLab kernel must run on the same machine.

## 1. Get the prerequisites

The installation guide targets macOS and Linux. Native Windows bootstrap has
not been qualified for this release.

- Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Install [Node.js](https://nodejs.org/en/download) with `npx` for browser login
  (`mcp-remote` requires Node.js 18 or later; use a supported Node.js release).
- Install Git to clone this repository, or download its ZIP and extract it.
- Install SpurLab with its bundled `spur_rest` DuckDB extension for SQL access.
  The HTTP bridge also works on its own.

```sh
uv --version
node --version
npx --version
git clone https://github.com/getspur/tcinvest_rest.git
cd tcinvest_rest
```

The repository selects Python 3.12 through `.python-version`; `uv` can download
it automatically. There is no project environment to create or activate.
See [uv's script guide](https://docs.astral.sh/uv/guides/scripts/) for how
`--no-project` and `--with` work.

## 2. Sign in and start REST

From the cloned repository on macOS or Linux:

```sh
MCP_REMOTE_CONFIG_DIR="$HOME/.mcp-auth/tcinvest-compat-0.8.7" \
  uv run --no-project --with certifi python tcinvest_rest.py \
  --bootstrap --mcp-remote-version 0.8.7
```

Complete the TCBS login and consent in the browser. The launcher waits for the
OAuth callback on port **3334**, verifies access with `tools/list`, then starts
REST at **http://127.0.0.1:8788**. Keep this terminal running while using SpurLab;
press Ctrl+C to stop it.

The command is the standalone equivalent of running
`app_gallery/audience-radar/tcinvest_rest.py` in the SpurLab source repository.

**Why version 0.8.7?** It is the explicit TCBS compatibility option for the
issuer-metadata mismatch found during integration. It retains PKCE but does
not enforce metadata issuer equality. The script's default remains 0.14.3;
omit `--mcp-remote-version 0.8.7` to use that version when the service supports it.
Keep the compatibility session in its own `MCP_REMOTE_CONFIG_DIR` as shown.
See the [mcp-remote project](https://github.com/punkpeye/mcp-remote) for its client
and OAuth behavior.

Check the running bridge from a second terminal:

```sh
curl --fail http://127.0.0.1:8788/health
curl --fail http://127.0.0.1:8788/tools
curl --fail 'http://127.0.0.1:8788/tools/getTickerOverview?ticker=TCB'
```

`/health` checks the local process only. `/tools` checks upstream access.
Tool responses contain `result` and normalized `rows` for SQL consumption.

## 3. Add the tables to SpurLab

In SpurLab's REST/OpenAPI connection importer, use the running server's
`http://127.0.0.1:8788/openapi.json` or the included `tcinvest.openapi.yaml`.
The specification describes each tool's normalized `rows[]` fields, including
numeric columns, nullable values, nested objects, and arrays. `payload` remains
available as JSON text for accessing the source record.

After updating this repository, restart the REST server and re-import the spec
to refresh an existing connection's saved columns. The server and its
`tcinvest.response-schemas.json` catalog must stay in the same directory.

### Optional: install the curated SQL manifest

The TOML manifest provides a curated projection with stable SQL column names.
Use this alternative when you want its explicit projections instead of columns
discovered from OpenAPI.

Copy the included manifest to SpurLab's per-user manifest directory **before
starting a new notebook kernel**:

```sh
mkdir -p "$HOME/.spur/gateway/manifests"
cp -i tcinvest.connection.toml "$HOME/.spur/gateway/manifests/tcinvest.connection.toml"
```

If you already customized a TCInvest manifest, preserve those settings before
replacing it. Restart SpurLab and the notebook kernel after installing or editing
the file. The bundled `spur_rest` extension loads the manifest when the SQL
connection starts; registration is not hot-reloaded.

The manifest uses `auth = { scheme = "none" }` because the localhost bridge
holds your TCBS OAuth session. You do not paste tokens into the manifest.
Installing it registers functions in the SQL runtime; it does not create a
saved connection card in the sidebar.

In a SpurLab **SQL cell**, verify registration:

```sql
SELECT function_name, parameters, parameter_types
FROM duckdb_functions()
WHERE function_type = 'table'
  AND starts_with(function_name, 'tcinvest_')
ORDER BY function_name;
```

Expect 55 function names from the bundled manifest. Then query:

```sql
SELECT * FROM tcinvest_list_tools();

SELECT ticker, exchange
FROM tcinvest_get_ticker_overview(ticker := 'TCB');

SELECT price_to_earning, price_to_book, roe
FROM tcinvest_get_stock_ratio(ticker := 'TCB');
```

Use **named arguments** to send parameters upstream. For example,
`ticker := 'TCB'` belongs inside the function call; a SQL `WHERE ticker = 'TCB'`
is only a local filter. The manifest exposes remote filter arguments as strings;
the REST bridge converts numeric and boolean values using the MCP input schema.
For pagination, use `page := '0', size := '5'`.

See [all functions and required arguments](docs/table-functions.md) and
[copyable SQL examples](examples/queries.sql). Fields are projected as typed
columns. Nested values remain JSON text; `payload` preserves the normalized
record when you need fields outside the declared projection.

## 4. Work through the notebook tutorial

Open [the Vietnamese TCInvest + SQL tutorial](examples/tutorial-tcinvest-sql-phan-tich-chung-khoan.ipynb)
in **SpurLab**. It includes 16 SQL cells with explanations, saved example
results, a sector chart, exercises, and troubleshooting notes. The lessons cover
connection discovery, parameters, temporary tables, data quality, returns,
moving averages, liquidity, market breadth, foreign flows, and 19 industries.

Start the bridge using step 2, then use step 3's **REST/OpenAPI importer** to
create a global connection named `tcinvest` and attach it to the notebook.
The tutorial uses OpenAPI-derived columns such as `tradingdate`, `seqtime`, and
`accvalue`; the optional curated TOML manifest uses different projections.
Run the datasource setup cell generated by SpurLab, then the SQL cells in order.
The shared notebook contains no generated local bootstrap or saved connection.
A standard Jupyter Python kernel cannot execute these SpurLab SQL cells directly.

Saved results illustrate the **2026-10-07** cutoff and are not current quotes.
Change the date or reference index in **SQL 03** and rerun the following cells
using your own TCBS session. Restarting the kernel clears SQL variables and
temporary tables even when saved output remains visible.

## HTTP and OpenAPI

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Local liveness |
| `GET /tools` | Current MCP catalog, descriptions, and input schemas |
| `GET /openapi.json` | OpenAPI generated from the current catalog |
| `GET /tools/{name}?arg=value` | Call a tool with query parameters |
| `POST /tools/{name}` | Call a tool with a JSON argument object |

Both `getTickerOverview` and `tcinvest-getTickerOverview` work as tool names.

```sh
curl --fail http://127.0.0.1:8788/tools/tcinvest-getTickerOverview \
  -H 'Content-Type: application/json' \
  -d '{"ticker":"TCB"}'
```

[`tcinvest.openapi.yaml`](tcinvest.openapi.yaml) is a checked-in OpenAPI snapshot
(JSON syntax, valid YAML). Its operation names and arguments match the manifest.
The live document and checked-in snapshot use the same per-tool definitions in
[`tcinvest.response-schemas.json`](tcinvest.response-schemas.json). Both GET and
POST document the same responses, including `result`, normalized `rows`, and
the compatibility `payload` string. The schemas describe the HTTP JSON types;
an object or array stored as text by the SQL manifest remains an object or
array in OpenAPI.

Fields can be absent or null for different companies or periods. Fields whose
non-null type is not established are explicitly documented as accepting any
JSON value; the catalog does not guess a type from null. Examples are
illustrative shapes, not market quotes. Newly introduced upstream tools use a
generic `payload` schema until their detailed definition is added to the catalog.
The OpenAPI bearer scheme describes the optional upstream-token forwarding path;
the normal localhost workflow uses the cached session without a bearer header.

## Restarting and troubleshooting

- **Expired login / HTTP 401 or 502:** rerun the bootstrap command with the same
  config directory. The Python bridge does not refresh tokens while running.
  For a completely new login, choose a new `MCP_REMOTE_CONFIG_DIR`.
- **No cached session:** run with `--bootstrap`. To start from an existing session,
  keep the same config directory and omit `--bootstrap`; it must still be valid.
- **Issuer validation error:** use the full compatibility command above. An old
  `npx mcp-remote` command may choose a different version.
- **Missing `npx`:** install Node.js and reopen the terminal so it is on `PATH`.
- **Callback port busy:** add `--auth-port 3335` (or another available port).
  `--auth-timeout 600` controls how long browser authorization may take.
- **REST port busy:** stop the other bridge or add `--port 8789`, and change
  `[source].base_url` in your installed manifest to the same port. The generated
  OpenAPI `servers` entry currently advertises the default port 8788.
- **Missing SQL functions:** confirm the manifest is under the home directory
  used by the kernel, then restart SpurLab and its kernel. Check the SQL/kernel
  output for a `spur_rest` load error. `SPUR_REST_MANIFEST_DIR`, if set, overrides
  the normal manifest directory. A saved native API connection named `tcinvest`
  owns that namespace and can suppress this legacy manifest; use a fresh profile
  or rename this manifest's `[source].name` (and the SQL prefix) to avoid a clash.
- **Extension missing or version mismatch:** use SpurLab's bundled extension
  with its matching DuckDB runtime. Installing `duckdb` alone does not install
  `spur_rest`; this repository distributes the bridge and manifest, not binaries.
- **Remote kernel:** `127.0.0.1` refers to the kernel's host. The local instructions
  require a local kernel; the launcher deliberately accepts only loopback binds.
- **Legacy session URL:** cache lookup preserves the trailing slash in
  `https://mcp.tcbs.com.vn/mcp/tcinvest/`. A session created without the slash
  needs the same explicit `--mcp-url https://mcp.tcbs.com.vn/mcp/tcinvest` at startup.

OAuth credentials stay under `MCP_REMOTE_CONFIG_DIR`. Keep that directory private.
The bridge is a single-user localhost service: local clients can use its session.
Do not publish token files or expose the service through a public proxy.

## Development and verification

```sh
uv run --no-project --with certifi --with pytest --with openapi-schema-validator \
  python -m pytest -q
```

The default suite uses local HTTP/TLS fixtures, validates every manifest
operation and response schema, and checks exact snapshot/runtime agreement.
Response fixtures retain observed JSON shapes with all values replaced by
synthetic data. It needs no TCBS account; TLS tests need the `openssl` executable.
CI runs on Linux and macOS. Native Windows is not currently covered by CI.

To update response descriptions, edit `tcinvest.response-schemas.json`, add a
regression fixture, restart the server, and export its generated document:

```sh
curl --fail http://127.0.0.1:8788/openapi.json > tcinvest.openapi.yaml
```

Run the tests after export. Schemas are reviewed definitions; responses are not
used to infer or change them automatically at runtime.

To also check real DuckDB function registration and an end-to-end SQL → REST call,
use the extension installed by SpurLab and its **matching** DuckDB version:

```sh
TCINVEST_TEST_EXTENSION="$HOME/.spur/extensions/duckdb-v1.5.5/spur_rest.duckdb_extension" \
  uv run --no-project --with certifi --with pytest --with openapi-schema-validator --with duckdb==1.5.5 \
  python -m pytest -q
```

This optional test uses a temporary manifest directory and mock TCInvest results.
It does not change your saved connections. The response-schema update also
validated live normalized rows across all 54 upstream tools, including bank and
non-bank samples. That is snapshot validation, not a guarantee about every
future response; the reproducible CI checks use the synthetic fixtures above.

## License

[Apache-2.0](LICENSE). Extracted from the Spur notebook project, with standalone
launch documentation, catalog contract tests, and usage examples added here.
TCInvest and TCBS are the upstream service names; this repository is maintained
by getspur.
