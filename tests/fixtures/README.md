# Response shape fixtures

`response_rows.json` records representative normalized row shapes from the
TCInvest bridge, including bank and non-bank responses. Every scalar value is
replaced with synthetic test data, and nested lists are reduced to a
representative item. These are not market quotes or captured account data.

The response catalog also retains fields from the existing curated SQL
projections, including fields missing from these samples. Numeric projection
types are preserved or widened to match observed JSON numbers. Fields with no
established non-null JSON type remain unconstrained and are identified in their
descriptions. Unknown properties are accepted for forward compatibility.

Add fixtures when a response shape changes, and update the reviewed catalog
deliberately. Do not copy credentials, account data, or complete live payloads
into these fixtures.
