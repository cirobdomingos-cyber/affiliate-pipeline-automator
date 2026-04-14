"""DuckDB persistence layer.

DuckDB chosen over SQLite because the core workload is OLAP (rank, window,
join scraped snapshots over time). One file on disk, zero server, columnar
storage, Parquet-compatible. Postgres migration path: replace this module
with a SQLAlchemy engine pointed at Railway Postgres — the rest of the app
talks to the `ProductRepository` interface, not raw DuckDB.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from datetime import datetime, timezone

from .models import (
    AffiliateLink,
    BridgePage,
    ChannelConfig,
    ChannelStatus,
    ClickEvent,
    EmailSequence,
    EmailSequenceStep,
    GeneratedCreative,
    LinkStatus,
    ManagedProduct,
    Niche,
    OperatorProfile,
    Platform,
    Product,
    ProductScore,
    ScoredProduct,
    ScoreBreakdown,
    ShortLink,
    ShortLinkStats,
    SubscriberCountSnapshot,
    TrafficChannel,
)


# DuckDB file path. In production (Railway) a persistent volume is mounted at
# $DATA_DIR and both the api and ui services point at the same file. Locally
# this falls through to the repo root so existing dev workflow is untouched.
_DEFAULT_DB_PATH = Path(os.environ.get("DATA_DIR", ".")) / "affiliate.duckdb"


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

CREATE TABLE IF NOT EXISTS managed_products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    niche TEXT NOT NULL,
    commission_pct DOUBLE NOT NULL,
    ticket_brl DOUBLE NOT NULL,
    sales_page_url TEXT,
    affiliate_url TEXT NOT NULL,
    quality_score DOUBLE NOT NULL,
    epc_actual DOUBLE,
    cpv_actual DOUBLE,
    notes TEXT DEFAULT '',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS operator_profile (
    id INTEGER PRIMARY KEY,
    primary_niche TEXT NOT NULL,
    completed_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS short_links (
    slug TEXT PRIMARY KEY,
    managed_product_id TEXT NOT NULL,
    destination_url TEXT NOT NULL,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS click_events (
    id TEXT PRIMARY KEY,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    managed_product_id TEXT NOT NULL,
    ts TIMESTAMP NOT NULL,
    ip_prefix TEXT,
    ua_family TEXT,
    utm_source TEXT
);

CREATE TABLE IF NOT EXISTS channel_configs (
    id TEXT PRIMARY KEY,
    managed_product_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    daily_budget_brl DOUBLE,
    daily_click_goal INTEGER,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    UNIQUE (managed_product_id, channel)
);

CREATE TABLE IF NOT EXISTS bridge_pages (
    slug TEXT PRIMARY KEY,
    managed_product_id TEXT NOT NULL,
    headline TEXT NOT NULL,
    subheadline TEXT,
    bullets JSON NOT NULL,
    cta_text TEXT NOT NULL,
    cta_url TEXT NOT NULL,
    primary_color TEXT NOT NULL DEFAULT '#2563eb',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS email_sequences (
    id TEXT PRIMARY KEY,
    managed_product_id TEXT NOT NULL,
    name TEXT NOT NULL,
    mailerlite_group_id TEXT,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS email_sequence_steps (
    id TEXT PRIMARY KEY,
    sequence_id TEXT NOT NULL,
    step_order INTEGER NOT NULL,
    delay_days INTEGER NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriber_counts (
    managed_product_id TEXT PRIMARY KEY,
    count INTEGER NOT NULL,
    synced_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS generated_creatives (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    variant_index INTEGER NOT NULL,
    kind TEXT NOT NULL,
    asset_url TEXT NOT NULL,
    source_prompt TEXT NOT NULL,
    model TEXT NOT NULL,
    cost_brl DOUBLE,
    width INTEGER,
    height INTEGER,
    duration_s INTEGER,
    generated_at TIMESTAMP NOT NULL
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


class ManagedProductRepository:
    """CRUD for the operator's hand-curated product catalog (Stage 1)."""

    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        with self._connect():
            pass

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._db_path)
        try:
            con.execute(_SCHEMA)
            yield con
        finally:
            con.close()

    def upsert(self, mp: ManagedProduct) -> None:
        row = (
            mp.id,
            mp.name,
            mp.platform.value,
            mp.niche.value,
            mp.commission_pct,
            mp.ticket_brl,
            mp.sales_page_url,
            mp.affiliate_url,
            mp.quality_score,
            mp.epc_actual,
            mp.cpv_actual,
            mp.notes,
            mp.created_at,
            mp.updated_at,
        )
        with self._connect() as con:
            con.execute("DELETE FROM managed_products WHERE id = ?", [mp.id])
            con.execute(
                "INSERT INTO managed_products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )

    def get(self, product_id: str) -> ManagedProduct | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM managed_products WHERE id = ?", [product_id]
            ).fetchone()
        return self._row_to_mp(row) if row else None

    def list(self, *, niche: Niche | None = None) -> list[ManagedProduct]:
        sql = "SELECT * FROM managed_products"
        params: list = []
        if niche is not None:
            sql += " WHERE niche = ?"
            params.append(niche.value)
        sql += " ORDER BY quality_score DESC, created_at DESC"
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [self._row_to_mp(r) for r in rows]

    def delete(self, product_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM managed_products WHERE id = ?", [product_id])

    @staticmethod
    def _row_to_mp(row) -> ManagedProduct:
        return ManagedProduct(
            id=row[0],
            name=row[1],
            platform=Platform(row[2]),
            niche=Niche(row[3]),
            commission_pct=row[4],
            ticket_brl=row[5],
            sales_page_url=row[6],
            affiliate_url=row[7],
            quality_score=row[8],
            epc_actual=row[9],
            cpv_actual=row[10],
            notes=row[11] or "",
            created_at=row[12],
            updated_at=row[13],
        )


class OperatorProfileRepository:
    """Single-row onboarding preference."""

    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        with self._connect():
            pass

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._db_path)
        try:
            con.execute(_SCHEMA)
            yield con
        finally:
            con.close()

    def get(self) -> OperatorProfile | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT primary_niche, completed_at FROM operator_profile WHERE id = 1"
            ).fetchone()
        if not row:
            return None
        return OperatorProfile(primary_niche=Niche(row[0]), completed_at=row[1])

    def save(self, profile: OperatorProfile) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM operator_profile WHERE id = 1")
            con.execute(
                "INSERT INTO operator_profile VALUES (1, ?, ?)",
                [profile.primary_niche.value, profile.completed_at],
            )


class ShortLinkRepository:
    """CRUD + stats for /r/{slug} redirects (Stage 2)."""

    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        with self._connect():
            pass

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._db_path)
        try:
            con.execute(_SCHEMA)
            yield con
        finally:
            con.close()

    def upsert(self, link: ShortLink) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM short_links WHERE slug = ?", [link.slug])
            con.execute(
                "INSERT INTO short_links VALUES (?,?,?,?,?,?,?)",
                [
                    link.slug,
                    link.managed_product_id,
                    link.destination_url,
                    link.utm_source,
                    link.utm_medium,
                    link.utm_campaign,
                    link.created_at,
                ],
            )

    def get(self, slug: str) -> ShortLink | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM short_links WHERE slug = ?", [slug]
            ).fetchone()
        if not row:
            return None
        return ShortLink(
            slug=row[0],
            managed_product_id=row[1],
            destination_url=row[2],
            utm_source=row[3],
            utm_medium=row[4],
            utm_campaign=row[5],
            created_at=row[6],
        )

    def list(self, *, managed_product_id: str | None = None) -> list[ShortLink]:
        sql = "SELECT * FROM short_links"
        params: list = []
        if managed_product_id:
            sql += " WHERE managed_product_id = ?"
            params.append(managed_product_id)
        sql += " ORDER BY created_at DESC"
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [
            ShortLink(
                slug=r[0],
                managed_product_id=r[1],
                destination_url=r[2],
                utm_source=r[3],
                utm_medium=r[4],
                utm_campaign=r[5],
                created_at=r[6],
            )
            for r in rows
        ]

    def delete(self, slug: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM short_links WHERE slug = ?", [slug])

    def stats(self, slug: str) -> ShortLinkStats:
        with self._connect() as con:
            total = con.execute(
                "SELECT COUNT(*), MAX(ts) FROM click_events WHERE target_type='short_link' AND target_id=?",
                [slug],
            ).fetchone()
            unique = con.execute(
                "SELECT COUNT(DISTINCT ip_prefix) FROM click_events WHERE target_type='short_link' AND target_id=? AND ip_prefix IS NOT NULL",
                [slug],
            ).fetchone()[0]
            by_src = con.execute(
                "SELECT COALESCE(utm_source,'(none)'), COUNT(*) FROM click_events WHERE target_type='short_link' AND target_id=? GROUP BY 1 ORDER BY 2 DESC",
                [slug],
            ).fetchall()
        return ShortLinkStats(
            slug=slug,
            total_clicks=int(total[0] or 0),
            unique_clicks=int(unique or 0),
            last_click_at=total[1],
            by_utm_source={row[0]: int(row[1]) for row in by_src},
        )


class ClickEventRepository:
    """Append-only click log. Shared by short-links and bridge CTAs."""

    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        with self._connect():
            pass

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._db_path)
        try:
            con.execute(_SCHEMA)
            yield con
        finally:
            con.close()

    def log(self, event: ClickEvent) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO click_events VALUES (?,?,?,?,?,?,?,?)",
                [
                    event.id,
                    event.target_type,
                    event.target_id,
                    event.managed_product_id,
                    event.ts,
                    event.ip_prefix,
                    event.ua_family,
                    event.utm_source,
                ],
            )

    def count_for_product(
        self, managed_product_id: str, *, target_type: str | None = None
    ) -> int:
        sql = "SELECT COUNT(*) FROM click_events WHERE managed_product_id=?"
        params: list = [managed_product_id]
        if target_type:
            sql += " AND target_type=?"
            params.append(target_type)
        with self._connect() as con:
            return int(con.execute(sql, params).fetchone()[0] or 0)

    def recent_count(self, managed_product_id: str, *, hours: int) -> int:
        # DuckDB doesn't parameterize INTERVAL; compute the cutoff in Python.
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self._connect() as con:
            return int(
                con.execute(
                    "SELECT COUNT(*) FROM click_events WHERE managed_product_id=? AND ts >= ?",
                    [managed_product_id, cutoff],
                ).fetchone()[0]
                or 0
            )

    def daily_timeseries(
        self,
        managed_product_id: str,
        *,
        days: int = 30,
        target_type: str | None = None,
    ) -> list[tuple[str, int]]:
        """Return [(YYYY-MM-DD, count)] for the last `days` days. Missing days
        are NOT zero-filled — caller is responsible for presentation."""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        sql = (
            "SELECT CAST(ts AS DATE) AS d, COUNT(*) "
            "FROM click_events WHERE managed_product_id=? AND ts >= ?"
        )
        params: list = [managed_product_id, cutoff]
        if target_type:
            sql += " AND target_type=?"
            params.append(target_type)
        sql += " GROUP BY d ORDER BY d"
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [(str(r[0]), int(r[1])) for r in rows]


class ChannelConfigRepository:
    """Per-product traffic channel configuration (Stage 3)."""

    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        with self._connect():
            pass

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._db_path)
        try:
            con.execute(_SCHEMA)
            yield con
        finally:
            con.close()

    def upsert(self, cfg: ChannelConfig) -> None:
        with self._connect() as con:
            con.execute(
                "DELETE FROM channel_configs WHERE managed_product_id=? AND channel=?",
                [cfg.managed_product_id, cfg.channel.value],
            )
            con.execute(
                "INSERT INTO channel_configs VALUES (?,?,?,?,?,?,?,?)",
                [
                    cfg.id,
                    cfg.managed_product_id,
                    cfg.channel.value,
                    cfg.status.value,
                    cfg.daily_budget_brl,
                    cfg.daily_click_goal,
                    cfg.created_at,
                    cfg.updated_at,
                ],
            )

    def list_for_product(self, managed_product_id: str) -> list[ChannelConfig]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM channel_configs WHERE managed_product_id=? ORDER BY channel",
                [managed_product_id],
            ).fetchall()
        return [self._row_to_cfg(r) for r in rows]

    def list_all(self) -> list[ChannelConfig]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM channel_configs").fetchall()
        return [self._row_to_cfg(r) for r in rows]

    def delete(self, cfg_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM channel_configs WHERE id=?", [cfg_id])

    @staticmethod
    def _row_to_cfg(row) -> ChannelConfig:
        return ChannelConfig(
            id=row[0],
            managed_product_id=row[1],
            channel=TrafficChannel(row[2]),
            status=ChannelStatus(row[3]),
            daily_budget_brl=row[4],
            daily_click_goal=row[5],
            created_at=row[6],
            updated_at=row[7],
        )


class BridgePageRepository:
    """CRUD for bridge pages (Stage 4)."""

    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        with self._connect():
            pass

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._db_path)
        try:
            con.execute(_SCHEMA)
            yield con
        finally:
            con.close()

    def upsert(self, page: BridgePage) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM bridge_pages WHERE slug = ?", [page.slug])
            con.execute(
                "INSERT INTO bridge_pages VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    page.slug,
                    page.managed_product_id,
                    page.headline,
                    page.subheadline,
                    json.dumps(page.bullets),
                    page.cta_text,
                    page.cta_url,
                    page.primary_color,
                    page.created_at,
                    page.updated_at,
                ],
            )

    def get(self, slug: str) -> BridgePage | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM bridge_pages WHERE slug = ?", [slug]
            ).fetchone()
        return self._row_to_bp(row) if row else None

    def list(self, *, managed_product_id: str | None = None) -> list[BridgePage]:
        sql = "SELECT * FROM bridge_pages"
        params: list = []
        if managed_product_id:
            sql += " WHERE managed_product_id = ?"
            params.append(managed_product_id)
        sql += " ORDER BY updated_at DESC"
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [self._row_to_bp(r) for r in rows]

    def delete(self, slug: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM bridge_pages WHERE slug = ?", [slug])

    @staticmethod
    def _row_to_bp(row) -> BridgePage:
        return BridgePage(
            slug=row[0],
            managed_product_id=row[1],
            headline=row[2],
            subheadline=row[3],
            bullets=json.loads(row[4]) if row[4] else [],
            cta_text=row[5],
            cta_url=row[6],
            primary_color=row[7],
            created_at=row[8],
            updated_at=row[9],
        )


class EmailSequenceRepository:
    """CRUD for email nurture sequences + their steps (Stage 5).

    Steps are stored in a child table so we can query them independently (and
    a future scheduler can pluck by (sequence_id, step_order) without
    deserializing JSON). Upsert replaces the full step list atomically — the
    sequence-as-a-unit is the aggregate root.
    """

    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        with self._connect():
            pass

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._db_path)
        try:
            con.execute(_SCHEMA)
            yield con
        finally:
            con.close()

    def upsert(self, seq: EmailSequence) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM email_sequence_steps WHERE sequence_id=?", [seq.id])
            con.execute("DELETE FROM email_sequences WHERE id=?", [seq.id])
            con.execute(
                "INSERT INTO email_sequences VALUES (?,?,?,?,?)",
                [
                    seq.id,
                    seq.managed_product_id,
                    seq.name,
                    seq.mailerlite_group_id,
                    seq.created_at,
                ],
            )
            for step in seq.steps:
                con.execute(
                    "INSERT INTO email_sequence_steps VALUES (?,?,?,?,?,?)",
                    [
                        step.id,
                        seq.id,
                        step.step_order,
                        step.delay_days,
                        step.subject,
                        step.body,
                    ],
                )

    def get(self, sequence_id: str) -> EmailSequence | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM email_sequences WHERE id=?", [sequence_id]
            ).fetchone()
            if not row:
                return None
            step_rows = con.execute(
                "SELECT * FROM email_sequence_steps WHERE sequence_id=? ORDER BY step_order",
                [sequence_id],
            ).fetchall()
        return EmailSequence(
            id=row[0],
            managed_product_id=row[1],
            name=row[2],
            mailerlite_group_id=row[3],
            created_at=row[4],
            steps=[
                EmailSequenceStep(
                    id=s[0],
                    sequence_id=s[1],
                    step_order=s[2],
                    delay_days=s[3],
                    subject=s[4],
                    body=s[5],
                )
                for s in step_rows
            ],
        )

    def list(self, *, managed_product_id: str | None = None) -> list[EmailSequence]:
        with self._connect() as con:
            sql = "SELECT id FROM email_sequences"
            params: list = []
            if managed_product_id:
                sql += " WHERE managed_product_id=?"
                params.append(managed_product_id)
            sql += " ORDER BY created_at DESC"
            ids = [r[0] for r in con.execute(sql, params).fetchall()]
        return [seq for sid in ids if (seq := self.get(sid)) is not None]

    def delete(self, sequence_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM email_sequence_steps WHERE sequence_id=?", [sequence_id])
            con.execute("DELETE FROM email_sequences WHERE id=?", [sequence_id])


class SubscriberCountRepository:
    """Snapshot of each product's MailerLite subscriber count."""

    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        with self._connect():
            pass

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._db_path)
        try:
            con.execute(_SCHEMA)
            yield con
        finally:
            con.close()

    def save(self, snap: SubscriberCountSnapshot) -> None:
        with self._connect() as con:
            con.execute(
                "DELETE FROM subscriber_counts WHERE managed_product_id=?",
                [snap.managed_product_id],
            )
            con.execute(
                "INSERT INTO subscriber_counts VALUES (?,?,?)",
                [snap.managed_product_id, snap.count, snap.synced_at],
            )

    def get(self, managed_product_id: str) -> SubscriberCountSnapshot | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM subscriber_counts WHERE managed_product_id=?",
                [managed_product_id],
            ).fetchone()
        if not row:
            return None
        return SubscriberCountSnapshot(
            managed_product_id=row[0], count=row[1], synced_at=row[2]
        )

    def all(self) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT managed_product_id, count FROM subscriber_counts"
            ).fetchall()
        return {r[0]: int(r[1]) for r in rows}


class GeneratedCreativeRepository:
    """Append-store for concrete AI-generated images and videos.

    Each row is one asset (image or video) produced for one (product,
    variant_index, kind) combo. We allow multiple rows per combo — regenerating
    an image inserts a new row rather than replacing, so the operator can
    compare takes. The UI shows the most recent.
    """

    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        with self._connect():
            pass

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(self._db_path)
        try:
            con.execute(_SCHEMA)
            yield con
        finally:
            con.close()

    def insert(self, asset: GeneratedCreative) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO generated_creatives VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    asset.id,
                    asset.product_id,
                    asset.variant_index,
                    asset.kind,
                    asset.asset_url,
                    asset.source_prompt,
                    asset.model,
                    asset.cost_brl,
                    asset.width,
                    asset.height,
                    asset.duration_s,
                    asset.generated_at,
                ],
            )

    def list_for_product(self, product_id: str) -> list[GeneratedCreative]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM generated_creatives WHERE product_id=? ORDER BY generated_at DESC",
                [product_id],
            ).fetchall()
        return [self._row_to_asset(r) for r in rows]

    def latest_by_variant(
        self, product_id: str
    ) -> dict[tuple[int, str], GeneratedCreative]:
        """Return {(variant_index, kind): latest GeneratedCreative} for a product.
        Used by the UI to show the most recent image and video per variant."""
        out: dict[tuple[int, str], GeneratedCreative] = {}
        for asset in self.list_for_product(product_id):
            key = (asset.variant_index, asset.kind)
            if key not in out:  # list is sorted desc by time
                out[key] = asset
        return out

    def delete(self, asset_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM generated_creatives WHERE id=?", [asset_id])

    def total_cost_brl(self) -> float:
        with self._connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(cost_brl), 0) FROM generated_creatives"
            ).fetchone()
        return float(row[0] or 0)

    @staticmethod
    def _row_to_asset(row) -> GeneratedCreative:
        return GeneratedCreative(
            id=row[0],
            product_id=row[1],
            variant_index=row[2],
            kind=row[3],
            asset_url=row[4],
            source_prompt=row[5],
            model=row[6],
            cost_brl=row[7],
            width=row[8],
            height=row[9],
            duration_s=row[10],
            generated_at=row[11],
        )
