"""DuckDB persistence layer.

DuckDB chosen over SQLite because the core workload is OLAP (rank, window,
join scraped snapshots over time). One file on disk, zero server, columnar
storage, Parquet-compatible. Postgres migration path: replace this module
with a SQLAlchemy engine pointed at Railway Postgres — the rest of the app
talks to the `ProductRepository` interface, not raw DuckDB.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from .models import Platform, Product, ProductScore, ScoredProduct, ScoreBreakdown


_DEFAULT_DB_PATH = Path("affiliate.duckdb")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    category TEXT,
    niche TEXT,
    price_brl DOUBLE,
    commission_pct DOUBLE,
    commission_brl DOUBLE,
    popularity DOUBLE,
    producer_name TEXT,
    producer_reputation DOUBLE,
    sales_page_signals DOUBLE,
    scraped_at TIMESTAMP NOT NULL,
    raw JSON
);

CREATE TABLE IF NOT EXISTS product_scores (
    product_id TEXT NOT NULL,
    score DOUBLE NOT NULL,
    component_commission DOUBLE NOT NULL,
    component_ticket DOUBLE NOT NULL,
    component_reputation DOUBLE NOT NULL,
    component_popularity DOUBLE NOT NULL,
    component_sales_signals DOUBLE NOT NULL,
    expected_value_per_visit DOUBLE NOT NULL,
    computed_at TIMESTAMP NOT NULL
);
"""


class ProductRepository:
    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        with self._connect() as con:
            con.execute(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._db_path)
        try:
            yield con
        finally:
            con.close()

    def upsert_products(self, products: list[Product]) -> int:
        if not products:
            return 0
        rows = [
            (
                p.id,
                p.platform.value,
                p.external_id,
                p.name,
                p.url,
                p.category,
                p.niche.value if p.niche else None,
                p.price_brl,
                p.commission_pct,
                p.commission_brl,
                p.popularity,
                p.producer_name,
                p.producer_reputation,
                p.sales_page_signals,
                p.scraped_at,
                json.dumps(p.raw, default=str),
            )
            for p in products
        ]
        with self._connect() as con:
            # DuckDB upsert via DELETE + INSERT keyed on id.
            ids = [r[0] for r in rows]
            con.execute(
                f"DELETE FROM products WHERE id IN ({','.join(['?'] * len(ids))})",
                ids,
            )
            con.executemany(
                """
                INSERT INTO products VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                rows,
            )
        return len(rows)

    def replace_scores(self, scores: list[ProductScore]) -> int:
        if not scores:
            return 0
        with self._connect() as con:
            ids = [s.product_id for s in scores]
            con.execute(
                f"DELETE FROM product_scores WHERE product_id IN ({','.join(['?'] * len(ids))})",
                ids,
            )
            con.executemany(
                """
                INSERT INTO product_scores VALUES (?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        s.product_id,
                        s.score,
                        s.components.commission,
                        s.components.ticket,
                        s.components.reputation,
                        s.components.popularity,
                        s.components.sales_signals,
                        s.expected_value_per_visit,
                        s.computed_at,
                    )
                    for s in scores
                ],
            )
        return len(scores)

    def top_products(self, limit: int = 25) -> list[ScoredProduct]:
        query = """
        SELECT
            p.platform, p.external_id, p.name, p.url, p.category, p.niche,
            p.price_brl, p.commission_pct, p.popularity, p.producer_name,
            p.producer_reputation, p.sales_page_signals, p.scraped_at, p.raw,
            s.score, s.component_commission, s.component_ticket,
            s.component_reputation, s.component_popularity,
            s.component_sales_signals, s.expected_value_per_visit, s.computed_at
        FROM products p
        JOIN product_scores s ON s.product_id = p.id
        ORDER BY s.score DESC
        LIMIT ?
        """
        with self._connect() as con:
            rows = con.execute(query, [limit]).fetchall()

        out: list[ScoredProduct] = []
        for r in rows:
            product = Product(
                platform=Platform(r[0]),
                external_id=r[1],
                name=r[2],
                url=r[3],
                category=r[4],
                niche=r[5],
                price_brl=r[6],
                commission_pct=r[7],
                popularity=r[8],
                producer_name=r[9],
                producer_reputation=r[10],
                sales_page_signals=r[11],
                scraped_at=r[12],
                raw=json.loads(r[13]) if r[13] else {},
            )
            score = ProductScore(
                product_id=product.id,
                score=r[14],
                components=ScoreBreakdown(
                    commission=r[15],
                    ticket=r[16],
                    reputation=r[17],
                    popularity=r[18],
                    sales_signals=r[19],
                ),
                expected_value_per_visit=r[20],
                computed_at=r[21],
            )
            out.append(ScoredProduct(product=product, score=score))
        return out
