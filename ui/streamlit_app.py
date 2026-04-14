"""Streamlit UI for browsing scored affiliate products.

Why Streamlit for the MVP: this is an internal operator tool — single user,
data-heavy, schema is still moving. Streamlit gives us a working UI in a
fraction of the code Next.js would. When the data model stabilizes in V1
we'll port to Next.js against the same FastAPI contract.

Run from repo root:
    streamlit run ui/streamlit_app.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import streamlit as st

# Allow `streamlit run ui/streamlit_app.py` from repo root without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.db import ProductRepository  # noqa: E402
from backend.app.services.discovery import run_discovery  # noqa: E402


st.set_page_config(
    page_title="Affiliate Pipeline Automator",
    page_icon=None,
    layout="wide",
)

st.title("Affiliate Pipeline Automator")
st.caption(
    "Stage 1 — Product discovery for the Brazilian affiliate marketing pipeline. "
    "Scrapes platforms, scores products on commission, ticket, popularity, "
    "reputation, and sales-page signals, and ranks them so you can act on the top picks."
)


@st.cache_resource
def get_repo() -> ProductRepository:
    return ProductRepository()


repo = get_repo()

with st.sidebar:
    st.header("Discovery")
    use_mock = st.toggle(
        "Use mock data",
        value=True,
        help=(
            "On: use a deterministic fixture (recommended for demo). "
            "Off: hit live Hotmart marketplace — may break if selectors drift."
        ),
    )
    limit = st.slider("Products per source", min_value=10, max_value=200, value=50, step=10)
    top_n = st.slider("Show top N", min_value=5, max_value=100, value=25, step=5)

    if st.button("Run discovery", type="primary", use_container_width=True):
        with st.spinner("Scraping and scoring..."):
            result = asyncio.run(
                run_discovery(
                    repo=repo,
                    limit_per_source=limit,
                    top_n=top_n,
                    use_mock=use_mock,
                )
            )
        st.session_state["last_result"] = result
        st.success(
            f"Fetched {result.fetched} products from {', '.join(result.sources)} — "
            f"scored {result.scored}."
        )
        if result.errors:
            for err in result.errors:
                st.warning(err)


tab_top, tab_browse = st.tabs(["Top picks", "Browse persisted catalog"])

with tab_top:
    result = st.session_state.get("last_result")
    if not result:
        st.info("Run discovery from the sidebar to see top picks.")
    else:
        for i, sp in enumerate(result.top, start=1):
            p = sp.product
            s = sp.score
            with st.container(border=True):
                col_main, col_score = st.columns([3, 1])
                with col_main:
                    st.markdown(f"### {i}. {p.name}")
                    st.caption(
                        f"{p.platform.value.title()} · {p.category or 'Uncategorized'} · "
                        f"{p.producer_name or 'Unknown producer'}"
                    )
                    meta_cols = st.columns(4)
                    meta_cols[0].metric("Price", f"R$ {p.price_brl:,.2f}" if p.price_brl else "—")
                    meta_cols[1].metric(
                        "Commission %",
                        f"{p.commission_pct:.0f}%" if p.commission_pct is not None else "—",
                    )
                    meta_cols[2].metric(
                        "Commission R$",
                        f"R$ {p.commission_brl:,.2f}" if p.commission_brl else "—",
                    )
                    meta_cols[3].metric(
                        "EPC",
                        f"R$ {s.expected_value_per_visit:,.4f}",
                        help="Expected value per visit at 2% assumed conversion rate.",
                    )
                    st.markdown(f"[Open product page]({p.url})")
                with col_score:
                    st.metric("Score", f"{s.score:.1f} / 100")
                    with st.expander("Why this score?"):
                        st.write(
                            {
                                "commission": s.components.commission,
                                "ticket": s.components.ticket,
                                "reputation": s.components.reputation,
                                "popularity": s.components.popularity,
                                "sales_signals": s.components.sales_signals,
                            }
                        )

with tab_browse:
    persisted = repo.top_products(limit=200)
    if not persisted:
        st.info("No products persisted yet. Run discovery first.")
    else:
        rows = [
            {
                "Score": round(sp.score.score, 1),
                "Name": sp.product.name,
                "Platform": sp.product.platform.value,
                "Niche": sp.product.niche.value if sp.product.niche else "—",
                "Price (R$)": sp.product.price_brl,
                "Commission %": sp.product.commission_pct,
                "Popularity": sp.product.popularity,
                "EPC": sp.score.expected_value_per_visit,
                "URL": sp.product.url,
            }
            for sp in persisted
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
