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

# Start the lightweight health-check server on port 8001 so Railway's
# healthcheck can verify the process is alive independently of Streamlit's
# own initialisation. Must happen before st.set_page_config.
from ui.health_server import start_health_server  # noqa: E402

start_health_server()

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

st.title("Automação de Pipeline de Afiliados")
st.caption(
    "Pipeline completo de marketing de afiliados em 7 fases. "
    "Descobre e pontua produtos, gerencia links rastreados e bridge pages, "
    "gera briefs de tráfego e creativos com IA, e mostra KPIs por produto."
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
    st.header("Descoberta")
    use_mock = st.toggle(
        "Usar dados mock",
        value=False,
        help=(
            "Ligado: usa fixtures de demonstração. Dados mock NÃO são persistidos — "
            "aparecem apenas na aba Top picks. "
            "Desligado: busca das fontes reais (Hotmart via hidratação do Next.js + "
            "Amazon PA-API se houver credenciais); resultados são persistidos no DuckDB."
        ),
    )
    limit = st.slider("Produtos por fonte", min_value=10, max_value=200, value=50, step=10)
    top_n = st.slider("Mostrar top N", min_value=5, max_value=100, value=25, step=5)

    st.divider()
    st.subheader("Enriquecimento com LLM (V1)")
    enable_llm = st.toggle(
        "Usar Claude para ranquear por fit de nicho",
        value=False,
        help=(
            "Chama Sonnet 4.6 para rerrankear o catálogo conforme o quanto cada produto "
            "combina com o nicho declarado. Requer ANTHROPIC_API_KEY no ambiente."
        ),
    )
    target_niche = st.text_input(
        "Nicho-alvo",
        value="",
        placeholder="ex: Marketing digital para afiliados iniciantes",
        disabled=not enable_llm,
    )
    target_audience = st.text_input(
        "Público-alvo",
        value="",
        placeholder="ex: 25-35 anos, Brasil, começando o primeiro negócio paralelo",
        disabled=not enable_llm,
    )

    if st.button("Rodar descoberta", type="primary", use_container_width=True):
        analyzer = None
        if enable_llm and target_niche and target_audience:
            try:
                from backend.app.llm import build_default_analyzer
                analyzer = build_default_analyzer()
            except Exception as e:
                st.error(f"Não foi possível inicializar o analisador LLM: {e}")

        with st.spinner("Coletando e pontuando..."):
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
            f"Coletados {result.fetched} de {', '.join(result.sources)} · "
            f"pontuados {result.scored}"
        )
        if result.llm_signals_filled:
            summary += f" · LLM analisou {result.llm_signals_filled} páginas de vendas"
        if result.niche_rankings:
            summary += f" · ranqueados por fit de nicho ({len(result.niche_rankings)})"
        st.success(summary)
        if result.errors:
            for err in result.errors:
                st.warning(err)

    st.divider()
    st.subheader("Manutenção do catálogo")
    persisted_count = len(repo.top_products(limit=10_000))
    st.caption(f"{persisted_count} produtos atualmente no catálogo persistido.")
    if persisted_count > 0:
        confirm = st.checkbox(
            "Entendo que isto apagará todos os produtos e scores persistidos",
            key="confirm_clear",
        )
        if st.button(
            "Limpar catálogo",
            type="secondary",
            use_container_width=True,
            disabled=not confirm,
        ):
            deleted = repo.clear_catalog()
            st.session_state.pop("last_result", None)
            st.success(f"Apagados {deleted} produtos do catálogo.")
            st.rerun()
    else:
        st.caption(":gray[Nada para limpar.]")


phase1, phase2, phase3, phase4, phase5, phase6, phase7 = st.tabs(
    [
        "Fase 1 — Descoberta",
        "Fase 2 — Produtos & Links",
        "Fase 3 — Planejamento de Tráfego",
        "Fase 4 — Bridge Pages",
        "Fase 5 — E-mail",
        "Fase 6 — KPIs",
        "Fase 7 — Escalar",
    ]
)

with phase1:
    tab_onboarding, tab_top, tab_browse, tab_analytics = st.tabs(
        ["Onboarding", "Top picks", "Navegar catálogo", "Analytics"]
    )

with phase2:
    st.caption(
        "Catálogo manual + links rastreados. A configuração de canais fica "
        "dentro de cada card de produto (role até **Canais de tráfego** "
        "debaixo de um produto)."
    )
    tab_managed, tab_links = st.tabs(["Meus Produtos", "Link vault legado"])

tab_traffic = phase3
tab_bridges = phase4
tab_email = phase5
tab_kpis = phase6
tab_scale = phase7


_NICHE_LABELS = {
    Niche.FINANCE: "Finanças",
    Niche.DIGITAL_MARKETING: "Marketing digital",
    Niche.HEALTH: "Saúde",
    Niche.TECH_SAAS: "Tecnologia / SaaS",
    Niche.BUSINESS: "Negócios",
    Niche.OTHER: "Outro",
}


with tab_onboarding:
    st.subheader("Onboarding do operador")
    st.caption("Escolha o nicho em que está construindo seu portfólio. Define os defaults em todas as outras abas.")
    current_profile = profile_repo.get()
    if current_profile:
        st.success(
            f"Nicho principal: **{_NICHE_LABELS[current_profile.primary_niche]}** "
            f"(definido em {current_profile.completed_at.strftime('%d/%m/%Y %H:%M')})"
        )
    with st.form("onboarding_form"):
        default_idx = (
            list(Niche).index(current_profile.primary_niche) if current_profile else 0
        )
        chosen = st.selectbox(
            "Nicho principal",
            options=list(Niche),
            index=default_idx,
            format_func=lambda n: _NICHE_LABELS[n],
        )
        if st.form_submit_button("Salvar", type="primary"):
            profile_repo.save(OperatorProfile(primary_niche=chosen))
            st.success("Salvo.")
            st.rerun()


with tab_managed:
    st.subheader("Meus produtos")
    st.caption(
        "Catálogo curado manualmente — os produtos que você está promovendo ativamente. "
        "Score automático com comissão (40%), ticket (30%) e confiança da plataforma (30%). "
        "Score > 70 recebe o selo **Recomendado**."
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
            "⚠ Canais pagos ativos com **0 cliques nas últimas 24h**:\n\n"
            + "\n".join(
                f"- **{name}** · {ch}"
                + (f" · R$ {budget:.0f}/dia" if budget else "")
                for name, ch, budget in _stale_alerts
            )
        )

    with st.expander("Adicionar novo produto", expanded=False):
        with st.form("new_managed_product"):
            col1, col2 = st.columns(2)
            with col1:
                mp_name = st.text_input("Nome do produto", placeholder="ex: Curso SEO Black")
                mp_platform = st.selectbox(
                    "Plataforma",
                    options=list(Platform),
                    format_func=lambda p: p.value.title(),
                )
                mp_niche = st.selectbox(
                    "Nicho",
                    options=list(Niche),
                    format_func=lambda n: _NICHE_LABELS[n],
                    index=(
                        list(Niche).index(current_profile.primary_niche)
                        if current_profile
                        else 0
                    ),
                )
                mp_commission = st.number_input(
                    "Comissão %", min_value=0.0, max_value=100.0, value=50.0, step=1.0
                )
            with col2:
                mp_ticket = st.number_input(
                    "Ticket médio (R$)", min_value=0.0, value=297.0, step=10.0
                )
                mp_sales_url = st.text_input("URL da página de vendas", placeholder="https://...")
                mp_aff_url = st.text_input("URL de afiliado", placeholder="https://...")
                mp_notes = st.text_area("Notas", value="", height=68)

            if st.form_submit_button("Criar produto", type="primary"):
                if not (mp_name and mp_aff_url):
                    st.error("Nome e URL de afiliado são obrigatórios.")
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
                    st.success(f"Criado — score de qualidade {mp.quality_score:.1f}")
                    st.rerun()

    managed_list = managed_repo.list()
    if not managed_list:
        st.info("Nenhum produto cadastrado ainda. Adicione um acima.")
    else:
        for mp in managed_list:
            with st.container(border=True):
                header_cols = st.columns([3, 1, 1])
                with header_cols[0]:
                    title = f"### {mp.name}"
                    if mp.recommended:
                        title += "  :green-badge[Recomendado]"
                    st.markdown(title)
                    st.caption(
                        f"{mp.platform.value.title()} · {_NICHE_LABELS[mp.niche]} · "
                        f"R$ {mp.ticket_brl:,.2f} · comissão {mp.commission_pct:.0f}%"
                    )
                with header_cols[1]:
                    st.metric("Score de qualidade", f"{mp.quality_score:.1f}")
                with header_cols[2]:
                    if st.button("Excluir", key=f"del_{mp.id}", type="secondary"):
                        managed_repo.delete(mp.id)
                        st.rerun()
                if mp.affiliate_url:
                    st.markdown(f"[Link de afiliado ↗]({mp.affiliate_url})")
                if mp.notes:
                    st.caption(mp.notes)

                with st.expander("Links rastreados"):
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
                                st.metric("Cliques", stats.total_clicks)
                            with col_c:
                                if st.button("Excluir", key=f"delsl_{sl.slug}"):
                                    short_repo.delete(sl.slug)
                                    st.rerun()
                    else:
                        st.caption(":gray[Nenhum link rastreado ainda.]")

                    with st.form(f"newsl_{mp.id}"):
                        st.markdown("**Novo link rastreado**")
                        utm_cols = st.columns(3)
                        new_src = utm_cols[0].text_input(
                            "utm_source", placeholder="instagram", key=f"src_{mp.id}"
                        )
                        new_med = utm_cols[1].text_input(
                            "utm_medium", placeholder="bio", key=f"med_{mp.id}"
                        )
                        new_cmp = utm_cols[2].text_input(
                            "utm_campaign", placeholder="lancamento-abril", key=f"cmp_{mp.id}"
                        )
                        if st.form_submit_button("Criar link rastreado"):
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
                            st.success(f"Criado: {APP_BASE_URL}/r/{slug}")
                            st.rerun()

                with st.expander("Canais de tráfego"):
                    existing_channels = {
                        c.channel: c for c in channel_repo.list_for_product(mp.id)
                    }
                    st.caption(
                        f"{len(existing_channels)} configurado(s) · "
                        f"{sum(1 for c in existing_channels.values() if c.status == ChannelStatus.ACTIVE)} ativo(s)"
                    )
                    for cfg in existing_channels.values():
                        row = st.columns([3, 1, 1, 1])
                        is_paid = cfg.channel in PAID_CHANNELS
                        row[0].markdown(
                            f"**{cfg.channel.value}** "
                            f"{':green-badge[ativo]' if cfg.status == ChannelStatus.ACTIVE else ':gray-badge[pausado]'}"
                            f"{' · :orange-badge[pago]' if is_paid else ' · :blue-badge[orgânico]'}"
                        )
                        row[1].caption(
                            f"R$ {cfg.daily_budget_brl:.0f}/dia" if cfg.daily_budget_brl else "—"
                        )
                        row[2].caption(
                            f"meta {cfg.daily_click_goal}/dia" if cfg.daily_click_goal else "—"
                        )
                        if row[3].button(
                            "Pausar" if cfg.status == ChannelStatus.ACTIVE else "Ativar",
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
                        st.markdown("**Adicionar canal**")
                        ch_cols = st.columns([2, 1, 1])
                        available = [
                            ch for ch in TrafficChannel if ch not in existing_channels
                        ]
                        if not available:
                            st.caption(":gray[Todos os canais já configurados.]")
                            st.form_submit_button("Adicionar", disabled=True)
                        else:
                            new_ch = ch_cols[0].selectbox(
                                "Canal",
                                options=available,
                                format_func=lambda c: c.value,
                                key=f"newchsel_{mp.id}",
                            )
                            new_budget = ch_cols[1].number_input(
                                "Orçamento diário R$",
                                min_value=0.0,
                                value=0.0,
                                step=10.0,
                                key=f"newchbud_{mp.id}",
                            )
                            new_goal = ch_cols[2].number_input(
                                "Meta cliques/dia",
                                min_value=0,
                                value=0,
                                step=5,
                                key=f"newchgoal_{mp.id}",
                            )
                            if st.form_submit_button("Adicionar canal"):
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
        "Páginas de pré-venda autônomas em `/bp/{slug}` — sem cabeçalho, sem rodapé, "
        "mobile-first. Cliques no CTA são registrados e atribuídos ao produto para "
        "rastreio de conversão."
    )

    _managed_for_bridge = managed_repo.list()
    if not _managed_for_bridge:
        st.info("Cadastre um produto primeiro (aba Meus Produtos).")
    else:
        with st.expander("Criar nova bridge page", expanded=False):
            with st.form("new_bridge"):
                bp_product = st.selectbox(
                    "Produto",
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
                    "Bullets (um por linha, até 5)",
                    placeholder="Benefício 1\nBenefício 2\nBenefício 3",
                    height=100,
                )
                bp_cta_text = st.text_input("Texto do CTA", value="Quero saber mais")
                bp_cta_url = st.text_input(
                    "URL de destino do CTA (geralmente o link de afiliado ou link rastreado)",
                    value=bp_product.affiliate_url if bp_product else "",
                )
                bp_color = st.color_picker("Cor primária", value="#2563eb")
                if st.form_submit_button("Criar", type="primary"):
                    if not (bp_headline and bp_cta_text and bp_cta_url):
                        st.error("Headline, texto do CTA e URL do CTA são obrigatórios.")
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
                        st.success(f"Criada: {APP_BASE_URL}/bp/{slug}")
                        st.rerun()

        pages = bridge_repo.list()
        if not pages:
            st.info("Nenhuma bridge page ainda.")
        else:
            for page in pages:
                mp = managed_repo.get(page.managed_product_id)
                mp_name = mp.name if mp else "(produto excluído)"
                with st.container(border=True):
                    head_cols = st.columns([3, 1, 1])
                    with head_cols[0]:
                        st.markdown(f"### {page.headline}")
                        st.caption(
                            f"para **{mp_name}** · /bp/{page.slug} · "
                            f"atualizada em {page.updated_at.strftime('%d/%m/%Y %H:%M')}"
                        )
                    with head_cols[1]:
                        n_cta = click_repo.count_for_product(
                            page.managed_product_id, target_type="bridge_cta"
                        )
                        st.metric("Cliques no CTA", n_cta)
                    with head_cols[2]:
                        if st.button("Excluir", key=f"delbp_{page.slug}"):
                            bridge_repo.delete(page.slug)
                            st.rerun()
                    st.code(f"{APP_BASE_URL}/bp/{page.slug}", language=None)
                    if page.bullets:
                        st.caption(" · ".join(page.bullets))

with tab_email:
    st.subheader("Listas de e-mail & sequências de nutrição")
    st.caption(
        "Copie o snippet do formulário embedável e cole em qualquer site externo. "
        "No opt-in o inscrito é enviado ao MailerLite e atribuído ao grupo "
        "configurado na sequência desse produto."
    )

    _managed_for_email = managed_repo.list()
    if not _managed_for_email:
        st.info("Cadastre um produto primeiro (aba Meus Produtos).")
    else:
        if st.button("Sincronizar contagem de inscritos do MailerLite"):
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
            st.success(f"Sincronizado(s) {synced} produto(s). {errors} erro(s).")
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
                        "Inscritos",
                        snap.count if snap else 0,
                        help=(
                            f"Sincronizado em {snap.synced_at:%d/%m/%Y %H:%M}" if snap else "Ainda não sincronizado"
                        ),
                    )

                with st.expander("Snippet do formulário embed"):
                    st.caption(
                        "Cole este HTML em qualquer site externo para coletar opt-ins "
                        "desse produto. O iframe é autocontido (sem JS)."
                    )
                    snippet = (
                        f'<iframe src="{APP_BASE_URL}/mailerlite/embed/{mp.id}" '
                        f'width="420" height="340" style="border:1px solid #e5e7eb;'
                        f'border-radius:8px" title="Opt-in"></iframe>'
                    )
                    st.code(snippet, language="html")

                with st.expander("Sequência de nutrição"):
                    existing_seqs = seq_repo.list(managed_product_id=mp.id)
                    current = existing_seqs[0] if existing_seqs else None

                    with st.form(f"seq_form_{mp.id}"):
                        seq_name = st.text_input(
                            "Nome da sequência",
                            value=current.name if current else f"Nutrição {mp.name}",
                            key=f"seqname_{mp.id}",
                        )
                        ml_group = st.text_input(
                            "ID do grupo no MailerLite (obrigatório para o opt-in funcionar)",
                            value=current.mailerlite_group_id if current else "",
                            key=f"seqgroup_{mp.id}",
                        )
                        num_steps = st.slider(
                            "Número de e-mails",
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
                            st.markdown(f"**E-mail {i + 1}**")
                            c1, c2 = st.columns([1, 3])
                            delay = c1.number_input(
                                "Atraso (dias)",
                                min_value=0,
                                value=existing_step.delay_days if existing_step else (0 if i == 0 else 1),
                                key=f"delay_{mp.id}_{i}",
                            )
                            subj = c2.text_input(
                                "Assunto",
                                value=existing_step.subject if existing_step else "",
                                key=f"subj_{mp.id}_{i}",
                            )
                            body = st.text_area(
                                "Corpo",
                                value=existing_step.body if existing_step else "",
                                height=100,
                                key=f"body_{mp.id}_{i}",
                            )
                            steps_data.append((delay, subj, body))

                        if st.form_submit_button(
                            "Salvar sequência" if current else "Criar sequência",
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
                            st.success("Salvo.")
                            st.rerun()

with tab_kpis:
    st.subheader("KPIs por produto")
    st.caption(
        "Ranqueado por **EPC real** (decrescente). Produtos sem EPC preenchido "
        "ficam no final — preencha na aba Meus Produtos depois que receber "
        "comissões reais."
    )
    _kpi_products = managed_repo.list()
    if not _kpi_products:
        st.info("Nenhum produto cadastrado ainda.")
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
                    "Produto": mp.name,
                    "Nicho": _NICHE_LABELS[mp.niche],
                    "Cliques afiliado": aff,
                    "Views bridge": views,
                    "Cliques CTA": cta,
                    "Conv. bridge": f"{conv * 100:.1f}%",
                    "Inscritos": subs_all.get(mp.id, 0),
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
        st.subheader("Cliques por dia (últimos 30 dias)")
        product_choice = st.selectbox(
            "Produto",
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
                        "data": d,
                        "Cliques afiliado": aff_ts.get(d, 0),
                        "Views bridge": views_ts.get(d, 0),
                        "Cliques CTA": cta_ts.get(d, 0),
                    }
                    for d in all_dates
                ]
                st.line_chart(chart_rows, x="data", use_container_width=True)
            else:
                st.caption(":gray[Nenhum dado de clique para este produto ainda.]")

        st.divider()
        st.subheader("Editar EPC / CPV")
        st.caption(
            "Entradas manuais — preencha quando as comissões reais começarem a chegar."
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
                if c4.form_submit_button("Salvar"):
                    mp.epc_actual = new_epc if new_epc > 0 else None
                    mp.cpv_actual = new_cpv if new_cpv > 0 else None
                    mp.updated_at = datetime.now(timezone.utc)
                    managed_repo.upsert(mp)
                    st.rerun()

with tab_scale:
    st.subheader("Prontidão para escalar")
    st.caption(
        "Checklist por produto. Um produto está pronto para escalar quando todos os "
        "gates passam — até lá, as sugestões abaixo mostram exatamente o que está "
        "faltando."
    )
    _scale_products = managed_repo.list()
    if not _scale_products:
        st.info("Nenhum produto cadastrado ainda.")
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
                        ":green-badge[PRONTO PARA ESCALAR]"
                        if readiness.ready_to_scale
                        else ":orange-badge[Não pronto]"
                    )
                    st.markdown(f"### {mp.name} {badge}")
                    passed = sum(1 for item in readiness.checklist if item.passed)
                    st.caption(f"{passed} / {len(readiness.checklist)} gates aprovados")
                with head[1]:
                    if mp.recommended:
                        st.metric("Qualidade", f"{mp.quality_score:.0f}")

                for item in readiness.checklist:
                    icon = "✅" if item.passed else "⬜"
                    st.markdown(f"{icon} **{item.label}** — :gray[{item.detail}]")

                if readiness.suggestions:
                    with st.expander(f"Sugestões ({len(readiness.suggestions)})"):
                        for sug in readiness.suggestions:
                            st.markdown(f"- {sug}")

                with st.expander("Notas"):
                    with st.form(f"notes_{mp.id}"):
                        new_notes = st.text_area(
                            "Notas",
                            value=mp.notes,
                            height=120,
                            key=f"notes_in_{mp.id}",
                            label_visibility="collapsed",
                        )
                        if st.form_submit_button("Salvar notas"):
                            mp.notes = new_notes
                            mp.updated_at = datetime.now(timezone.utc)
                            managed_repo.upsert(mp)
                            st.success("Salvo.")
                            st.rerun()

with tab_top:
    result = st.session_state.get("last_result")
    if not result:
        st.info("Rode a descoberta na sidebar para ver os top picks.")
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
                        f"{p.platform.value.title()} · {p.category or 'Sem categoria'} · "
                        f"{p.producer_name or 'Produtor desconhecido'}"
                    )
                    meta_cols = st.columns(4)
                    meta_cols[0].metric("Preço", f"R$ {p.price_brl:,.2f}" if p.price_brl else "—")
                    meta_cols[1].metric(
                        "Comissão %",
                        f"{p.commission_pct:.0f}%" if p.commission_pct is not None else "—",
                    )
                    meta_cols[2].metric(
                        "Comissão R$",
                        f"R$ {p.commission_brl:,.2f}" if p.commission_brl else "—",
                    )
                    meta_cols[3].metric(
                        "EPC",
                        f"R$ {s.expected_value_per_visit:,.4f}",
                        help="Valor esperado por visita assumindo 2% de conversão.",
                    )
                    is_mock = (p.raw or {}).get("source") == "mock_fixture"
                    if is_mock:
                        st.markdown(
                            f"[Link demo ↗]({p.url}) "
                            ":gray[_(fixture mock — aponta para example.com)_]"
                        )
                    else:
                        st.markdown(f"[Abrir página do produto ↗]({p.url})")
                with col_score:
                    st.metric("Score", f"{s.score:.1f} / 100")
                    fit = niche_fit_by_id.get(p.id)
                    if fit:
                        st.metric("Fit de nicho", f"{fit.fit_score:.0f} / 100")
                        st.caption(fit.reasoning)
                    with st.expander("Por que esse score?"):
                        st.write(
                            {
                                "comissao": s.components.commission,
                                "ticket": s.components.ticket,
                                "reputacao": s.components.reputation,
                                "popularidade": s.components.popularity,
                                "sinais_pagina": s.components.sales_signals,
                            }
                        )
                    llm_detail = p.raw.get("sales_page_signals_detail") if p.raw else None
                    if llm_detail:
                        with st.expander("Sinais da página (LLM)"):
                            st.write(llm_detail)

with tab_browse:
    persisted = repo.top_products(limit=200)
    if not persisted:
        st.info("Nenhum produto persistido ainda. Rode a descoberta primeiro.")
    else:
        rows = [
            {
                "Score": round(sp.score.score, 1),
                "Nome": sp.product.name,
                "Plataforma": sp.product.platform.value,
                "Nicho": sp.product.niche.value if sp.product.niche else "—",
                "Preço (R$)": sp.product.price_brl,
                "Comissão %": sp.product.commission_pct,
                "Popularidade": sp.product.popularity,
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
                    help="Clique para abrir a página do produto (URLs de fixture apontam para example.com)",
                    display_text="Abrir ↗",
                ),
            },
        )


with tab_analytics:
    st.subheader("Analytics — dividido por plataforma")
    st.caption(
        "Cada visualização aqui é colorida pela plataforma de origem. Use os filtros "
        "para focar em uma plataforma ou comparar nichos entre todas elas."
    )

    import altair as alt  # bundled with Streamlit
    import pandas as pd

    analytics_data = repo.top_products(limit=500)
    if not analytics_data:
        st.info("Nenhum produto persistido ainda. Rode a descoberta na sidebar primeiro.")
    else:
        df = pd.DataFrame(
            [
                {
                    "Plataforma": sp.product.platform.value,
                    "Nome": sp.product.name,
                    "Nicho": sp.product.niche.value if sp.product.niche else "desconhecido",
                    "Score": sp.score.score,
                    "Preço (R$)": sp.product.price_brl,
                    "Comissão %": sp.product.commission_pct,
                    "Comissão R$": sp.product.commission_brl,
                    "EPC": sp.score.expected_value_per_visit,
                    "Popularidade": sp.product.popularity,
                    "Reputação": sp.product.producer_reputation,
                }
                for sp in analytics_data
            ]
        )

        # -------- Filters --------
        fcol1, fcol2, fcol3 = st.columns([2, 2, 1])
        available_platforms = sorted(df["Plataforma"].unique())
        selected_platforms = fcol1.multiselect(
            "Plataformas",
            options=available_platforms,
            default=available_platforms,
            format_func=lambda v: v.title(),
        )
        available_niches = sorted(df["Nicho"].unique())
        selected_niches = fcol2.multiselect(
            "Nichos",
            options=available_niches,
            default=available_niches,
            format_func=lambda v: v.replace("_", " ").title(),
        )
        min_score = fcol3.slider("Score mínimo", 0, 100, 0, 5)

        filtered = df[
            df["Plataforma"].isin(selected_platforms)
            & df["Nicho"].isin(selected_niches)
            & (df["Score"] >= min_score)
        ]

        if filtered.empty:
            st.warning("Nenhum produto bate com os filtros atuais.")
        else:
            # -------- KPI row --------
            kpi_cols = st.columns(4)
            kpi_cols[0].metric("Total de produtos", len(filtered))
            kpi_cols[1].metric("Plataformas", filtered["Plataforma"].nunique())
            kpi_cols[2].metric("Score médio", f"{filtered['Score'].mean():.1f}")
            mean_epc = filtered["EPC"].fillna(0).mean()
            kpi_cols[3].metric("EPC médio", f"R$ {mean_epc:.4f}")

            # Consistent color mapping across every chart so the platform
            # palette stays stable when the user toggles filters.
            platform_color = alt.Color(
                "Plataforma:N",
                scale=alt.Scale(scheme="tableau10"),
                legend=alt.Legend(title="Plataforma"),
            )

            # -------- Chart 1: Product count by platform --------
            st.markdown("#### Produtos por plataforma")
            count_chart = (
                alt.Chart(filtered)
                .mark_bar()
                .encode(
                    x=alt.X("count():Q", title="Produtos"),
                    y=alt.Y("Plataforma:N", sort="-x", title=None),
                    color=platform_color,
                    tooltip=["Plataforma", alt.Tooltip("count():Q", title="Total")],
                )
                .properties(height=min(60 * len(selected_platforms), 300))
            )
            st.altair_chart(count_chart, use_container_width=True)

            # -------- Chart 2: Score distribution by platform --------
            st.markdown("#### Distribuição de score")
            st.caption(
                "Cada ponto é um produto. Dispersões maiores significam que o modelo "
                "de score diferencia bem os produtos daquela plataforma; clusters "
                "apertados indicam sinal comprimido (comum na Hotmart ao vivo quando "
                "preço e comissão ficam atrás do login de afiliado)."
            )
            score_chart = (
                alt.Chart(filtered)
                .mark_circle(size=80, opacity=0.7)
                .encode(
                    x=alt.X("Score:Q", title="Score (0–100)", scale=alt.Scale(domain=[0, 100])),
                    y=alt.Y("Plataforma:N", title=None),
                    color=platform_color,
                    tooltip=["Nome", "Plataforma", "Nicho", "Score", "EPC"],
                )
                .properties(height=min(60 * len(selected_platforms), 300))
            )
            st.altair_chart(score_chart, use_container_width=True)

            # -------- Chart 3: Top 20 products, colored by platform --------
            st.markdown("#### Top 20 produtos por score")
            top_n = filtered.nlargest(20, "Score")
            top_chart = (
                alt.Chart(top_n)
                .mark_bar()
                .encode(
                    x=alt.X("Score:Q", title="Score"),
                    y=alt.Y(
                        "Nome:N",
                        sort=alt.SortField(field="Score", order="descending"),
                        title=None,
                        axis=alt.Axis(labelLimit=320),
                    ),
                    color=platform_color,
                    tooltip=[
                        "Nome",
                        "Plataforma",
                        "Nicho",
                        alt.Tooltip("Score:Q", format=".1f"),
                        alt.Tooltip("Preço (R$):Q", format=".2f"),
                        alt.Tooltip("Comissão %:Q", format=".1f"),
                        alt.Tooltip("EPC:Q", format=".4f"),
                    ],
                )
                .properties(height=min(30 * len(top_n), 600))
            )
            st.altair_chart(top_chart, use_container_width=True)

            # -------- Chart 4: Niche mix per platform --------
            st.markdown("#### Mix de nichos por plataforma")
            st.caption(
                "Onde o catálogo de cada plataforma se encaixa na sua taxonomia de "
                "nichos. Uma plataforma concentrada em um nicho é mais fácil de "
                "especializar; uma com cobertura ampla é melhor para diversificar."
            )
            niche_chart = (
                alt.Chart(filtered)
                .mark_bar()
                .encode(
                    x=alt.X("count():Q", title="Produtos", stack="normalize"),
                    y=alt.Y("Plataforma:N", title=None),
                    color=alt.Color(
                        "Nicho:N",
                        scale=alt.Scale(scheme="category10"),
                        legend=alt.Legend(title="Nicho"),
                    ),
                    tooltip=[
                        "Plataforma",
                        "Nicho",
                        alt.Tooltip("count():Q", title="Total"),
                    ],
                )
                .properties(height=min(60 * len(selected_platforms), 300))
            )
            st.altair_chart(niche_chart, use_container_width=True)


with tab_links:
    st.subheader("Link vault legado")
    st.caption(
        "Repositório central de links de afiliado. Controle o status de aprovação "
        "por plataforma e construa URLs rastreadas com UTMs normalizadas."
    )

    col_add, col_filter = st.columns([2, 1])

    with col_add:
        with st.expander("Adicionar novo link de afiliado", expanded=False):
            with st.form("add_link_form", clear_on_submit=True):
                label = st.text_input("Rótulo", placeholder="ex: Curso X — Instagram bio")
                raw_url = st.text_input("URL de afiliado", placeholder="https://hotmart.com/...")
                new_platform = st.selectbox(
                    "Plataforma",
                    options=[p.value for p in Platform],
                    format_func=lambda v: v.title(),
                )
                new_status = st.selectbox(
                    "Status de aprovação",
                    options=[s.value for s in LinkStatus],
                    format_func=lambda v: v.title(),
                )
                notes = st.text_area("Notas", placeholder="Data de aprovação do produtor, restrições, etc.")
                tags_raw = st.text_input("Tags (separadas por vírgula)", placeholder="instagram, bio, orgânico")
                submitted = st.form_submit_button("Salvar link", type="primary")
                if submitted:
                    if not label or not raw_url:
                        st.error("Rótulo e URL são obrigatórios.")
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
                        st.success(f"Salvo: {label}")
                        st.rerun()

    with col_filter:
        status_filter = st.selectbox(
            "Filtrar por status",
            options=["(todos)"] + [s.value for s in LinkStatus],
            format_func=lambda v: v.title() if v != "(todos)" else "Todos os status",
        )
        platform_filter = st.selectbox(
            "Filtrar por plataforma",
            options=["(todas)"] + [p.value for p in Platform],
            format_func=lambda v: v.title() if v != "(todas)" else "Todas as plataformas",
        )

    status_arg = LinkStatus(status_filter) if status_filter != "(todos)" else None
    platform_arg = Platform(platform_filter) if platform_filter != "(todas)" else None
    links = link_repo.list(status=status_arg, platform=platform_arg)

    if not links:
        st.info("Nenhum link no vault ainda. Adicione um acima para começar.")
    else:
        for link in links:
            status_color = {
                LinkStatus.PENDING: ":orange[Pendente]",
                LinkStatus.APPROVED: ":green[Aprovado]",
                LinkStatus.REJECTED: ":red[Rejeitado]",
                LinkStatus.EXPIRED: ":gray[Expirado]",
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

                with st.expander("Construir URL rastreada"):
                    with st.form(f"utm_form_{link.id}"):
                        c1, c2, c3 = st.columns(3)
                        utm_source = c1.text_input("utm_source", value="instagram", key=f"src_{link.id}")
                        utm_medium = c2.text_input("utm_medium", value="organico", key=f"med_{link.id}")
                        utm_campaign = c3.text_input("utm_campaign", value="bio-link", key=f"camp_{link.id}")
                        c4, c5 = st.columns(2)
                        utm_term = c4.text_input("utm_term (opcional)", key=f"term_{link.id}")
                        utm_content = c5.text_input("utm_content (opcional)", key=f"cont_{link.id}")
                        build = st.form_submit_button("Construir")
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
                            st.caption("Valores são normalizados automaticamente — 'Instagram Bio' → 'instagram-bio'.")


with tab_traffic:
    st.subheader("Planejamento de Tráfego")
    st.caption(
        "Gera um calendário de conteúdo orgânico (Sonnet) e variantes de anúncios pagos "
        "(Haiku) para um produto do seu catálogo. Requer ANTHROPIC_API_KEY."
    )

    persisted_products = repo.top_products(limit=100)
    if not persisted_products:
        st.info("Nenhum produto persistido ainda. Rode a descoberta primeiro.")
    else:
        product_labels = {
            sp.product.id: f"{sp.product.name} · {sp.product.platform.value} · R${sp.product.price_brl or 0:.0f}"
            for sp in persisted_products
        }
        selected_id = st.selectbox(
            "Escolha um produto",
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
                "Público-alvo",
                placeholder="ex: Afiliados iniciantes no Brasil, 25–35 anos, lutando para fazer a primeira venda",
                key="tp_audience_input",
            )
        with btn_col:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button(
                "✨ Sugerir",
                use_container_width=True,
                help="Pede ao Haiku para propor um público com base nos metadados do produto",
            ):
                try:
                    from backend.app.traffic_planner import build_default_traffic_planner
                    planner = build_default_traffic_planner()
                    with st.spinner("Consultando o Haiku..."):
                        suggestion = planner.suggest_audience(selected_product)
                    if suggestion:
                        st.session_state["tp_pending_suggestion"] = suggestion
                        st.rerun()
                    else:
                        st.warning("Haiku retornou uma sugestão vazia.")
                except Exception as e:
                    st.error(f"Sugestão falhou: {e}")

        organic_col, paid_col = st.columns(2)

        with organic_col:
            st.markdown("**Plano orgânico (Sonnet 4.6)**")
            organic_channels = st.multiselect(
                "Canais orgânicos",
                options=[c.value for c in ORGANIC_CHANNELS],
                default=[TrafficChannel.INSTAGRAM_REEL.value, TrafficChannel.TIKTOK.value],
                format_func=lambda v: v.replace("_", " ").title(),
            )
            plan_days = st.slider("Horizonte do plano (dias)", 3, 30, 7)
            if st.button("Gerar plano orgânico", type="primary", use_container_width=True):
                if not tp_audience:
                    st.error("Público-alvo é obrigatório.")
                elif not organic_channels:
                    st.error("Escolha pelo menos um canal.")
                else:
                    try:
                        from backend.app.traffic_planner import build_default_traffic_planner
                        planner = build_default_traffic_planner()
                        with st.spinner("Sonnet está escrevendo seu plano de conteúdo..."):
                            plan = planner.plan_organic(
                                product=selected_product,
                                target_audience=tp_audience,
                                channels=[TrafficChannel(c) for c in organic_channels],
                                days=plan_days,
                            )
                        st.session_state["last_organic_plan"] = plan
                    except Exception as e:
                        st.error(f"Plano orgânico falhou: {e}")

        with paid_col:
            st.markdown("**Variantes de anúncios pagos (Haiku 4.5)**")
            ad_platform = st.selectbox(
                "Plataforma de ads",
                options=[c.value for c in PAID_CHANNELS],
                format_func=lambda v: v.replace("_", " ").title(),
            )
            variant_count = st.slider("Número de variantes", 3, 10, 5)
            if st.button("Gerar variantes de anúncio", type="primary", use_container_width=True):
                if not tp_audience:
                    st.error("Público-alvo é obrigatório.")
                else:
                    try:
                        from backend.app.traffic_planner import build_default_traffic_planner
                        planner = build_default_traffic_planner()
                        with st.spinner("Haiku está escrevendo as variantes de copy..."):
                            variants = planner.generate_ad_variants(
                                product=selected_product,
                                target_audience=tp_audience,
                                platform=TrafficChannel(ad_platform),
                                count=variant_count,
                            )
                        st.session_state["last_ad_variants"] = variants
                    except Exception as e:
                        st.error(f"Geração de variantes falhou: {e}")

        plan = st.session_state.get("last_organic_plan")
        if plan:
            st.divider()
            st.markdown("### Plano orgânico")
            st.markdown(f"**Público-alvo:** {plan.target_audience}")
            st.markdown(f"**Posicionamento:** {plan.positioning}")
            st.markdown(f"**Ritmo de postagem:** {plan.posting_rhythm}")
            st.markdown("**Mensagens-chave:**")
            for msg in plan.key_messages:
                st.markdown(f"- {msg}")
            st.markdown(f"**Calendário de conteúdo ({len(plan.briefs)} posts)**")
            for brief in plan.briefs:
                with st.container(border=True):
                    st.markdown(
                        f"**Dia {brief.day_offset} · {brief.channel.value.replace('_', ' ').title()}**"
                    )
                    st.markdown(f"**Hook:** {brief.hook}")
                    st.markdown(f"**Corpo:** {brief.body}")
                    st.markdown(f"**CTA:** {brief.call_to_action}")
                    if brief.hashtags:
                        st.caption(" ".join(f"#{h}" for h in brief.hashtags))
                    if brief.format_notes:
                        st.caption(f"Formato: {brief.format_notes}")

        variants = st.session_state.get("last_ad_variants")
        if variants:
            st.divider()
            st.markdown("### Variantes de anúncios pagos")

            briefs_by_idx = {b.variant_index: b for b in st.session_state.get("last_creative_briefs", [])}

            cbcol1, cbcol2 = st.columns([1, 1])
            with cbcol1:
                if st.button(
                    "🎨 Gerar briefs criativos para todas as variantes",
                    use_container_width=True,
                    help=(
                        "Uma chamada ao Haiku retorna um par de prompts de imagem + vídeo para "
                        "cada variante — prontos para colar no Ideogram v3 (imagens) e Veo 3 / "
                        "Runway Gen-4 (vídeo). Imagens/vídeos concretos são gerados no próximo passo."
                    ),
                ):
                    if not tp_audience:
                        st.error("Público-alvo é obrigatório para gerar briefs criativos.")
                    else:
                        try:
                            from backend.app.services.creatives import (
                                build_default_creative_generator,
                            )

                            gen = build_default_creative_generator()
                            with st.spinner("Haiku está redigindo prompts de imagem + vídeo..."):
                                briefs = gen.briefs(
                                    product=selected_product,
                                    target_audience=tp_audience,
                                    variants=variants,
                                )
                            st.session_state["last_creative_briefs"] = briefs
                            st.rerun()
                        except Exception as e:
                            st.error(f"Geração de briefs criativos falhou: {e}")
            with cbcol2:
                if briefs_by_idx and st.button(
                    "Limpar briefs criativos",
                    use_container_width=True,
                    type="secondary",
                ):
                    st.session_state.pop("last_creative_briefs", None)
                    st.rerun()

            for i, v in enumerate(variants):
                with st.container(border=True):
                    st.markdown(f"**Variante {i + 1} · {v.platform.value.replace('_', ' ').title()}**")
                    st.markdown(f"**Headline:** {v.headline}")
                    st.markdown(f"**Texto principal:** {v.primary_text}")
                    st.markdown(f"**Descrição:** {v.description}")
                    st.caption(f"Público: {v.target_audience}")
                    st.caption(f"Orçamento diário: R$ {v.daily_budget_brl:.0f}")
                    if v.creative_notes:
                        st.caption(f"Criativo: {v.creative_notes}")

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
                            f"🎨 Brief criativo · {brief.aspect_ratio} · {brief.image_tool} + {brief.video_tool}",
                            expanded=True,
                        ):
                            prompt_col, image_col = st.columns([3, 2])
                            with prompt_col:
                                st.markdown(
                                    "**Prompt de imagem** — cole no [Ideogram v3](https://ideogram.ai)"
                                    " ou gere direto abaixo"
                                )
                                st.code(brief.image_prompt, language=None)
                                if has_direct_gen:
                                    if st.button(
                                        "🖼 Gerar imagem agora",
                                        key=f"genimg_{i}",
                                        help="Geração direta via fal.ai ou Replicate. ~R$0.40 por imagem.",
                                    ):
                                        try:
                                            from backend.app.services.creatives import (
                                                build_default_creative_generator,
                                            )

                                            gen = build_default_creative_generator()
                                            if not hasattr(gen, "generate_image"):
                                                st.error("Nenhuma API de geração configurada.")
                                            else:
                                                provider = type(gen).__name__.replace("Generator", "")
                                                with st.spinner(f"Gerando imagem via {provider}..."):
                                                    asset = gen.generate_image(
                                                        product_id=selected_product.id,
                                                        brief=brief,
                                                    )
                                                creative_repo.insert(asset)
                                                st.success(
                                                    f"Imagem gerada (R$ {asset.cost_brl:.2f})"
                                                    if asset.cost_brl
                                                    else "Imagem gerada."
                                                )
                                                st.rerun()
                                        except Exception as e:
                                            st.error(f"Geração de imagem falhou: {e}")
                                else:
                                    st.caption(
                                        ":gray[Defina `REPLICATE_API_TOKEN` ou `FAL_API_KEY` no .env para habilitar geração direta.]"
                                    )
                            with image_col:
                                if latest_image:
                                    st.image(latest_image.asset_url, use_container_width=True)
                                    st.caption(
                                        f"Modelo: {latest_image.model} · "
                                        + (
                                            f"R$ {latest_image.cost_brl:.2f} · "
                                            if latest_image.cost_brl
                                            else ""
                                        )
                                        + f"[Abrir ↗]({latest_image.asset_url})"
                                    )

                            st.divider()

                            vp_col, video_col = st.columns([3, 2])
                            with vp_col:
                                st.markdown(
                                    f"**Prompt de vídeo** ({brief.video_duration_s}s) — cole no "
                                    f"[Veo 3](https://labs.google/veo) ou gere direto abaixo"
                                )
                                st.code(brief.video_prompt, language=None)
                                if has_direct_gen:
                                    if st.button(
                                        "🎬 Gerar vídeo agora",
                                        key=f"genvid_{i}",
                                        help="Geração direta via fal.ai ou Replicate. Veo 3 ~R$15/clip, 2–5 min de espera. Mais barato via REPLICATE_VIDEO_MODEL override.",
                                    ):
                                        try:
                                            from backend.app.services.creatives import (
                                                build_default_creative_generator,
                                            )

                                            gen = build_default_creative_generator()
                                            if not hasattr(gen, "generate_video"):
                                                st.error("Nenhuma API de geração configurada.")
                                            else:
                                                provider = type(gen).__name__.replace("Generator", "")
                                                with st.spinner(
                                                    f"Gerando vídeo via {provider} (2–5 minutos)..."
                                                ):
                                                    asset = gen.generate_video(
                                                        product_id=selected_product.id,
                                                        brief=brief,
                                                    )
                                                creative_repo.insert(asset)
                                                st.success(
                                                    f"Vídeo gerado (R$ {asset.cost_brl:.2f})"
                                                    if asset.cost_brl
                                                    else "Vídeo gerado."
                                                )
                                                st.rerun()
                                        except Exception as e:
                                            st.error(f"Geração de vídeo falhou: {e}")
                            with video_col:
                                if latest_video:
                                    st.video(latest_video.asset_url)
                                    st.caption(
                                        f"Modelo: {latest_video.model} · "
                                        + (
                                            f"R$ {latest_video.cost_brl:.2f} · "
                                            if latest_video.cost_brl
                                            else ""
                                        )
                                        + f"[Abrir ↗]({latest_video.asset_url})"
                                    )
