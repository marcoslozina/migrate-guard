"""The rewrite (FastAPI), "migrated with an AI assistant". It looks fine at a glance.

It still changes five behaviors on purpose (see the demo README):
  1. round() on a float instead of half-up on Decimal: 2.675 becomes 2.67.
  2. Fixed discounts before percentages.
  3. A promotion with no end date counts as expired.
  4. Promotion end dates are read as UTC, but they were stored in Buenos Aires time.
  5. A price lookup also writes `last_priced_at`. Same response, different database.

Run it with ./run-demo.sh, which starts it with uvicorn on port 5002.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Same catalog, new schema: table "prices" with snake_case columns (see seeds/rewrite.sql).
DB_PATH = os.environ.get("REWRITE_DB", "rewrite.db")
PROMOS: dict[str, dict[str, Any]] = {
    "SUMMER10": {"percent": 10.0, "ends_at": datetime(2026, 11, 30, 23, 59)},
    "VIP5": {"fixed": 5.00, "ends_at": datetime(2026, 12, 31, 23, 59)},
    "LOYAL2": {"fixed": 2.00, "ends_at": None},
}

app = FastAPI()


def catalog() -> dict[str, float]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        return dict(conn.execute("SELECT sku, price FROM prices").fetchall())


def touch(sku: str) -> None:
    # bug 5: "keep track of when a price was last used". Nobody asked for this write.
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        now = datetime.now(UTC).isoformat()
        conn.execute("UPDATE prices SET last_priced_at = ? WHERE sku = ?", (now, sku))


class Item(BaseModel):
    sku: str
    qty: int


class Cart(BaseModel):
    items: list[Item]
    promo_codes: list[str] = []
    at: datetime


def money(value: float) -> float:
    return round(value, 2)  # bug 1: 2.675 is stored as 2.67499999..., so this gives 2.67


def is_active(promo: dict[str, Any], at: datetime) -> bool:
    ends_at = promo["ends_at"]
    if ends_at is None:
        return False  # bug 3
    return at.astimezone(UTC) <= ends_at.replace(tzinfo=UTC)  # bug 4


def envelope(body: dict[str, Any]) -> dict[str, Any]:
    return {"request_id": uuid.uuid4().hex, "generated_at": datetime.now(UTC).isoformat(), **body}


@app.get("/prices/{sku}")
def price(sku: str, qty: int = 1) -> dict[str, Any]:
    prices = catalog()
    if sku not in prices:
        raise HTTPException(404)
    unit = prices[sku]
    touch(sku)
    return envelope({"sku": sku, "qty": qty, "unit_price": unit, "line_total": money(unit * qty)})


@app.post("/cart/price")
def price_cart(cart: Cart) -> dict[str, Any]:
    prices = catalog()
    lines = sorted((i.sku, i.qty, prices[i.sku] * i.qty) for i in cart.items)
    subtotal = sum(line for _, _, line in lines)

    active = [c for c in cart.promo_codes if is_active(PROMOS[c], cart.at)]
    total = subtotal
    for code in active:  # bug 2: fixed amounts first
        if "fixed" in PROMOS[code]:
            total -= PROMOS[code]["fixed"]
    for code in active:
        if "percent" in PROMOS[code]:
            total -= total * PROMOS[code]["percent"] / 100

    return envelope(
        {
            "priced_at": cart.at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "items": [{"sku": s, "qty": q, "line_total": money(t)} for s, q, t in lines],
            "applied_promos": active,
            "subtotal": money(subtotal),
            "discount": money(subtotal - total),
            "total": money(total),
        }
    )
