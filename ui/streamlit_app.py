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
        value=True,
        help=(
            "On: use a deterministic fixture (recommended for demo). "
            "Off: hit live Hotmart marketplace — may break if selectors drift."
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


tab_top, tab_browse, tab_links, tab_traffic = st.tabs(
    ["Top picks", "Browse persisted catalog", "Link Vault", "Traffic Plan"]
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
                    st.markdown(f"[Open product page]({p.url})")
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
        st.dataframe(rows, use_container_width=True, hide_index=True)


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
