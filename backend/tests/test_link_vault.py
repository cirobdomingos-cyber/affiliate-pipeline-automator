"""LinkRepository round-trip tests against a tmp_path DuckDB file."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.db import LinkRepository
from backend.app.models import LinkStatus, Platform
from backend.app.services.link_vault import add_link, make_link_id


@pytest.fixture
def repo(tmp_path: Path) -> LinkRepository:
    return LinkRepository(db_path=tmp_path / "links.duckdb")


class TestLinkVaultCRUD:
    def test_add_and_get(self, repo):
        link = add_link(
            repo,
            platform=Platform.HOTMART,
            label="Curso X - Instagram",
            raw_url="https://hotmart.com/pt-br/produto/X",
            notes="Bio link for Instagram",
            tags=["instagram", "organic"],
        )
        assert link.id == make_link_id(Platform.HOTMART, "https://hotmart.com/pt-br/produto/X")
        assert link.approval_status == LinkStatus.PENDING
        assert link.approved_at is None

        fetched = repo.get(link.id)
        assert fetched is not None
        assert fetched.label == "Curso X - Instagram"
        assert fetched.tags == ["instagram", "organic"]

    def test_set_status_approved_sets_approved_at(self, repo):
        link = add_link(
            repo,
            platform=Platform.MONETIZZE,
            label="Test",
            raw_url="https://monetizze.com.br/produto/Y",
        )
        updated = repo.set_status(link.id, LinkStatus.APPROVED)
        assert updated.approval_status == LinkStatus.APPROVED
        assert updated.approved_at is not None

    def test_set_status_rejected_does_not_set_approved_at(self, repo):
        link = add_link(
            repo,
            platform=Platform.EDUZZ,
            label="Test",
            raw_url="https://eduzz.com/produto/Z",
        )
        updated = repo.set_status(link.id, LinkStatus.REJECTED)
        assert updated.approval_status == LinkStatus.REJECTED
        assert updated.approved_at is None

    def test_list_filters_by_status(self, repo):
        add_link(repo, platform=Platform.HOTMART, label="A", raw_url="https://x.com/1")
        l2 = add_link(repo, platform=Platform.HOTMART, label="B", raw_url="https://x.com/2")
        repo.set_status(l2.id, LinkStatus.APPROVED)

        pending = repo.list(status=LinkStatus.PENDING)
        approved = repo.list(status=LinkStatus.APPROVED)
        assert len(pending) == 1
        assert len(approved) == 1
        assert pending[0].label == "A"
        assert approved[0].label == "B"

    def test_list_filters_by_platform(self, repo):
        add_link(repo, platform=Platform.HOTMART, label="A", raw_url="https://hotmart.com/1")
        add_link(repo, platform=Platform.MONETIZZE, label="B", raw_url="https://monetizze.com.br/1")

        hotmart_only = repo.list(platform=Platform.HOTMART)
        assert len(hotmart_only) == 1
        assert hotmart_only[0].platform == Platform.HOTMART

    def test_upsert_is_idempotent(self, repo):
        link = add_link(
            repo, platform=Platform.HOTMART, label="Original", raw_url="https://x.com/dupe"
        )
        # Adding the same URL+platform again uses the same ID, so it replaces.
        link2 = add_link(
            repo, platform=Platform.HOTMART, label="Updated", raw_url="https://x.com/dupe"
        )
        assert link.id == link2.id
        all_links = repo.list()
        assert len(all_links) == 1
        assert all_links[0].label == "Updated"
