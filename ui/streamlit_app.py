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
import os
import sys
from pathlib import Path

import streamlit as st

# Allow `streamlit run ui/streamlit_app.py` from repo root without install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uuid  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from backend.app.db import (  # noqa: E402
    BridgePageRepository,
    ChannelConfigRepository,
    ClickEventRepository,
    EmailSequenceRepository,
    GeneratedCreativeRepository,
    LinkRepository,
    ManagedProductRepository,
    OperatorProfileRepository,
    ProductRepository,
    ShortLinkRepository,
    SubscriberCountRepository,
)
from backend.app.models import (  # noqa: E402
    ORGANIC_CHANNELS,
    PAID_CHANNELS,
    BridgePage,
    ChannelConfig,
    ChannelStatus,
    EmailSequence,
    EmailSequenceStep,
    LinkStatus,
    ManagedProduct,
    Niche,
    OperatorProfile,
    Platform,
    ShortLink,
    TrafficChannel,
    UTMParams,
)
from backend.app.scoring import score_managed_product  # noqa: E402
from backend.app.services.discovery import run_discovery  # noqa: E402
from backend.app.services.link_vault import add_link, compose_tracked_url  # noqa: E402
from backend.app.services.shortener import compose_destination, generate_slug  # noqa: E402


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


@st.cache_resource
def get_managed_repo() -> ManagedProductRepository:
    return ManagedProductRepository()


@st.cache_resource
def get_profile_repo() -> OperatorProfileRepository:
    return OperatorProfileRepository()


@st.cache_resource
def get_short_repo() -> ShortLinkRepository:
    return ShortLinkRepository()


@st.cache_resource
def get_click_repo() -> ClickEventRepository:
    return ClickEventRepository()


@st.cache_resource
def get_channel_repo() -> ChannelConfigRepository:
    return ChannelConfigRepository()


@st.cache_resource
def get_bridge_repo() -> BridgePageRepository:
    return BridgePageRepository()


@st.cache_resource
def get_seq_repo() -> EmailSequenceRepository:
    return EmailSequenceRepository()


@st.cache_resource
def get_sub_repo() -> SubscriberCountRepository:
    return SubscriberCountRepository()


@st.cache_resource
def get_creative_repo() -> GeneratedCreativeRepository:
    return GeneratedCreativeRepository()


repo = get_repo()
link_repo = get_link_repo()
managed_repo = get_managed_repo()
profile_repo = get_profile_repo()
short_repo = get_short_repo()
click_repo = get_click_repo()
channel_repo = get_channel_repo()
bridge_repo = get_bridge_repo()
seq_repo = get_seq_repo()
sub_repo = get_sub_repo()
creative_repo = get_creative_repo()


APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000")

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


phase1, phase2, phase3, phase4, phase5, phase6, phase7 = st.tabs(
    [
        "Phase 1 — Discovery",
        "Phase 2 — Products & Links",
        "Phase 3 — Traffic Planning",
        "Phase 4 — Bridge Pages",
        "Phase 5 — Email",
        "Phase 6 — KPIs",
        "Phase 7 — Scale",
    ]
)

with phase1:
    tab_onboarding, tab_top, tab_browse, tab_analytics = st.tabs(
        ["Onboarding", "Top picks", "Browse persisted catalog", "Analytics"]
    )

with phase2:
    st.caption(
        "Manual catalog + tracked short links. Channel configs live inside "
        "each product card (scroll to **Traffic channels** under a product)."
    )
    tab_managed, tab_links = st.tabs(["My Products", "Legacy link vault"])

tab_traffic = phase3
tab_bridges = phase4
tab_email = phase5
tab_kpis = phase6
tab_scale = phase7


_NICHE_LABELS = {
    Niche.FINANCE: "Finance",
    Niche.DIGITAL_MARKETING: "Digital marketing",
    Niche.HEALTH: "Health",
    Niche.TECH_SAAS: "Tech / SaaS",
    Niche.BUSINESS: "Business",
    Niche.OTHER: "Other",
}


with tab_onboarding:
    st.subheader("Operator onboarding")
    st.caption("Pick the niche you're building a portfolio around. Drives defaults in every other tab.")
    current_profile = profile_repo.get()
    if current_profile:
        st.success(
            f"Primary niche: **{_NICHE_LABELS[current_profile.primary_niche]}** "
            f"(set {current_profile.completed_at.strftime('%Y-%m-%d %H:%M')})"
        )
    with st.form("onboarding_form"):
        default_idx = (
            list(Niche).index(current_profile.primary_niche) if current_profile else 0
        )
        chosen = st.selectbox(
            "Primary niche",
            options=list(Niche),
            index=default_idx,
            format_func=lambda n: _NICHE_LABELS[n],
        )
        if st.form_submit_button("Save", type="primary"):
            profile_repo.save(OperatorProfile(primary_niche=chosen))
            st.success("Saved.")
            st.rerun()


with tab_managed:
    st.subheader("My products")
    st.caption(
        "Hand-curated catalog — the products you're actively promoting. "
        "Auto-scored on commission (40%), ticket (30%), and platform trust (30%). "
        "Score > 70 earns a **Recommended** badge."
    )

    _stale_alerts = []
    _name_by_id = {m.id: m.name for m in managed_repo.list()}
    for _cfg in channel_repo.list_all():
        if _cfg.status != ChannelStatus.ACTIVE or _cfg.channel not in PAID_CHANNELS:
            continue
        if click_repo.recent_count(_cfg.managed_product_id, hours=24) == 0:
            _stale_alerts.append(
                (_name_by_id.get(_cfg.managed_product_id, "?"), _cfg.channel.value, _cfg.daily_budget_brl)
            )
    if _stale_alerts:
        st.warning(
            "⚠ Paid channels active with **0 clicks in 24h**:\n\n"
            + "\n".join(
                f"- **{name}** · {ch}"
                + (f" · R$ {budget:.0f}/day" if budget else "")
                for name, ch, budget in _stale_alerts
            )
        )

    with st.expander("Add a new product", expanded=False):
        with st.form("new_managed_product"):
            col1, col2 = st.columns(2)
            with col1:
                mp_name = st.text_input("Product name", placeholder="e.g. Curso SEO Black")
                mp_platform = st.selectbox(
                    "Platform",
                    options=list(Platform),
                    format_func=lambda p: p.value.title(),
                )
                mp_niche = st.selectbox(
                    "Niche",
                    options=list(Niche),
                    format_func=lambda n: _NICHE_LABELS[n],
                    index=(
                        list(Niche).index(current_profile.primary_niche)
                        if current_profile
                        else 0
                    ),
                )
                mp_commission = st.number_input(
                    "Commission %", min_value=0.0, max_value=100.0, value=50.0, step=1.0
                )
            with col2:
                mp_ticket = st.number_input(
                    "Average ticket (R$)", min_value=0.0, value=297.0, step=10.0
                )
                mp_sales_url = st.text_input("Sales page URL", placeholder="https://...")
                mp_aff_url = st.text_input("Affiliate URL", placeholder="https://...")
                mp_notes = st.text_area("Notes", value="", height=68)

            if st.form_submit_button("Create product", type="primary"):
                if not (mp_name and mp_aff_url):
                    st.error("Name and affiliate URL are required.")
                else:
                    now = datetime.now(timezone.utc)
                    mp = ManagedProduct(
                        id=str(uuid.uuid4()),
                        name=mp_name,
                        platform=mp_platform,
                        niche=mp_niche,
                        commission_pct=mp_commission,
                        ticket_brl=mp_ticket,
                        sales_page_url=mp_sales_url or None,
                        affiliate_url=mp_aff_url,
                        quality_score=score_managed_product(
                            commission_pct=mp_commission,
                            ticket_brl=mp_ticket,
                            platform=mp_platform,
                        ),
                        notes=mp_notes,
                        created_at=now,
                        updated_at=now,
                    )
                    managed_repo.upsert(mp)
                    st.success(f"Created — quality score {mp.quality_score:.1f}")
                    st.rerun()

    managed_list = managed_repo.list()
    if not managed_list:
        st.info("No managed products yet. Add one above.")
    else:
        for mp in managed_list:
            with st.container(border=True):
                header_cols = st.columns([3, 1, 1])
                with header_cols[0]:
                    title = f"### {mp.name}"
                    if mp.recommended:
                        title += "  :green-badge[Recommended]"
                    st.markdown(title)
                    st.caption(
                        f"{mp.platform.value.title()} · {_NICHE_LABELS[mp.niche]} · "
                        f"R$ {mp.ticket_brl:,.2f} · {mp.commission_pct:.0f}% commission"
                    )
                with header_cols[1]:
                    st.metric("Quality score", f"{mp.quality_score:.1f}")
                with header_cols[2]:
                    if st.button("Delete", key=f"del_{mp.id}", type="secondary"):
                        managed_repo.delete(mp.id)
                        st.rerun()
                if mp.affiliate_url:
                    st.markdown(f"[Affiliate link ↗]({mp.affiliate_url})")
                if mp.notes:
                    st.caption(mp.notes)

                with st.expander("Tracked short links"):
                    existing = short_repo.list(managed_product_id=mp.id)
                    if existing:
                        for sl in existing:
                            stats = short_repo.stats(sl.slug)
                            col_a, col_b, col_c = st.columns([3, 1, 1])
                            with col_a:
                                url = f"{APP_BASE_URL}/r/{sl.slug}"
                                st.code(url, language=None)
                                st.caption(
                                    f"→ {sl.destination_url[:80]}{'…' if len(sl.destination_url) > 80 else ''}"
                                )
                            with col_b:
                                st.metric("Clicks", stats.total_clicks)
                            with col_c:
                                if st.button("Delete", key=f"delsl_{sl.slug}"):
                                    short_repo.delete(sl.slug)
                                    st.rerun()
                    else:
                        st.caption(":gray[No tracked links yet.]")

                    with st.form(f"newsl_{mp.id}"):
                        st.markdown("**New tracked link**")
                        utm_cols = st.columns(3)
                        new_src = utm_cols[0].text_input(
                            "utm_source", placeholder="instagram", key=f"src_{mp.id}"
                        )
                        new_med = utm_cols[1].text_input(
                            "utm_medium", placeholder="bio", key=f"med_{mp.id}"
                        )
                        new_cmp = utm_cols[2].text_input(
                            "utm_campaign", placeholder="launch-abril", key=f"cmp_{mp.id}"
                        )
                        if st.form_submit_button("Create tracked link"):
                            slug = generate_slug()
                            dest = compose_destination(
                                affiliate_url=mp.affiliate_url,
                                utm_source=new_src or None,
                                utm_medium=new_med or None,
                                utm_campaign=new_cmp or None,
                            )
                            short_repo.upsert(
                                ShortLink(
                                    slug=slug,
                                    managed_product_id=mp.id,
                                    destination_url=dest,
                                    utm_source=new_src or None,
                                    utm_medium=new_med or None,
                                    utm_campaign=new_cmp or None,
                                )
                            )
                            st.success(f"Created: {APP_BASE_URL}/r/{slug}")
                            st.rerun()

                with st.expander("Traffic channels"):
                    existing_channels = {
                        c.channel: c for c in channel_repo.list_for_product(mp.id)
                    }
                    st.caption(
                        f"{len(existing_channels)} configured · "
                        f"{sum(1 for c in existing_channels.values() if c.status == ChannelStatus.ACTIVE)} active"
                    )
                    for cfg in existing_channels.values():
                        row = st.columns([3, 1, 1, 1])
                        is_paid = cfg.channel in PAID_CHANNELS
                        row[0].markdown(
                            f"**{cfg.channel.value}** "
                            f"{':green-badge[active]' if cfg.status == ChannelStatus.ACTIVE else ':gray-badge[paused]'}"
                            f"{' · :orange-badge[paid]' if is_paid else ' · :blue-badge[organic]'}"
                        )
                        row[1].caption(
                            f"R$ {cfg.daily_budget_brl:.0f}/day" if cfg.daily_budget_brl else "—"
                        )
                        row[2].caption(
                            f"goal {cfg.daily_click_goal}/day" if cfg.daily_click_goal else "—"
                        )
                        if row[3].button(
                            "Pause" if cfg.status == ChannelStatus.ACTIVE else "Activate",
                            key=f"togglech_{cfg.id}",
                        ):
                            cfg.status = (
                                ChannelStatus.PAUSED
                                if cfg.status == ChannelStatus.ACTIVE
                                else ChannelStatus.ACTIVE
                            )
                            cfg.updated_at = datetime.now(timezone.utc)
                            channel_repo.upsert(cfg)
                            st.rerun()

                    with st.form(f"newch_{mp.id}"):
                        st.markdown("**Add channel**")
                        ch_cols = st.columns([2, 1, 1])
                        available = [
                            ch for ch in TrafficChannel if ch not in existing_channels
                        ]
                        if not available:
                            st.caption(":gray[All channels configured.]")
                            st.form_submit_button("Add", disabled=True)
                        else:
                            new_ch = ch_cols[0].selectbox(
                                "Channel",
                                options=available,
                                format_func=lambda c: c.value,
                                key=f"newchsel_{mp.id}",
                            )
                            new_budget = ch_cols[1].number_input(
                                "Daily budget R$",
                                min_value=0.0,
                                value=0.0,
                                step=10.0,
                                key=f"newchbud_{mp.id}",
                            )
                            new_goal = ch_cols[2].number_input(
                                "Clicks/day goal",
                                min_value=0,
                                value=0,
                                step=5,
                                key=f"newchgoal_{mp.id}",
                            )
                            if st.form_submit_button("Add channel"):
                                is_paid = new_ch in PAID_CHANNELS
                                cfg = ChannelConfig(
                                    id=str(uuid.uuid4()),
                                    managed_product_id=mp.id,
                                    channel=new_ch,
                                    status=ChannelStatus.ACTIVE,
                                    daily_budget_brl=new_budget if (is_paid and new_budget > 0) else None,
                                    daily_click_goal=new_goal if new_goal > 0 else None,
                                )
                                channel_repo.upsert(cfg)
                                st.rerun()

with tab_bridges:
    st.subheader("Bridge pages")
    st.caption(
        "Standalone pre-sell pages served at `/bp/{slug}` — no header, no footer, "
        "mobile-first. CTA clicks are logged and attributed to the product for "
        "conversion tracking."
    )

    _managed_for_bridge = managed_repo.list()
    if not _managed_for_bridge:
        st.info("Create a managed product first (My Products tab).")
    else:
        with st.expander("Create new bridge page", expanded=False):
            with st.form("new_bridge"):
                bp_product = st.selectbox(
                    "Managed product",
                    options=_managed_for_bridge,
                    format_func=lambda m: m.name,
                )
                bp_headline = st.text_input(
                    "Headline", placeholder="Como dobrar sua renda em 90 dias"
                )
                bp_sub = st.text_input(
                    "Subheadline", placeholder="Método testado por mais de 10.000 alunos"
                )
                bp_bullets_raw = st.text_area(
                    "Bullets (one per line, up to 5)",
                    placeholder="Benefício 1\nBenefício 2\nBenefício 3",
                    height=100,
                )
                bp_cta_text = st.text_input("CTA text", value="Quero saber mais")
                bp_cta_url = st.text_input(
                    "CTA destination URL (usually the affiliate or short link)",
                    value=bp_product.affiliate_url if bp_product else "",
                )
                bp_color = st.color_picker("Primary color", value="#2563eb")
                if st.form_submit_button("Create", type="primary"):
                    if not (bp_headline and bp_cta_text and bp_cta_url):
                        st.error("Headline, CTA text, and CTA URL are required.")
                    else:
                        bullets = [
                            b.strip() for b in bp_bullets_raw.splitlines() if b.strip()
                        ][:5]
                        slug = generate_slug()
                        page = BridgePage(
                            slug=slug,
                            managed_product_id=bp_product.id,
                            headline=bp_headline,
                            subheadline=bp_sub or None,
                            bullets=bullets,
                            cta_text=bp_cta_text,
                            cta_url=bp_cta_url,
                            primary_color=bp_color,
                        )
                        bridge_repo.upsert(page)
                        st.success(f"Created: {APP_BASE_URL}/bp/{slug}")
                        st.rerun()

        pages = bridge_repo.list()
        if not pages:
            st.info("No bridge pages yet.")
        else:
            for page in pages:
                mp = managed_repo.get(page.managed_product_id)
                mp_name = mp.name if mp else "(deleted product)"
                with st.container(border=True):
                    head_cols = st.columns([3, 1, 1])
                    with head_cols[0]:
                        st.markdown(f"### {page.headline}")
                        st.caption(
                            f"for **{mp_name}** · /bp/{page.slug} · "
                            f"updated {page.updated_at.strftime('%Y-%m-%d %H:%M')}"
                        )
                    with head_cols[1]:
                        cta_clicks = sum(
                            1
                            for _ in range(1)
                            if click_repo.count_for_product(
                                page.managed_product_id, target_type="bridge_cta"
                            )
                        )
                        # actual number:
                        n_cta = click_repo.count_for_product(
                            page.managed_product_id, target_type="bridge_cta"
                        )
                        st.metric("CTA clicks", n_cta)
                    with head_cols[2]:
                        if st.button("Delete", key=f"delbp_{page.slug}"):
                            bridge_repo.delete(page.slug)
                            st.rerun()
                    st.code(f"{APP_BASE_URL}/bp/{page.slug}", language=None)
                    if page.bullets:
                        st.caption(" · ".join(page.bullets))

with tab_email:
    st.subheader("Email lists & nurture sequences")
    st.caption(
        "Copy the embeddable form snippet into any external site. On opt-in "
        "the subscriber is pushed to MailerLite and assigned to the group "
        "attached to that product's nurture sequence."
    )

    _managed_for_email = managed_repo.list()
    if not _managed_for_email:
        st.info("Create a managed product first (My Products tab).")
    else:
        if st.button("Sync subscriber counts from MailerLite"):
            from backend.app.services.mailerlite import (
                MailerLiteError,
                group_subscriber_count,
            )

            synced = 0
            errors = 0
            for mp in _managed_for_email:
                seqs = seq_repo.list(managed_product_id=mp.id)
                gid = next((s.mailerlite_group_id for s in seqs if s.mailerlite_group_id), None)
                if not gid:
                    continue
                try:
                    count = group_subscriber_count(gid)
                    sub_repo.save(
                        __import__(
                            "backend.app.models", fromlist=["SubscriberCountSnapshot"]
                        ).SubscriberCountSnapshot(
                            managed_product_id=mp.id,
                            count=count,
                        )
                    )
                    synced += 1
                except MailerLiteError as exc:
                    errors += 1
                    st.warning(f"{mp.name}: {exc}")
            st.success(f"Synced {synced} product(s). {errors} error(s).")
            st.rerun()

        for mp in _managed_for_email:
            with st.container(border=True):
                head = st.columns([3, 1])
                with head[0]:
                    st.markdown(f"### {mp.name}")
                    st.caption(_NICHE_LABELS[mp.niche])
                with head[1]:
                    snap = sub_repo.get(mp.id)
                    st.metric(
                        "Subscribers",
                        snap.count if snap else 0,
                        help=(
                            f"Synced {snap.synced_at:%Y-%m-%d %H:%M}" if snap else "Not yet synced"
                        ),
                    )

                with st.expander("Embed form snippet"):
                    st.caption(
                        "Paste this HTML into any external site to collect opt-ins "
                        "for this product. The iframe is self-contained (no JS)."
                    )
                    snippet = (
                        f'<iframe src="{APP_BASE_URL}/mailerlite/embed/{mp.id}" '
                        f'width="420" height="340" style="border:1px solid #e5e7eb;'
                        f'border-radius:8px" title="Opt-in"></iframe>'
                    )
                    st.code(snippet, language="html")

                with st.expander("Nurture sequence"):
                    existing_seqs = seq_repo.list(managed_product_id=mp.id)
                    current = existing_seqs[0] if existing_seqs else None

                    with st.form(f"seq_form_{mp.id}"):
                        seq_name = st.text_input(
                            "Sequence name",
                            value=current.name if current else f"{mp.name} nurture",
                            key=f"seqname_{mp.id}",
                        )
                        ml_group = st.text_input(
                            "MailerLite group ID (required for opt-in to work)",
                            value=current.mailerlite_group_id if current else "",
                            key=f"seqgroup_{mp.id}",
                        )
                        num_steps = st.slider(
                            "Number of emails",
                            min_value=0,
                            max_value=7,
                            value=len(current.steps) if current else 3,
                            key=f"seqn_{mp.id}",
                        )
                        steps_data = []
                        for i in range(num_steps):
                            existing_step = (
                                current.steps[i]
                                if current and i < len(current.steps)
                                else None
                            )
                            st.markdown(f"**Email {i + 1}**")
                            c1, c2 = st.columns([1, 3])
                            delay = c1.number_input(
                                "Delay (days)",
                                min_value=0,
                                value=existing_step.delay_days if existing_step else (0 if i == 0 else 1),
                                key=f"delay_{mp.id}_{i}",
                            )
                            subj = c2.text_input(
                                "Subject",
                                value=existing_step.subject if existing_step else "",
                                key=f"subj_{mp.id}_{i}",
                            )
                            body = st.text_area(
                                "Body",
                                value=existing_step.body if existing_step else "",
                                height=100,
                                key=f"body_{mp.id}_{i}",
                            )
                            steps_data.append((delay, subj, body))

                        if st.form_submit_button(
                            "Save sequence" if current else "Create sequence",
                            type="primary",
                        ):
                            seq = EmailSequence(
                                id=current.id if current else str(uuid.uuid4()),
                                managed_product_id=mp.id,
                                name=seq_name,
                                mailerlite_group_id=ml_group or None,
                                steps=[
                                    EmailSequenceStep(
                                        id=str(uuid.uuid4()),
                                        sequence_id=current.id if current else "tmp",
                                        step_order=i,
                                        delay_days=d,
                                        subject=s,
                                        body=b,
                                    )
                                    for i, (d, s, b) in enumerate(steps_data)
                                ],
                                created_at=current.created_at if current else datetime.now(timezone.utc),
                            )
                            seq_repo.upsert(seq)
                            st.success("Saved.")
                            st.rerun()

with tab_kpis:
    st.subheader("KPIs by product")
    st.caption(
        "Ranked by **EPC actual** (descending). Products without EPC set fall "
        "to the bottom — enter them in the My Products tab after you see real "
        "commissions."
    )
    _kpi_products = managed_repo.list()
    if not _kpi_products:
        st.info("No managed products yet.")
    else:
        subs_all = sub_repo.all()
        rows = []
        for mp in _kpi_products:
            aff = click_repo.count_for_product(mp.id, target_type="short_link")
            views = click_repo.count_for_product(mp.id, target_type="bridge_view")
            cta = click_repo.count_for_product(mp.id, target_type="bridge_cta")
            conv = (cta / views) if views else 0.0
            rows.append(
                {
                    "Product": mp.name,
                    "Niche": _NICHE_LABELS[mp.niche],
                    "Affiliate clicks": aff,
                    "Bridge views": views,
                    "Bridge CTA": cta,
                    "Bridge CVR": f"{conv * 100:.1f}%",
                    "Subscribers": subs_all.get(mp.id, 0),
                    "EPC (R$)": mp.epc_actual if mp.epc_actual is not None else None,
                    "CPV (R$)": mp.cpv_actual if mp.cpv_actual is not None else None,
                    "_id": mp.id,
                }
            )
        rows.sort(
            key=lambda r: (r["EPC (R$)"] is None, -(r["EPC (R$)"] or 0.0))
        )
        st.dataframe(
            [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
            use_container_width=True,
            hide_index=True,
        )

        st.divider()
        st.subheader("Clicks per day (last 30 days)")
        product_choice = st.selectbox(
            "Product",
            options=_kpi_products,
            format_func=lambda m: m.name,
            key="kpi_prod",
        )
        if product_choice:
            aff_ts = dict(
                click_repo.daily_timeseries(
                    product_choice.id, days=30, target_type="short_link"
                )
            )
            views_ts = dict(
                click_repo.daily_timeseries(
                    product_choice.id, days=30, target_type="bridge_view"
                )
            )
            cta_ts = dict(
                click_repo.daily_timeseries(
                    product_choice.id, days=30, target_type="bridge_cta"
                )
            )
            all_dates = sorted(set(aff_ts) | set(views_ts) | set(cta_ts))
            if all_dates:
                chart_rows = [
                    {
                        "date": d,
                        "Affiliate clicks": aff_ts.get(d, 0),
                        "Bridge views": views_ts.get(d, 0),
                        "Bridge CTA": cta_ts.get(d, 0),
                    }
                    for d in all_dates
                ]
                st.line_chart(chart_rows, x="date", use_container_width=True)
            else:
                st.caption(":gray[No click data yet for this product.]")

        st.divider()
        st.subheader("Edit EPC / CPV")
        st.caption(
            "Manual inputs — enter once you've actually seen commissions come in."
        )
        for mp in _kpi_products:
            with st.form(f"epc_{mp.id}"):
                c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                c1.markdown(f"**{mp.name}**")
                new_epc = c2.number_input(
                    "EPC",
                    min_value=0.0,
                    value=float(mp.epc_actual) if mp.epc_actual else 0.0,
                    step=0.01,
                    key=f"epc_in_{mp.id}",
                )
                new_cpv = c3.number_input(
                    "CPV",
                    min_value=0.0,
                    value=float(mp.cpv_actual) if mp.cpv_actual else 0.0,
                    step=0.01,
                    key=f"cpv_in_{mp.id}",
                )
                if c4.form_submit_button("Save"):
                    mp.epc_actual = new_epc if new_epc > 0 else None
                    mp.cpv_actual = new_cpv if new_cpv > 0 else None
                    mp.updated_at = datetime.now(timezone.utc)
                    managed_repo.upsert(mp)
                    st.rerun()

with tab_scale:
    st.subheader("Scale readiness")
    st.caption(
        "Per-product checklist. A product is ready to scale when every gate "
        "passes — until then, the suggestions below tell you exactly what's "
        "missing."
    )
    _scale_products = managed_repo.list()
    if not _scale_products:
        st.info("No managed products yet.")
    else:
        from backend.app.api.scale import (  # noqa: E402  (lazy import — avoids cost on other tabs)
            scale_readiness as _compute_readiness,
        )

        for mp in _scale_products:
            readiness = _compute_readiness(
                product_id=mp.id,
                managed_repo=managed_repo,
                short_repo=short_repo,
                bridge_repo=bridge_repo,
                channel_repo=channel_repo,
                seq_repo=seq_repo,
                click_repo=click_repo,
            )
            with st.container(border=True):
                head = st.columns([3, 1])
                with head[0]:
                    badge = (
                        ":green-badge[READY TO SCALE]"
                        if readiness.ready_to_scale
                        else ":orange-badge[Not ready]"
                    )
                    st.markdown(f"### {mp.name} {badge}")
                    passed = sum(1 for item in readiness.checklist if item.passed)
                    st.caption(f"{passed} / {len(readiness.checklist)} gates passed")
                with head[1]:
                    if mp.recommended:
                        st.metric("Quality", f"{mp.quality_score:.0f}")

                for item in readiness.checklist:
                    icon = "✅" if item.passed else "⬜"
                    st.markdown(f"{icon} **{item.label}** — :gray[{item.detail}]")

                if readiness.suggestions:
                    with st.expander(f"Suggestions ({len(readiness.suggestions)})"):
                        for sug in readiness.suggestions:
                            st.markdown(f"- {sug}")

                with st.expander("Notes"):
                    with st.form(f"notes_{mp.id}"):
                        new_notes = st.text_area(
                            "Notes",
                            value=mp.notes,
                            height=120,
                            key=f"notes_in_{mp.id}",
                            label_visibility="collapsed",
                        )
                        if st.form_submit_button("Save notes"):
                            mp.notes = new_notes
                            mp.updated_at = datetime.now(timezone.utc)
                            managed_repo.upsert(mp)
                            st.success("Saved.")
                            st.rerun()

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

        # If a suggestion is pending from the previous run, inject it into the
        # widget's own state key BEFORE the widget is instantiated. Streamlit
        # forbids modifying widget state after creation, so the button writes
        # `tp_pending_suggestion` and we consume it here.
        if "tp_pending_suggestion" in st.session_state:
            st.session_state["tp_audience_input"] = st.session_state.pop(
                "tp_pending_suggestion"
            )

        aud_col, btn_col = st.columns([4, 1])
        with aud_col:
            tp_audience = st.text_input(
                "Target audience",
                placeholder="e.g. First-year affiliates in Brazil, 25–35, struggling to make their first sale",
                key="tp_audience_input",
            )
        with btn_col:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button(
                "✨ Suggest",
                use_container_width=True,
                help="Ask Haiku to propose an audience based on this product's metadata",
            ):
                try:
                    from backend.app.traffic_planner import build_default_traffic_planner
                    planner = build_default_traffic_planner()
                    with st.spinner("Asking Haiku..."):
                        suggestion = planner.suggest_audience(selected_product)
                    if suggestion:
                        st.session_state["tp_pending_suggestion"] = suggestion
                        st.rerun()
                    else:
                        st.warning("Haiku returned an empty suggestion.")
                except Exception as e:
                    st.error(f"Suggestion failed: {e}")

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

            briefs_by_idx = {b.variant_index: b for b in st.session_state.get("last_creative_briefs", [])}

            cbcol1, cbcol2 = st.columns([1, 1])
            with cbcol1:
                if st.button(
                    "🎨 Generate creative briefs for all variants",
                    use_container_width=True,
                    help=(
                        "One Haiku call returns a matched image + video prompt for each variant — "
                        "ready to paste into Ideogram v3 (images) and Veo 3 / Runway Gen-4 (video). "
                        "No external images are generated yet; that's the follow-up step."
                    ),
                ):
                    if not tp_audience:
                        st.error("Target audience is required to generate creative briefs.")
                    else:
                        try:
                            from backend.app.services.creatives import (
                                build_default_creative_generator,
                            )

                            gen = build_default_creative_generator()
                            with st.spinner("Haiku is drafting image + video prompts..."):
                                briefs = gen.briefs(
                                    product=selected_product,
                                    target_audience=tp_audience,
                                    variants=variants,
                                )
                            st.session_state["last_creative_briefs"] = briefs
                            st.rerun()
                        except Exception as e:
                            st.error(f"Creative brief generation failed: {e}")
            with cbcol2:
                if briefs_by_idx and st.button(
                    "Clear creative briefs",
                    use_container_width=True,
                    type="secondary",
                ):
                    st.session_state.pop("last_creative_briefs", None)
                    st.rerun()

            for i, v in enumerate(variants):
                with st.container(border=True):
                    st.markdown(f"**Variant {i + 1} · {v.platform.value.replace('_', ' ').title()}**")
                    st.markdown(f"**Headline:** {v.headline}")
                    st.markdown(f"**Primary text:** {v.primary_text}")
                    st.markdown(f"**Description:** {v.description}")
                    st.caption(f"Audience: {v.target_audience}")
                    st.caption(f"Daily budget: R$ {v.daily_budget_brl:.0f}")
                    if v.creative_notes:
                        st.caption(f"Creative: {v.creative_notes}")

                    brief = briefs_by_idx.get(i)
                    if brief:
                        # Fetch latest persisted image/video for (product, variant) so
                        # reload doesn't lose them.
                        latest = creative_repo.latest_by_variant(selected_product.id)
                        latest_image = latest.get((i, "image"))
                        latest_video = latest.get((i, "video"))

                        has_fal = bool(
                            os.environ.get("FAL_API_KEY") or os.environ.get("FAL_KEY")
                        )
                        has_replicate = bool(
                            os.environ.get("REPLICATE_API_TOKEN")
                            or os.environ.get("REPLICATE_API_KEY")
                        )
                        has_direct_gen = has_fal or has_replicate

                        with st.expander(
                            f"🎨 Creative brief · {brief.aspect_ratio} · {brief.image_tool} + {brief.video_tool}",
                            expanded=True,
                        ):
                            prompt_col, image_col = st.columns([3, 2])
                            with prompt_col:
                                st.markdown(
                                    "**Image prompt** — paste into [Ideogram v3](https://ideogram.ai)"
                                    " or generate directly below"
                                )
                                st.code(brief.image_prompt, language=None)
                                if has_direct_gen:
                                    if st.button(
                                        "🖼 Generate image now",
                                        key=f"genimg_{i}",
                                        help="Hit fal.ai directly. ~R$0.40 per image.",
                                    ):
                                        try:
                                            from backend.app.services.creatives import (
                                                build_default_creative_generator,
                                            )

                                            gen = build_default_creative_generator()
                                            if not hasattr(gen, "generate_image"):
                                                st.error("FAL_API_KEY not set.")
                                            else:
                                                provider = type(gen).__name__.replace("Generator", "")
                                                with st.spinner(f"Generating image via {provider}..."):
                                                    asset = gen.generate_image(
                                                        product_id=selected_product.id,
                                                        brief=brief,
                                                    )
                                                creative_repo.insert(asset)
                                                st.success(
                                                    f"Image generated (R$ {asset.cost_brl:.2f})"
                                                    if asset.cost_brl
                                                    else "Image generated."
                                                )
                                                st.rerun()
                                        except Exception as e:
                                            st.error(f"Image generation failed: {e}")
                                else:
                                    st.caption(
                                        ":gray[Set `FAL_API_KEY` or `REPLICATE_API_TOKEN` in .env to enable direct image generation.]"
                                    )
                            with image_col:
                                if latest_image:
                                    st.image(latest_image.asset_url, use_container_width=True)
                                    st.caption(
                                        f"Model: {latest_image.model} · "
                                        + (
                                            f"R$ {latest_image.cost_brl:.2f} · "
                                            if latest_image.cost_brl
                                            else ""
                                        )
                                        + f"[Open ↗]({latest_image.asset_url})"
                                    )

                            st.divider()

                            vp_col, video_col = st.columns([3, 2])
                            with vp_col:
                                st.markdown(
                                    f"**Video prompt** ({brief.video_duration_s}s) — paste into "
                                    f"[Veo 3](https://labs.google/veo) or generate directly below"
                                )
                                st.code(brief.video_prompt, language=None)
                                if has_direct_gen:
                                    if st.button(
                                        "🎬 Generate video now",
                                        key=f"genvid_{i}",
                                        help="Direct generation via fal.ai or Replicate. Veo 3 ~R$15/clip, 2–5 min wait. Cheaper via REPLICATE_VIDEO_MODEL override.",
                                    ):
                                        try:
                                            from backend.app.services.creatives import (
                                                build_default_creative_generator,
                                            )

                                            gen = build_default_creative_generator()
                                            if not hasattr(gen, "generate_video"):
                                                st.error("FAL_API_KEY not set.")
                                            else:
                                                provider = type(gen).__name__.replace("Generator", "")
                                                with st.spinner(
                                                    f"Generating video via {provider} (2–5 minutes)..."
                                                ):
                                                    asset = gen.generate_video(
                                                        product_id=selected_product.id,
                                                        brief=brief,
                                                    )
                                                creative_repo.insert(asset)
                                                st.success(
                                                    f"Video generated (R$ {asset.cost_brl:.2f})"
                                                    if asset.cost_brl
                                                    else "Video generated."
                                                )
                                                st.rerun()
                                        except Exception as e:
                                            st.error(f"Video generation failed: {e}")
                            with video_col:
                                if latest_video:
                                    st.video(latest_video.asset_url)
                                    st.caption(
                                        f"Model: {latest_video.model} · "
                                        + (
                                            f"R$ {latest_video.cost_brl:.2f} · "
                                            if latest_video.cost_brl
                                            else ""
                                        )
                                        + f"[Open ↗]({latest_video.asset_url})"
                                    )
