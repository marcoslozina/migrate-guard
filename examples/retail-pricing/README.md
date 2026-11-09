# Demo: retail pricing

A legacy pricing service (`legacy/`, Flask) and its rewrite (`rewrite/`, FastAPI), "migrated with
an AI assistant". The rewrite looks fine and still changes four behaviors:

1. **Rounding:** `round()` on a float. `2.675` is stored as `2.67499999…`, so it becomes `2.67`.
2. **Stacked promotions:** fixed amounts before percentages. `(100 - 5) × 0.9 = 85.50`, not `85.00`.
3. **No end date:** a promotion without `ends_at` counts as expired.
4. **Time zone:** end dates stored in Buenos Aires time are read as UTC, so a promotion ends
   three hours early.

A fifth one (a read that silently writes `last_priced_at`) needs the database state comparison,
which is the next milestone.

## Run it

From the repo root:

```bash
uv sync --group demo
examples/retail-pricing/run-demo.sh
```

```
✗ price of one unit that ends in 5
    /line_total  2.68 → 2.67  value_changed  (rounded to 2 decimals (half_up): 2.68 vs 2.67)
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

5 cases: 1 match · 4 with differences · 0 errors
```

The exit code is `1`. The cart without promotions matches even though the rewrite sorts the lines,
returns a different `request_id` and writes `priced_at` in UTC instead of `-03:00`: the rules in
`migrate-guard.yaml` tolerate exactly those differences, and nothing else.
