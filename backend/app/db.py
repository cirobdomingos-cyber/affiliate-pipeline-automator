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

from .models import (
    AffiliateLink,
    LinkStatus,
    Platform,
    Product,
    ProductScore,
    ScoredProduct,
    ScoreBreakdown,
)


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

CREATE TABLE IF NOT EXISTS affiliate_links (
    id TEXT PRIMARY KEY,
    product_id TEXT,
    platform TEXT NOT NULL,
    label TEXT NOT NULL,
    raw_url TEXT NOT NULL,
    approval_status TEXT NOT NULL,
    approved_at TIMESTAMP,
    notes TEXT,
    tags JSON,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
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
        # Touch a connection once so the file exists and the schema is applied.
        # _connect() itself re-runs the (idempotent) schema on every call.
        with self._connect():
            pass

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        # Run schema creation on every connection. Idempotent (CREATE TABLE
        # IF NOT EXISTS) and cheap. Protects against the file being deleted
        # out from under a cached repository instance — without this,
        # Streamlit's @st.cache_resource plus a manual `rm affiliate.duckdb`
        # leaves the repo holding a path to a file that has no schema, and
        # DuckDB's replacement scan then tries to interpret local Python
        # variables named like table names (e.g. `items`) as data sources
        # and fails with a cryptic error.
        con = duckdb.connect(self._db_path)
        try:
            con.execute(_SCHEMA)
            yield con
        finally:
            con.close()

    def upsert_products(self, items: list[Product]) -> int:
        # Parameter is named `items` (not `products`) to avoid colliding with
        # the `products` table name in SQL. DuckDB's replacement scan walks
        # the caller's Python frame looking for identifiers that match table
        # names; a local variable named `products` would be misinterpreted as
        # a data source if the schema ever went missing.
        if not items:
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
            for p in items
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

    def clear_catalog(self) -> int:
        """Wipe every product and product_score row. Returns rows deleted.

        Used by the 'Clear catalog' button in the UI — and by anyone who
        wants to reset the discovery state without deleting the DB file on
        disk (which is fragile when the repo is cached by Streamlit).
        The affiliate_links table is NOT touched — the vault is user work,
        not scrape output.
        """
        with self._connect() as con:
            before = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            con.execute("DELETE FROM product_scores")
            con.execute("DELETE FROM products")
        return int(before)

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


class LinkRepository:
    """CRUD for the affiliate link vault.

    Shares the same DuckDB file as ProductRepository. Links reference
    products by `product_id` but are not a foreign key — an operator can
    stash a link for a product that isn't in the discovery catalog yet.
    """

    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        # First connection runs the schema; subsequent connections repeat it
        # idempotently in _connect() for the same reason as ProductRepository.
        with self._connect() as con:
            pass

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._db_path)
        try:
            con.execute(_SCHEMA)
            yield con
        finally:
            con.close()

    def upsert(self, link: AffiliateLink) -> None:
        row = (
            link.id,
            link.product_id,
            link.platform.value,
            link.label,
            link.raw_url,
            link.approval_status.value,
            link.approved_at,
            link.notes,
            json.dumps(link.tags),
            link.created_at,
            link.updated_at,
        )
        with self._connect() as con:
            con.execute("DELETE FROM affiliate_links WHERE id = ?", [link.id])
            con.execute(
                "INSERT INTO affiliate_links VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )

    def get(self, link_id: str) -> AffiliateLink | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM affiliate_links WHERE id = ?", [link_id]
            ).fetchone()
        return self._row_to_link(row) if row else None

    def list(
        self,
        *,
        status: LinkStatus | None = None,
        platform: Platform | None = None,
    ) -> list[AffiliateLink]:
        sql = "SELECT * FROM affiliate_links"
        clauses = []
        params: list = []
        if status is not None:
            clauses.append("approval_status = ?")
            params.append(status.value)
        if platform is not None:
            clauses.append("platform = ?")
            params.append(platform.value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [self._row_to_link(r) for r in rows]

    def set_status(self, link_id: str, status: LinkStatus) -> AffiliateLink | None:
        link = self.get(link_id)
        if link is None:
            return None
        link.approval_status = status
        from datetime import datetime, timezone
        link.updated_at = datetime.now(timezone.utc)
        if status == LinkStatus.APPROVED and link.approved_at is None:
            link.approved_at = link.updated_at
        self.upsert(link)
        return link

    def delete(self, link_id: str) -> bool:
        with self._connect() as con:
            cur = con.execute("DELETE FROM affiliate_links WHERE id = ?", [link_id])
            return cur.fetchall() is not None  # DuckDB returns empty list on DELETE

    @staticmethod
    def _row_to_link(row) -> AffiliateLink:
        return AffiliateLink(
            id=row[0],
            product_id=row[1],
            platform=Platform(row[2]),
            label=row[3],
            raw_url=row[4],
            approval_status=LinkStatus(row[5]),
            approved_at=row[6],
            notes=row[7] or "",
            tags=json.loads(row[8]) if row[8] else [],
            created_at=row[9],
            updated_at=row[10],
        )
