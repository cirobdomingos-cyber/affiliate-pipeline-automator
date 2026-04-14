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

from backend.app.db import LinkRepository, ProductRepository  # noqa: E402
from backend.app.models import (  # noqa: E402
    ORGANIC_CHANNELS,
    PAID_CHANNELS,
    LinkStatus,
    Platform,
    TrafficChannel,
    UTMParams,
)
from backend.app.services.discovery import run_discovery  # noqa: E402
from backend.app.services.link_vault import add_link, compose_tracked_url  # noqa: E402


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


@st.cache_resource
def get_link_repo() -> LinkRepository:
    return LinkRepository()


repo = get_repo()
link_repo = get_link_repo()

with st.sidebar:
    st.header("Discovery")
    use_mock = st.toggle(
        "Use mock data",
        value=False,
        help=(
            "On: use fixture data for demos. Mock data is NOT persisted — it "
            "lives in the Top picks tab only. "
            "Off: hit the live sources (Hotmart Next.js hydration + Amazon "
            "PA-API if credentials are set); results are persisted to DuckDB."
        ),
    )
    limit = st.slider("Products per source", min_value=10, max_value=200, value=50, step=10)
    top_n = st.slider("Show top N", min_value=5, max_value=100, value=25, step=5)

    st.divider()
    st.subheader("LLM enrichment (V1)")
    enable_llm = st.toggle(
        "Use Claude to rank by niche fit",
        value=False,
        help=(
            "Calls Sonnet 4.6 to re-rank the catalog by how well each product "
            "fits your stated niche. Requires ANTHROPIC_API_KEY in the environment."
        ),
    )
    target_niche = st.text_input(
        "Target niche",
        value="",
        placeholder="e.g. Digital marketing for first-year affiliates",
        disabled=not enable_llm,
    )
    target_audience = st.text_input(
        "Target audience",
        value="",
        placeholder="e.g. 25-35 year olds in Brazil starting their first side hustle",
        disabled=not enable_llm,
    )

    if st.button("Run discovery", type="primary", use_container_width=True):
        analyzer = None
        if enable_llm and target_niche and target_audience:
            try:
                from backend.app.llm import build_default_analyzer
                analyzer = build_default_analyzer()
            except Exception as e:
                st.error(f"Could not initialize LLM analyzer: {e}")

        with st.spinner("Scraping and scoring..."):
            result = asyncio.run(
                run_discovery(
                    repo=repo,
                    limit_per_source=limit,
                    top_n=top_n,
                    use_mock=use_mock,
                    llm_analyzer=analyzer,
                    target_niche=target_niche if enable_llm else None,
                    target_audience=target_audience if enable_llm else None,
                )
            )
        st.session_state["last_result"] = result
        summary = (
            f"Fetched {result.fetched} from {', '.join(result.sources)} · "
            f"scored {result.scored}"
        )
        if result.llm_signals_filled:
            summary += f" · LLM analyzed {result.llm_signals_filled} sales pages"
        if result.niche_rankings:
            summary += f" · ranked by niche fit ({len(result.niche_rankings)})"
        st.success(summary)
        if result.errors:
            for err in result.errors:
                st.warning(err)

    st.divider()
    st.subheader("Catalog maintenance")
    persisted_count = len(repo.top_products(limit=10_000))
    st.caption(f"{persisted_count} products currently in the persisted catalog.")
    if persisted_count > 0:
        confirm = st.checkbox(
            "I understand this will delete all persisted products and scores",
            key="confirm_clear",
        )
        if st.button(
            "Clear catalog",
            type="secondary",
            use_container_width=True,
            disabled=not confirm,
        ):
            deleted = repo.clear_catalog()
            st.session_state.pop("last_result", None)
            st.success(f"Cleared {deleted} products from the catalog.")
            st.rerun()
    else:
        st.caption(":gray[Nothing to clear.]")


tab_top, tab_browse, tab_analytics, tab_links, tab_traffic = st.tabs(
    ["Top picks", "Browse persisted catalog", "Analytics", "Link Vault", "Traffic Plan"]
)

with tab_top:
    result = st.session_state.get("last_result")
    if not result:
        st.info("Run discovery from the sidebar to see top picks.")
    else:
        niche_fit_by_id = {nf.product_id: nf for nf in result.niche_rankings}
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
                    is_mock = (p.raw or {}).get("source") == "mock_fixture"
                    if is_mock:
                        st.markdown(
                            f"[Demo link ↗]({p.url}) "
                            ":gray[_(mock fixture — points at example.com)_]"
                        )
                    else:
                        st.markdown(f"[Open product page ↗]({p.url})")
                with col_score:
                    st.metric("Score", f"{s.score:.1f} / 100")
                    fit = niche_fit_by_id.get(p.id)
                    if fit:
                        st.metric("Niche fit", f"{fit.fit_score:.0f} / 100")
                        st.caption(fit.reasoning)
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
                    llm_detail = p.raw.get("sales_page_signals_detail") if p.raw else None
                    if llm_detail:
                        with st.expander("LLM sales-page signals"):
                            st.write(llm_detail)

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
        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
            column_config={
                "URL": st.column_config.LinkColumn(
                    "URL",
                    help="Click to open the product page (mock fixture URLs point at example.com)",
                    display_text="Open ↗",
                ),
            },
        )


with tab_analytics:
    st.subheader("Analytics — split by platform")
    st.caption(
        "Every view here is colored by source platform. Use the filters to drill "
        "into one platform or compare niches across all of them."
    )

    import altair as alt  # bundled with Streamlit
    import pandas as pd

    analytics_data = repo.top_products(limit=500)
    if not analytics_data:
        st.info("No products persisted yet. Run discovery from the sidebar first.")
    else:
        df = pd.DataFrame(
            [
                {
                    "Platform": sp.product.platform.value,
                    "Name": sp.product.name,
                    "Niche": sp.product.niche.value if sp.product.niche else "unknown",
                    "Score": sp.score.score,
                    "Price (R$)": sp.product.price_brl,
                    "Commission %": sp.product.commission_pct,
                    "Commission R$": sp.product.commission_brl,
                    "EPC": sp.score.expected_value_per_visit,
                    "Popularity": sp.product.popularity,
                    "Reputation": sp.product.producer_reputation,
                }
                for sp in analytics_data
            ]
        )

        # -------- Filters --------
        fcol1, fcol2, fcol3 = st.columns([2, 2, 1])
        available_platforms = sorted(df["Platform"].unique())
        selected_platforms = fcol1.multiselect(
            "Platforms",
            options=available_platforms,
            default=available_platforms,
            format_func=lambda v: v.title(),
        )
        available_niches = sorted(df["Niche"].unique())
        selected_niches = fcol2.multiselect(
            "Niches",
            options=available_niches,
            default=available_niches,
            format_func=lambda v: v.replace("_", " ").title(),
        )
        min_score = fcol3.slider("Min score", 0, 100, 0, 5)

        filtered = df[
            df["Platform"].isin(selected_platforms)
            & df["Niche"].isin(selected_niches)
            & (df["Score"] >= min_score)
        ]

        if filtered.empty:
            st.warning("No products match the current filters.")
        else:
            # -------- KPI row --------
            kpi_cols = st.columns(4)
            kpi_cols[0].metric("Total products", len(filtered))
            kpi_cols[1].metric("Platforms", filtered["Platform"].nunique())
            kpi_cols[2].metric("Mean score", f"{filtered['Score'].mean():.1f}")
            mean_epc = filtered["EPC"].fillna(0).mean()
            kpi_cols[3].metric("Mean EPC", f"R$ {mean_epc:.4f}")

            # Consistent color mapping across every chart so the platform
            # palette stays stable when the user toggles filters.
            platform_color = alt.Color(
                "Platform:N",
                scale=alt.Scale(scheme="tableau10"),
                legend=alt.Legend(title="Platform"),
            )

            # -------- Chart 1: Product count by platform --------
            st.markdown("#### Product count by platform")
            count_chart = (
                alt.Chart(filtered)
                .mark_bar()
                .encode(
                    x=alt.X("count():Q", title="Products"),
                    y=alt.Y("Platform:N", sort="-x", title=None),
                    color=platform_color,
                    tooltip=["Platform", alt.Tooltip("count():Q", title="Count")],
                )
                .properties(height=min(60 * len(selected_platforms), 300))
            )
            st.altair_chart(count_chart, use_container_width=True)

            # -------- Chart 2: Score distribution by platform --------
            st.markdown("#### Score distribution")
            st.caption(
                "Each dot is one product. Wider spreads mean the scoring model "
                "differentiates products on that platform; tight clusters mean "
                "the signal is compressed (common for live Hotmart when price "
                "and commission are gated behind affiliate login)."
            )
            score_chart = (
                alt.Chart(filtered)
                .mark_circle(size=80, opacity=0.7)
                .encode(
                    x=alt.X("Score:Q", title="Score (0–100)", scale=alt.Scale(domain=[0, 100])),
                    y=alt.Y("Platform:N", title=None),
                    color=platform_color,
                    tooltip=["Name", "Platform", "Niche", "Score", "EPC"],
                )
                .properties(height=min(60 * len(selected_platforms), 300))
            )
            st.altair_chart(score_chart, use_container_width=True)

            # -------- Chart 3: Top 20 products, colored by platform --------
            st.markdown("#### Top 20 products by score")
            top_n = filtered.nlargest(20, "Score")
            top_chart = (
                alt.Chart(top_n)
                .mark_bar()
                .encode(
                    x=alt.X("Score:Q", title="Score"),
                    y=alt.Y(
                        "Name:N",
                        sort=alt.SortField(field="Score", order="descending"),
                        title=None,
                        axis=alt.Axis(labelLimit=320),
                    ),
                    color=platform_color,
                    tooltip=[
                        "Name",
                        "Platform",
                        "Niche",
                        alt.Tooltip("Score:Q", format=".1f"),
                        alt.Tooltip("Price (R$):Q", format=".2f"),
                        alt.Tooltip("Commission %:Q", format=".1f"),
                        alt.Tooltip("EPC:Q", format=".4f"),
                    ],
                )
                .properties(height=min(30 * len(top_n), 600))
            )
            st.altair_chart(top_chart, use_container_width=True)

            # -------- Chart 4: Niche mix per platform --------
            st.markdown("#### Niche mix per platform")
            st.caption(
                "Where each platform's catalog sits in your niche taxonomy. "
                "A single platform heavy on one niche is easier to specialize in; "
                "a platform with broad coverage is better for diversification."
            )
            niche_chart = (
                alt.Chart(filtered)
                .mark_bar()
                .encode(
                    x=alt.X("count():Q", title="Products", stack="normalize"),
                    y=alt.Y("Platform:N", title=None),
                    color=alt.Color(
                        "Niche:N",
                        scale=alt.Scale(scheme="category10"),
                        legend=alt.Legend(title="Niche"),
                    ),
                    tooltip=[
                        "Platform",
                        "Niche",
                        alt.Tooltip("count():Q", title="Count"),
                    ],
                )
                .properties(height=min(60 * len(selected_platforms), 300))
            )
            st.altair_chart(niche_chart, use_container_width=True)


with tab_links:
    st.subheader("Stage 2 — Affiliate Link Vault")
    st.caption(
        "Central store for affiliate links. Track approval status per platform "
        "and build tracked URLs with normalized UTM parameters."
    )

    col_add, col_filter = st.columns([2, 1])

    with col_add:
        with st.expander("Add a new affiliate link", expanded=False):
            with st.form("add_link_form", clear_on_submit=True):
                label = st.text_input("Label", placeholder="e.g. Curso X — Instagram bio")
                raw_url = st.text_input("Affiliate URL", placeholder="https://hotmart.com/...")
                new_platform = st.selectbox(
                    "Platform",
                    options=[p.value for p in Platform],
                    format_func=lambda v: v.title(),
                )
                new_status = st.selectbox(
                    "Approval status",
                    options=[s.value for s in LinkStatus],
                    format_func=lambda v: v.title(),
                )
                notes = st.text_area("Notes", placeholder="Producer approval date, restrictions, etc.")
                tags_raw = st.text_input("Tags (comma-separated)", placeholder="instagram, bio, organic")
                submitted = st.form_submit_button("Save link", type="primary")
                if submitted:
                    if not label or not raw_url:
                        st.error("Label and URL are required.")
                    else:
                        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
                        add_link(
                            link_repo,
                            platform=Platform(new_platform),
                            label=label,
                            raw_url=raw_url,
                            notes=notes,
                            tags=tags,
                            approval_status=LinkStatus(new_status),
                        )
                        st.success(f"Saved: {label}")
                        st.rerun()

    with col_filter:
        status_filter = st.selectbox(
            "Filter by status",
            options=["(all)"] + [s.value for s in LinkStatus],
            format_func=lambda v: v.title() if v != "(all)" else "All statuses",
        )
        platform_filter = st.selectbox(
            "Filter by platform",
            options=["(all)"] + [p.value for p in Platform],
            format_func=lambda v: v.title() if v != "(all)" else "All platforms",
        )

    status_arg = LinkStatus(status_filter) if status_filter != "(all)" else None
    platform_arg = Platform(platform_filter) if platform_filter != "(all)" else None
    links = link_repo.list(status=status_arg, platform=platform_arg)

    if not links:
        st.info("No links in the vault yet. Add one above to get started.")
    else:
        for link in links:
            status_color = {
                LinkStatus.PENDING: ":orange[Pending]",
                LinkStatus.APPROVED: ":green[Approved]",
                LinkStatus.REJECTED: ":red[Rejected]",
                LinkStatus.EXPIRED: ":gray[Expired]",
            }[link.approval_status]

            with st.container(border=True):
                row_main, row_status = st.columns([3, 1])
                with row_main:
                    st.markdown(f"**{link.label}** · {link.platform.value.title()}")
                    st.caption(f"{link.raw_url}")
                    if link.tags:
                        st.caption("Tags: " + ", ".join(f"`{t}`" for t in link.tags))
                    if link.notes:
                        st.caption(link.notes)
                with row_status:
                    st.markdown(status_color)
                    new_status_val = st.selectbox(
                        "Status",
                        options=[s.value for s in LinkStatus],
                        index=list(LinkStatus).index(link.approval_status),
                        key=f"status_{link.id}",
                        label_visibility="collapsed",
                    )
                    if new_status_val != link.approval_status.value:
                        link_repo.set_status(link.id, LinkStatus(new_status_val))
                        st.rerun()

                with st.expander("Build tracked URL"):
                    with st.form(f"utm_form_{link.id}"):
                        c1, c2, c3 = st.columns(3)
                        utm_source = c1.text_input("utm_source", value="instagram", key=f"src_{link.id}")
                        utm_medium = c2.text_input("utm_medium", value="organic", key=f"med_{link.id}")
                        utm_campaign = c3.text_input("utm_campaign", value="bio-link", key=f"camp_{link.id}")
                        c4, c5 = st.columns(2)
                        utm_term = c4.text_input("utm_term (optional)", key=f"term_{link.id}")
                        utm_content = c5.text_input("utm_content (optional)", key=f"cont_{link.id}")
                        build = st.form_submit_button("Build")
                        if build:
                            utm = UTMParams(
                                source=utm_source,
                                medium=utm_medium,
                                campaign=utm_campaign,
                                term=utm_term or None,
                                content=utm_content or None,
                            )
                            tracked = compose_tracked_url(link, utm)
                            st.code(tracked.final_url, language="text")
                            st.caption("Values are normalized automatically — 'Instagram Bio' → 'instagram-bio'.")


with tab_traffic:
    st.subheader("Stage 3 — Traffic Plan")
    st.caption(
        "Generate an organic content calendar (Sonnet) and paid ad variants (Haiku) "
        "for a product from your catalog. Requires ANTHROPIC_API_KEY."
    )

    persisted_products = repo.top_products(limit=100)
    if not persisted_products:
        st.info("No products persisted yet. Run discovery first.")
    else:
        product_labels = {
            sp.product.id: f"{sp.product.name} · {sp.product.platform.value} · R${sp.product.price_brl or 0:.0f}"
            for sp in persisted_products
        }
        selected_id = st.selectbox(
            "Pick a product",
            options=list(product_labels.keys()),
            format_func=lambda pid: product_labels[pid],
        )
        selected_product = next(
            sp.product for sp in persisted_products if sp.product.id == selected_id
        )

        tp_audience = st.text_input(
            "Target audience",
            placeholder="e.g. First-year affiliates in Brazil, 25–35, struggling to make their first sale",
        )

        organic_col, paid_col = st.columns(2)

        with organic_col:
            st.markdown("**Organic plan (Sonnet 4.6)**")
            organic_channels = st.multiselect(
                "Organic channels",
                options=[c.value for c in ORGANIC_CHANNELS],
                default=[TrafficChannel.INSTAGRAM_REEL.value, TrafficChannel.TIKTOK.value],
                format_func=lambda v: v.replace("_", " ").title(),
            )
            plan_days = st.slider("Plan horizon (days)", 3, 30, 7)
            if st.button("Generate organic plan", type="primary", use_container_width=True):
                if not tp_audience:
                    st.error("Target audience is required.")
                elif not organic_channels:
                    st.error("Pick at least one channel.")
                else:
                    try:
                        from backend.app.traffic_planner import build_default_traffic_planner
                        planner = build_default_traffic_planner()
                        with st.spinner("Sonnet is writing your content plan..."):
                            plan = planner.plan_organic(
                                product=selected_product,
                                target_audience=tp_audience,
                                channels=[TrafficChannel(c) for c in organic_channels],
                                days=plan_days,
                            )
                        st.session_state["last_organic_plan"] = plan
                    except Exception as e:
                        st.error(f"Organic plan failed: {e}")

        with paid_col:
            st.markdown("**Paid ad variants (Haiku 4.5)**")
            ad_platform = st.selectbox(
                "Ad platform",
                options=[c.value for c in PAID_CHANNELS],
                format_func=lambda v: v.replace("_", " ").title(),
            )
            variant_count = st.slider("Number of variants", 3, 10, 5)
            if st.button("Generate ad variants", type="primary", use_container_width=True):
                if not tp_audience:
                    st.error("Target audience is required.")
                else:
                    try:
                        from backend.app.traffic_planner import build_default_traffic_planner
                        planner = build_default_traffic_planner()
                        with st.spinner("Haiku is writing ad copy variants..."):
                            variants = planner.generate_ad_variants(
                                product=selected_product,
                                target_audience=tp_audience,
                                platform=TrafficChannel(ad_platform),
                                count=variant_count,
                            )
                        st.session_state["last_ad_variants"] = variants
                    except Exception as e:
                        st.error(f"Ad variant generation failed: {e}")

        plan = st.session_state.get("last_organic_plan")
        if plan:
            st.divider()
            st.markdown("### Organic plan")
            st.markdown(f"**Target audience:** {plan.target_audience}")
            st.markdown(f"**Positioning:** {plan.positioning}")
            st.markdown(f"**Posting rhythm:** {plan.posting_rhythm}")
            st.markdown("**Key messages:**")
            for msg in plan.key_messages:
                st.markdown(f"- {msg}")
            st.markdown(f"**Content calendar ({len(plan.briefs)} posts)**")
            for brief in plan.briefs:
                with st.container(border=True):
                    st.markdown(
                        f"**Day {brief.day_offset} · {brief.channel.value.replace('_', ' ').title()}**"
                    )
                    st.markdown(f"**Hook:** {brief.hook}")
                    st.markdown(f"**Body:** {brief.body}")
                    st.markdown(f"**CTA:** {brief.call_to_action}")
                    if brief.hashtags:
                        st.caption(" ".join(f"#{h}" for h in brief.hashtags))
                    if brief.format_notes:
                        st.caption(f"Format: {brief.format_notes}")

        variants = st.session_state.get("last_ad_variants")
        if variants:
            st.divider()
            st.markdown("### Paid ad variants")
            for i, v in enumerate(variants, start=1):
                with st.container(border=True):
                    st.markdown(f"**Variant {i} · {v.platform.value.replace('_', ' ').title()}**")
                    st.markdown(f"**Headline:** {v.headline}")
                    st.markdown(f"**Primary text:** {v.primary_text}")
                    st.markdown(f"**Description:** {v.description}")
                    st.caption(f"Audience: {v.target_audience}")
                    st.caption(f"Daily budget: R$ {v.daily_budget_brl:.0f}")
                    if v.creative_notes:
                        st.caption(f"Creative: {v.creative_notes}")
