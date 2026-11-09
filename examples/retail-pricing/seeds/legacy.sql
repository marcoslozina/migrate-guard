-- Legacy schema: PascalCase table and columns. Runs before every case, inside a transaction.
DROP TABLE IF EXISTS "Prices";
CREATE TABLE "Prices" (
    "SKU" TEXT PRIMARY KEY,
    "PriceAmt" NUMERIC NOT NULL,
    "LastPricedAt" TEXT          -- set by the nightly repricing job, never by a lookup
);
INSERT INTO "Prices" ("SKU", "PriceAmt") VALUES ('A1', 2.675), ('B2', 10.00), ('C3', 25.00);
