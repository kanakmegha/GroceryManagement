"""Public recipe reference discovery with robots.txt compliance and content verification."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, Field

USER_AGENT = "GroceryManagementRecipeBot/1.0"
DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"


class RecipeReference(BaseModel):
    """Verified public recipe reference discovered from the web."""

    title: str
    url: str
    source: str
    snippet: str | None = None
    matched_ingredients: list[str] = Field(default_factory=list)
    verified: bool = True


class _SearchLinkParser(HTMLParser):
    """Extract candidate result URLs from DuckDuckGo HTML search results."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        href = attr_map.get("href")
        if not href:
            return
        if "duckduckgo.com/l/?" in href or href.startswith("/l/?"):
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            uddg = qs.get("uddg")
            if uddg and uddg[0].startswith(("http://", "https://")):
                self.links.append(unquote(uddg[0]))
        elif href.startswith(("http://", "https://")) and "duckduckgo.com" not in href:
            cls = attr_map.get("class") or ""
            if "result__a" in cls or "result__url" in cls or not cls:
                self.links.append(href)


class _RecipePageParser(HTMLParser):
    """Extract <title>, <meta name='description'>, and application/ld+json blocks."""

    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self._in_json_ld = False
        self._title_parts: list[str] = []
        self._json_ld_parts: list[str] = []
        self._text_parts: list[str] = []
        self._skip_text = False
        self.title: str = ""
        self.description: str | None = None
        self.json_ld_blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        attr_map = {k.lower(): v for k, v in attrs}
        if t == "title":
            self._in_title = True
        elif t == "script":
            self._skip_text = True
            if (attr_map.get("type") or "").lower().split(";")[0].strip() == "application/ld+json":
                self._in_json_ld = True
                self._json_ld_parts = []
        elif t == "style":
            self._skip_text = True
        elif t == "meta":
            name = (attr_map.get("name") or attr_map.get("property") or "").lower()
            if name in {"description", "og:description"} and not self.description:
                content = (attr_map.get("content") or "").strip()
                if content:
                    self.description = content

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "title":
            self._in_title = False
            self.title = " ".join("".join(self._title_parts).split())
        elif t == "script":
            if self._in_json_ld:
                raw = "".join(self._json_ld_parts).strip()
                if raw:
                    self.json_ld_blocks.append(raw)
                self._in_json_ld = False
            self._skip_text = False
        elif t == "style":
            self._skip_text = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._in_json_ld:
            self._json_ld_parts.append(data)
        if not self._skip_text:
            self._text_parts.append(data)

    @property
    def body_text(self) -> str:
        return " ".join("".join(self._text_parts).split()).lower()


def _extract_recipe_ld_objects(blocks: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                _walk(item)
        elif isinstance(node, dict):
            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            if any(isinstance(t, str) and t.lower().endswith("recipe") for t in types):
                found.append(node)
            if "@graph" in node:
                _walk(node["@graph"])

    for block in blocks:
        try:
            parsed = json.loads(block)
            _walk(parsed)
        except json.JSONDecodeError:
            continue
    return found


async def _is_allowed_by_robots(
    client: httpx.AsyncClient,
    url: str,
    cache: dict[str, RobotFileParser | None],
) -> bool:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in cache:
        robots_url = f"{origin}/robots.txt"
        rp = RobotFileParser()
        try:
            resp = await client.get(robots_url, headers={"User-Agent": USER_AGENT})
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                cache[origin] = rp
            elif resp.status_code in {404, 410}:
                # No robots.txt means crawling is permitted by standard convention
                rp.parse([])
                cache[origin] = rp
            else:
                cache[origin] = None
        except (httpx.RequestError, httpx.TimeoutException):
            cache[origin] = None

    cached_rp = cache[origin]
    if cached_rp is None:
        return False
    return cached_rp.can_fetch(USER_AGENT, url)


def _tokenize(text: str) -> set[str]:
    stop = {"with", "and", "the", "for", "easy", "quick", "recipe", "bowl", "style"}
    return {tok for tok in re.findall(r"[a-z]{3,}", text.lower()) if tok not in stop}


def _validate_recipe_page(
    *,
    url: str,
    html: str,
    recipe_title: str,
    key_ingredients: list[str],
) -> RecipeReference | None:
    parser = _RecipePageParser()
    parser.feed(html)

    ld_recipes = _extract_recipe_ld_objects(parser.json_ld_blocks)
    ld_name = ""
    ld_ingredients_text = ""
    ld_description: str | None = None

    if ld_recipes:
        first = ld_recipes[0]
        if isinstance(first.get("name"), str):
            ld_name = first["name"].strip()
        if isinstance(first.get("description"), str):
            ld_description = first["description"].strip()
        raw_ings = first.get("recipeIngredient")
        if isinstance(raw_ings, list):
            ld_ingredients_text = " ".join(str(x).lower() for x in raw_ings)

    display_title = ld_name or parser.title
    if not display_title:
        return None

    title_tokens = _tokenize(recipe_title)
    page_title_tokens = _tokenize(display_title)
    title_overlap = len(title_tokens & page_title_tokens)

    searchable_text = f"{display_title.lower()} {ld_ingredients_text} {parser.body_text}"
    matched_ingredients = [
        ing for ing in key_ingredients if ing.strip() and ing.strip().lower() in searchable_text
    ]

    # Require either meaningful title overlap or multiple matched ingredients + recipe context
    is_related = (title_overlap >= 1 and (bool(matched_ingredients) or bool(ld_recipes))) or (
        len(matched_ingredients) >= 2
    )
    if not is_related:
        return None

    source = urlparse(url).netloc.removeprefix("www.")
    snippet = ld_description or parser.description
    if snippet and len(snippet) > 220:
        snippet = snippet[:217].rstrip() + "..."

    return RecipeReference(
        title=display_title,
        url=url,
        source=source,
        snippet=snippet,
        matched_ingredients=matched_ingredients,
        verified=True,
    )


async def discover_recipe_references(
    recipe_title: str,
    key_ingredients: list[str],
    limit: int = 2,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[RecipeReference]:
    """Search public recipe pages, verify robots.txt & content match, and return verified links."""
    if not recipe_title.strip() or limit <= 0:
        return []

    query_terms = [recipe_title.strip(), *key_ingredients[:3], "recipe"]
    query = " ".join(term for term in query_terms if term)

    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=6.0,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )

    try:
        search_resp = await active_client.get(
            DUCKDUCKGO_HTML_URL,
            params={"q": query},
        )
        if search_resp.status_code != 200:
            return []

        link_parser = _SearchLinkParser()
        link_parser.feed(search_resp.text)

        seen_urls: set[str] = set()
        candidate_urls: list[str] = []
        for url in link_parser.links:
            if url not in seen_urls:
                seen_urls.add(url)
                candidate_urls.append(url)

        robots_cache: dict[str, RobotFileParser | None] = {}
        results: list[RecipeReference] = []

        for url in candidate_urls[: max(limit * 3, 6)]:
            if len(results) >= limit:
                break
            if not await _is_allowed_by_robots(active_client, url, robots_cache):
                continue
            try:
                page_resp = await active_client.get(url)
            except (httpx.RequestError, httpx.TimeoutException):
                continue
            if page_resp.status_code != 200:
                continue
            content_type = page_resp.headers.get("content-type", "").lower()
            if "html" not in content_type and content_type != "":
                continue

            ref = _validate_recipe_page(
                url=str(page_resp.url),
                html=page_resp.text,
                recipe_title=recipe_title,
                key_ingredients=key_ingredients,
            )
            if ref is not None:
                results.append(ref)

        return results
    except (httpx.RequestError, httpx.TimeoutException):
        return []
    finally:
        if owns_client:
            await active_client.aclose()
