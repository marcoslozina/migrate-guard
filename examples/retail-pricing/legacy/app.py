"""The legacy pricing service (Flask). This is the behavior the rewrite must keep.

Run it with ./run-demo.sh, which starts it with flask on port 5001.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from flask import Flask, abort, jsonify, request

# The legacy service lives in Buenos Aires: promotion dates are stored in local time.
LOCAL = timezone(timedelta(hours=-3))
CENT = Decimal("0.01")

# The catalog lives in the legacy SQLite database (table "Prices"; see seeds/legacy.sql).
DB_PATH = os.environ.get("LEGACY_DB", "legacy.db")
PROMOS: dict[str, dict[str, Any]] = {
    "SUMMER10": {"percent": Decimal("10"), "ends_at": datetime(2026, 11, 30, 23, 59)},
    "VIP5": {"fixed": Decimal("5.00"), "ends_at": datetime(2026, 12, 31, 23, 59)},
    "LOYAL2": {"fixed": Decimal("2.00"), "ends_at": None},  # no end date: always active
}

app = Flask(__name__)


def catalog() -> dict[str, Decimal]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute('SELECT "SKU", "PriceAmt" FROM "Prices"').fetchall()
    return {sku: Decimal(str(amount)) for sku, amount in rows}


def money(value: Decimal) -> float:
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def is_active(promo: dict[str, Any], at: datetime) -> bool:
    ends_at = promo["ends_at"]
    if ends_at is None:
        return True
    return at.astimezone(LOCAL).replace(tzinfo=None) <= ends_at


def envelope(body: dict[str, Any]) -> dict[str, Any]:
    return {"request_id": str(uuid.uuid4()), "generated_at": datetime.now(UTC).isoformat(), **body}


@app.get("/prices/<sku>")
def price(sku: str) -> Any:
    prices = catalog()  # read only: legacy never writes on a price lookup
    if sku not in prices:
        abort(404)
    qty = int(request.args.get("qty", "1"))
    return jsonify(
        envelope(
            {
                "sku": sku,
                "qty": qty,
                "unit_price": float(prices[sku]),
                "line_total": money(prices[sku] * qty),
            }
        )
    )


@app.post("/cart/price")
def price_cart() -> Any:
    payload = request.get_json()
    at = datetime.fromisoformat(payload["at"])
    prices = catalog()
    lines = [(i["sku"], int(i["qty"]), prices[i["sku"]] * int(i["qty"])) for i in payload["items"]]
    subtotal = sum((line for _, _, line in lines), Decimal(0))

    active = [c for c in payload.get("promo_codes", []) if is_active(PROMOS[c], at)]
    # Percentages first, then fixed amounts.
    total = subtotal
    for code in active:
        if "percent" in PROMOS[code]:
            total -= total * PROMOS[code]["percent"] / 100
    for code in active:
        if "fixed" in PROMOS[code]:
            total -= PROMOS[code]["fixed"]

    return jsonify(
        envelope(
            {
                "priced_at": at.astimezone(LOCAL).isoformat(),
                "items": [{"sku": s, "qty": q, "line_total": money(t)} for s, q, t in lines],
                "applied_promos": active,
                "subtotal": money(subtotal),
                "discount": money(subtotal - total),
                "total": money(total),
            }
        )
    )
