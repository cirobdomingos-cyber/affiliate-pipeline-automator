"""CLI entrypoint — run discovery once and print top picks.

Useful for cron / scheduler in V1 and as a smoke test in CI.
    py -3.12 scripts/run_discovery.py --mock
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.db import ProductRepository  # noqa: E402
from backend.app.services.discovery import run_discovery  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run affiliate product discovery.")
    parser.add_argument("--mock", action="store_true", help="Use mock fixture data")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    repo = ProductRepository()
    result = asyncio.run(
        run_discovery(
            repo=repo,
            limit_per_source=args.limit,
            top_n=args.top,
            use_mock=args.mock,
        )
    )

    print(f"Fetched: {result.fetched}  Scored: {result.scored}  Sources: {result.sources}")
    if result.errors:
        print("Errors:")
        for err in result.errors:
            print(f"  - {err}")

    print("\nTop picks:")
    for i, sp in enumerate(result.top, 1):
        p, s = sp.product, sp.score
        print(
            f"{i:2}. [{s.score:5.1f}] {p.name[:50]:50s}  "
            f"R${p.price_brl or 0:7.2f}  {p.commission_pct or 0:5.1f}%  "
            f"EPC R${s.expected_value_per_visit:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
