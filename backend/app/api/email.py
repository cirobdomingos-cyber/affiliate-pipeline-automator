"""Email list + sequences + MailerLite integration (Stage 5).

Three endpoint families:

1. `/email-sequences` — CRUD over the nurture sequences the operator writes
   in the UI. Persisted against a managed product; a future dispatcher will
   read these and actually send the emails. For now, storage-only.

2. `/mailerlite/*` — embed form host, opt-in handler, and on-demand count
   sync. The embed route returns a self-contained HTML snippet meant to be
   iframed from an external site. Opt-in is a classic POST-Redirect-Get so
   external users don't see JSON.

3. The embed snippet itself is generated per-product as a tiny iframe tag the
   operator copy/pastes.
"""

from __future__ import annotations

import html
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from ..db import (
    EmailSequenceRepository,
    ManagedProductRepository,
    SubscriberCountRepository,
)
from ..models import (
    EmailSequence,
    EmailSequenceStep,
    SubscriberCountSnapshot,
)
from ..services.mailerlite import (
    MailerLiteError,
    create_subscriber,
    group_subscriber_count,
)

sequences_router = APIRouter(prefix="/email-sequences", tags=["email-sequences"])
mailerlite_router = APIRouter(prefix="/mailerlite", tags=["mailerlite"])


def get_seq_repo() -> EmailSequenceRepository:
    return EmailSequenceRepository()


def get_sub_repo() -> SubscriberCountRepository:
    return SubscriberCountRepository()


def get_managed_repo() -> ManagedProductRepository:
    return ManagedProductRepository()


class EmailStepUpsert(BaseModel):
    step_order: int = Field(ge=0, le=6)
    delay_days: int = Field(ge=0)
    subject: str
    body: str


class EmailSequenceUpsert(BaseModel):
    managed_product_id: str
    name: str
    mailerlite_group_id: str | None = None
    steps: list[EmailStepUpsert] = Field(default_factory=list, max_length=7)


def _to_sequence(body: EmailSequenceUpsert, *, seq_id: str, created_at: datetime) -> EmailSequence:
    return EmailSequence(
        id=seq_id,
        managed_product_id=body.managed_product_id,
        name=body.name,
        mailerlite_group_id=body.mailerlite_group_id,
        created_at=created_at,
        steps=[
            EmailSequenceStep(
                id=str(uuid.uuid4()),
                sequence_id=seq_id,
                step_order=s.step_order,
                delay_days=s.delay_days,
                subject=s.subject,
                body=s.body,
            )
            for s in body.steps
        ],
    )


@sequences_router.post("", response_model=EmailSequence)
def create_sequence(
    body: EmailSequenceUpsert,
    repo: EmailSequenceRepository = Depends(get_seq_repo),
    managed_repo: ManagedProductRepository = Depends(get_managed_repo),
) -> EmailSequence:
    if managed_repo.get(body.managed_product_id) is None:
        raise HTTPException(status_code=404, detail="managed product not found")
    seq = _to_sequence(body, seq_id=str(uuid.uuid4()), created_at=datetime.now(timezone.utc))
    repo.upsert(seq)
    return seq


@sequences_router.get("", response_model=list[EmailSequence])
def list_sequences(
    managed_product_id: str | None = None,
    repo: EmailSequenceRepository = Depends(get_seq_repo),
) -> list[EmailSequence]:
    return repo.list(managed_product_id=managed_product_id)


@sequences_router.patch("/{sequence_id}", response_model=EmailSequence)
def update_sequence(
    sequence_id: str,
    body: EmailSequenceUpsert,
    repo: EmailSequenceRepository = Depends(get_seq_repo),
) -> EmailSequence:
    existing = repo.get(sequence_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="sequence not found")
    seq = _to_sequence(body, seq_id=sequence_id, created_at=existing.created_at)
    repo.upsert(seq)
    return seq


@sequences_router.delete("/{sequence_id}")
def delete_sequence(
    sequence_id: str,
    repo: EmailSequenceRepository = Depends(get_seq_repo),
) -> dict[str, str]:
    repo.delete(sequence_id)
    return {"status": "deleted", "id": sequence_id}


_EMBED_TEMPLATE = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#fff;padding:20px;color:#111827}}
form{{display:flex;flex-direction:column;gap:12px;max-width:360px;margin:0 auto}}
h3{{font-size:18px;font-weight:700;margin-bottom:4px}}
input{{padding:12px 14px;border:1px solid #d1d5db;border-radius:6px;font-size:15px;width:100%}}
button{{padding:12px 14px;background:#2563eb;color:#fff;border:0;border-radius:6px;font-size:15px;font-weight:600;cursor:pointer}}
button:hover{{background:#1d4ed8}}
.msg{{font-size:14px;color:#059669;text-align:center;padding:16px 0}}
.err{{color:#dc2626}}
</style>
</head>
<body>
<form method="POST" action="/mailerlite/subscribe">
<h3>{title}</h3>
<input type="hidden" name="managed_product_id" value="{product_id}">
<input type="email" name="email" placeholder="seu@email.com" required>
<input type="text" name="name" placeholder="Seu nome (opcional)">
<button type="submit">Quero receber</button>
{message_html}
</form>
</body>
</html>"""


@mailerlite_router.get("/embed/{managed_product_id}", response_class=HTMLResponse)
def embed_form(
    managed_product_id: str,
    success: int = 0,
    error: str | None = None,
    managed_repo: ManagedProductRepository = Depends(get_managed_repo),
) -> HTMLResponse:
    product = managed_repo.get(managed_product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="managed product not found")
    msg_html = ""
    if success:
        msg_html = '<div class="msg">Inscrição confirmada — obrigado!</div>'
    elif error:
        msg_html = f'<div class="msg err">{html.escape(error)}</div>'
    return HTMLResponse(
        content=_EMBED_TEMPLATE.format(
            title=html.escape(product.name),
            product_id=html.escape(managed_product_id),
            message_html=msg_html,
        )
    )


@mailerlite_router.post("/subscribe")
def subscribe(
    managed_product_id: str = Form(...),
    email: str = Form(...),
    name: str | None = Form(default=None),
    seq_repo: EmailSequenceRepository = Depends(get_seq_repo),
) -> RedirectResponse:
    sequences = seq_repo.list(managed_product_id=managed_product_id)
    group_id = next((s.mailerlite_group_id for s in sequences if s.mailerlite_group_id), None)
    if not group_id:
        return RedirectResponse(
            url=f"/mailerlite/embed/{managed_product_id}?error=No+MailerLite+group+configured",
            status_code=302,
        )
    try:
        create_subscriber(email=email, group_id=group_id, name=name)
    except MailerLiteError as exc:
        return RedirectResponse(
            url=f"/mailerlite/embed/{managed_product_id}?error={html.escape(str(exc))[:80]}",
            status_code=302,
        )
    return RedirectResponse(
        url=f"/mailerlite/embed/{managed_product_id}?success=1", status_code=302
    )


@mailerlite_router.post("/sync-counts")
def sync_counts(
    seq_repo: EmailSequenceRepository = Depends(get_seq_repo),
    sub_repo: SubscriberCountRepository = Depends(get_sub_repo),
    managed_repo: ManagedProductRepository = Depends(get_managed_repo),
) -> dict[str, int]:
    """For each managed product with an attached group, re-read the group's
    active subscriber count from MailerLite and persist a snapshot."""
    out: dict[str, int] = {}
    for mp in managed_repo.list():
        sequences = seq_repo.list(managed_product_id=mp.id)
        group_id = next((s.mailerlite_group_id for s in sequences if s.mailerlite_group_id), None)
        if not group_id:
            continue
        try:
            count = group_subscriber_count(group_id)
        except MailerLiteError:
            continue
        sub_repo.save(
            SubscriberCountSnapshot(
                managed_product_id=mp.id,
                count=count,
                synced_at=datetime.now(timezone.utc),
            )
        )
        out[mp.id] = count
    return out
