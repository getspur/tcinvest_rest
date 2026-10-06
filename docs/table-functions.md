# TCInvest SQL table functions

Install [`tcinvest.connection.toml`](../tcinvest.connection.toml) using the [README](../README.md#3-install-the-table-functions-into-spurlab).

This snapshot declares 55 functions: 54 TCInvest operations and the tool catalog.
The running service may change; `GET /tools` is the live upstream catalog.

Pass remote filters as named string arguments. Required names below must be supplied;
quote SQL argument names containing a hyphen, such as `"x-language" := 'vi'`.
The REST bridge converts strings to the integer, number, or boolean type advertised by MCP.

| SQL function | Required arguments | Optional arguments | Columns |
| --- | --- | --- | ---: |
| `tcinvest_list_tools` | — | — | 3 |
| `tcinvest_get_stock_ratio` | `ticker` | — | 32 |
| `tcinvest_get_stock_same_industry` | `ticker` | — | 17 |
| `tcinvest_get_stock_recommend` | `ticker` | — | 17 |
| `tcinvest_get_price_volatility` | `ticker` | — | 6 |
| `tcinvest_get_ticker_overview` | `ticker` | — | 22 |
| `tcinvest_get_ticker_event_news` | `ticker` | `page`, `size` | 16 |
| `tcinvest_get_ticker_activity_news` | `ticker` | `page`, `size` | 12 |
| `tcinvest_get_projection_ratio` | `ticker` | — | 60 |
| `tcinvest_get_financialdata` | `ticker` | — | 64 |
| `tcinvest_get_event_news_detail` | `id` | — | 16 |
| `tcinvest_get_activity_news_detail` | `id` | — | 17 |
| `tcinvest_get_tooltip` | `ticker` | — | 15 |
| `tcinvest_get_income_statement_for_bank` | `ticker`, `yearly` | `fromYear` | 21 |
| `tcinvest_get_income_statement_for_non_bank` | `ticker`, `yearly` | `fromYear` | 20 |
| `tcinvest_get_financial_ratio_for_bank` | `ticker`, `yearly` | `fromYear` | 28 |
| `tcinvest_get_financial_ratio_for_non_bank` | `ticker`, `yearly` | `fromYear` | 43 |
| `tcinvest_get_cash_flow_analyze` | `ticker`, `yearly` | — | 29 |
| `tcinvest_get_cash_flow_for_bank` | `ticker`, `yearly` | `fromYear` | 9 |
| `tcinvest_get_cash_flow_for_non_bank` | `ticker`, `yearly` | `fromYear` | 9 |
| `tcinvest_get_balance_sheet_for_bank` | `ticker`, `yearly` | `fromYear` | 31 |
| `tcinvest_get_balance_sheet_for_non_bank` | `ticker`, `yearly` | `fromYear` | 21 |
| `tcinvest_get_business_results` | `ticker`, `yearly` | — | 1 |
| `tcinvest_get_income_statement_industry_for_bank` | `icbCodeL2`, `yearly` | `fromYear` | 21 |
| `tcinvest_get_income_statement_industry_for_non_bank` | `icbCodeL2`, `yearly` | `fromYear` | 20 |
| `tcinvest_get_financial_ratio_industry_for_bank` | `icbCodeL2`, `yearly` | `fromYear` | 28 |
| `tcinvest_get_financial_ratio_industry_for_non_bank` | `icbCodeL2`, `yearly` | `fromYear` | 43 |
| `tcinvest_get_cash_flow_industry_for_bank` | `icbCodeL2`, `yearly` | `fromYear` | 9 |
| `tcinvest_get_cash_flow_industry_for_non_bank` | `icbCodeL2`, `yearly` | `fromYear` | 9 |
| `tcinvest_get_balance_sheet_industry_for_bank` | `icbCodeL2`, `yearly` | `fromYear` | 16 |
| `tcinvest_get_balance_sheet_industry_for_non_bank` | `icbCodeL2`, `yearly` | `fromYear` | 16 |
| `tcinvest_get_asset_pro_portion` | `ticker`, `yearly` | — | 2 |
| `tcinvest_get_volume_and_foreign` | `ticker` | — | 9 |
| `tcinvest_get_technical_indicator` | `ticker` | — | 15 |
| `tcinvest_get_sub_company` | `ticker` | `page`, `size` | 5 |
| `tcinvest_get_company_overview` | `ticker` | — | 10 |
| `tcinvest_get_large_share_holders` | `ticker` | `size` | 5 |
| `tcinvest_get_key_officers` | `ticker` | `page`, `size` | 6 |
| `tcinvest_get_insider_dealing` | `ticker` | `page`, `size` | 9 |
| `tcinvest_get_dividend_payment_histories` | `ticker` | `page`, `size` | 7 |
| `tcinvest_get_list_audit_firm` | `ticker` | `page`, `size` | 5 |
| `tcinvest_get_history_recommendation` | `fType`, `fData`, `fTime` | `x-language`, `page`, `size`, `fRecommend` | 7 |
| `tcinvest_get_general_rating` | `ticker`, `fType` | `x-language` | 16 |
| `tcinvest_get_gauge_chart` | `ticker` | `period` | 10 |
| `tcinvest_get_long_term_candle_chart` | `type`, `ticker`, `resolution`, `to`, `countBack` | — | 9 |
| `tcinvest_get_industries` | — | `x-language` | 4 |
| `tcinvest_get_industry_index` | `exchange`, `industry` | — | 2 |
| `tcinvest_get_market_breadth` | `exchange`, `industry` | — | 7 |
| `tcinvest_get_market_leader` | `exchange`, `industry` | — | 6 |
| `tcinvest_get_investor_classify` | `exchange`, `industry` | — | 20 |
| `tcinvest_get_market_foreign_val` | `exchange`, `industry` | — | 6 |
| `tcinvest_get_flow_market_value_percent_trading` | `exchange`, `industry` | — | 6 |
| `tcinvest_get_fi_time_series` | `industryCodes` | — | 3 |
| `tcinvest_get_market_volatility` | `exchange`, `industrys` | — | 12 |
| `tcinvest_get_rs_rating` | `ticker`, `countBack`, `to` | — | 7 |

## Inspect columns

After starting the bridge:

```sql
DESCRIBE SELECT * FROM tcinvest_get_ticker_overview(ticker := 'TCB');
```

The manifest is the source of truth for column names, types, JSON paths, and
argument mappings. Numeric fields retain the upstream units; consult the tool
description in `/tools` or the OpenAPI snapshot before interpreting a metric.
Several operations return nested objects or arrays as JSON text. Every TCInvest
operation includes `payload` as an escape hatch; some operations have only that
column until a richer projection is declared.

## Updating coverage

When TCInvest adds or changes a tool, compare `/tools` and `/openapi.json` with
the snapshot, update the manifest paths/filters and observed row projections,
and run the catalog tests. Adding an operation to the live REST catalog does
not automatically add a new SQL function to an already installed manifest.
Restart the notebook kernel after replacing the installed manifest.
