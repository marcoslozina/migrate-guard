# Demo: retail pricing

A legacy pricing service (`legacy/`, Flask) and its rewrite (`rewrite/`, FastAPI), "migrated with
an AI assistant". The rewrite looks fine and still changes five behaviors:

1. **Rounding:** `round()` on a float. `2.675` is stored as `2.67499999…`, so it becomes `2.67`.
2. **Stacked promotions:** fixed amounts before percentages. `(100 - 5) × 0.9 = 85.50`, not `85.00`.
3. **No end date:** a promotion without `ends_at` counts as expired.
4. **Time zone:** end dates stored in Buenos Aires time are read as UTC, so a promotion ends
   three hours early.
5. **A read that writes:** a price lookup also stamps `last_priced_at`. The response is identical;
   only the database comparison sees it.

Each service has its own SQLite database with its own schema: `Prices` / `SKU` / `PriceAmt` in the
legacy one, `prices` / `sku` / `price` in the rewrite. `migrate-guard.yaml` maps both to the same
logical names, and the scripts in `seeds/` reset both databases before every case.

## Run it

From the repo root:

```bash
uv sync --group demo
examples/retail-pricing/run-demo.sh
```

```
✗ price of one unit that ends in 5
    /line_total  2.68 → 2.67  value_changed  (rounded to 2 decimals (half_up): 2.68 vs 2.67)
    state /prices/sku=A1/last_priced_at  null → "2026-11-09T15:29:02.844293+00:00"  type_changed
✓ cart without promotions
✗ stacked percentage and fixed promotions
    /discount  15.0 → 14.5  value_changed
    /total  85.0 → 85.5  value_changed
✗ promotion with no end date
    /applied_promos/0  "LOYAL2" → (missing)  missing_in_candidate
    /discount  2.0 → 0.0  value_changed
    /total  23.0 → 25.0  value_changed
✗ last night of a promotion, Buenos Aires time
    /applied_promos/0  "SUMMER10" → (missing)  missing_in_candidate
    /discount  2.5 → 0.0  value_changed
    /total  22.5 → 25.0  value_changed
✗ price lookup that only reads
    state /prices/sku=B2/last_priced_at  null → "2026-11-09T15:29:02.885023+00:00"  type_changed

6 cases: 1 match · 5 with differences · 0 errors
```

The exit code is `1`. The cart without promotions matches, in the response and in the database,
even though the rewrite sorts the lines, returns a different `request_id` and writes `priced_at`
in UTC instead of `-03:00`: the rules in `migrate-guard.yaml` tolerate exactly those differences,
and nothing else.

The last case is the one a response-only tool misses: both services answer the same JSON, and
only the rewrite changes a row.
