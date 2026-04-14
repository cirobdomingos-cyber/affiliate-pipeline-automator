"""One-off probe: fetch live Hotmart marketplace, parse Next.js hydration JSON,
locate the products array, and print sample fields."""

from __future__ import annotations

import asyncio
import json
import re

import httpx


_URL = "https://hotmart.com/pt-br/marketplace"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}


def find_products_in_next_data(tree: object) -> list[dict]:
    """DFS the Next.js hydration tree looking for a list whose items contain
    `producerReferenceCode`. Returns the first such list found."""
    if isinstance(tree, list):
        if tree and isinstance(tree[0], dict) and "producerReferenceCode" in tree[0]:
            return tree
        for item in tree:
            result = find_products_in_next_data(item)
            if result:
                return result
    elif isinstance(tree, dict):
        for value in tree.values():
            result = find_products_in_next_data(value)
            if result:
                return result
    return []


def walk_keys(tree: object, depth: int = 0, max_depth: int = 6) -> None:
    """Print the structural outline so we understand where products live."""
    prefix = "  " * depth
    if depth > max_depth:
        return
    if isinstance(tree, dict):
        for k, v in tree.items():
            if isinstance(v, (dict, list)):
                marker = ""
                if isinstance(v, list):
                    marker = f" (list len={len(v)})"
                elif isinstance(v, dict):
                    marker = f" (dict keys={len(v)})"
                print(f"{prefix}{k}{marker}")
                walk_keys(v, depth + 1, max_depth)


async def main() -> None:
    async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS, follow_redirects=True) as c:
        r = await c.get(_URL)

    scripts = re.findall(r"<script[^>]*>(.*?)</script>", r.text, re.DOTALL)
    data_script = next((s for s in scripts if s.lstrip().startswith('{"props"')), None)
    if data_script is None:
        print("no hydration blob found")
        return

    tree = json.loads(data_script)
    print("Top-level structural outline (depth <=4):")
    walk_keys(tree, max_depth=4)
    print()

    products = find_products_in_next_data(tree)
    print(f"\nExtracted {len(products)} products.")
    for p in products[:3]:
        print()
        print(f"  name        : {p.get('name')}")
        print(f"  title       : {p.get('title')}")
        print(f"  category    : {p.get('category')}")
        print(f"  topic       : {p.get('topic')}")
        print(f"  ownerName   : {p.get('ownerName')}")
        print(f"  rating      : {p.get('rating')}")
        print(f"  totalReviews: {p.get('totalReviews')}")
        print(f"  ref         : {p.get('producerReferenceCode')}")
        print(f"  slug        : {p.get('slug')}")
        print(f"  keys        : {sorted(p.keys())[:25]}")


if __name__ == "__main__":
    asyncio.run(main())
