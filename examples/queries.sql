-- Run in SpurLab SQL cells after installing tcinvest.connection.toml
-- and starting tcinvest_rest.py. Run one statement per cell if preferred.

-- Discover all registered TCInvest table functions (55 in this snapshot).
SELECT function_name, parameters, parameter_types
FROM duckdb_functions()
WHERE function_type = 'table'
  AND starts_with(function_name, 'tcinvest_')
ORDER BY function_name;

-- Read the live upstream tool catalog.
SELECT * FROM tcinvest_list_tools();

-- Parameters go inside the call so they reach TCInvest.
SELECT ticker, exchange
FROM tcinvest_get_ticker_overview(ticker := 'TCB');

SELECT price_to_earning, price_to_book, roe
FROM tcinvest_get_stock_ratio(ticker := 'TCB');

SELECT *
FROM tcinvest_get_ticker_activity_news(ticker := 'TCB', page := '0', size := '5');

-- Quote argument names that contain a hyphen.
SELECT * FROM tcinvest_get_industries("x-language" := 'vi');
