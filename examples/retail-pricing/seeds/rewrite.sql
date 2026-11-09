-- Rewrite schema: same data, snake_case names. Runs before every case, inside a transaction.
DROP TABLE IF EXISTS prices;
CREATE TABLE prices (
    sku TEXT PRIMARY KEY,
    price REAL NOT NULL,
    last_priced_at TEXT
);
INSERT INTO prices (sku, price) VALUES ('A1', 2.675), ('B2', 10.00), ('C3', 25.00);
