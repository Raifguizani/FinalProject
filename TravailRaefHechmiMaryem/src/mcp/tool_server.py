import os
import re
import sys
from pathlib import Path

os.environ["PYTHONUTF8"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import httpx
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    from fastmcp import FastMCP

mcp = FastMCP("sbt-tools")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(\+?(?:33|49|44|32|34|39|351|216|212|1)[\s.\-]?(?:\d[\s.\-]?){8,11}\d"
    r"|0\d[\s.\-]?(?:\d[\s.\-]?){7,8}\d)"
    r"(?!\d)"
)

SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")
SERPER_URL     = "https://google.serper.dev/search"


# -----------------------------------------------------------------------
# Outils MCP
# -----------------------------------------------------------------------

@mcp.tool()
def search_web(query: str, max_results: int = 10) -> list[dict]:
    """
    Recherche via Serper (Google) — retourne titre, url, body.
    Fallback sur DuckDuckGo si SERPER_API_KEY absent.
    """
    if SERPER_API_KEY:
        return _search_serper(query, max_results)
    return _search_ddg(query, max_results)


def _search_serper(query: str, max_results: int) -> list[dict]:
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                SERPER_URL,
                headers={
                    "X-API-KEY":    SERPER_API_KEY,
                    "Content-Type": "application/json",
                },
                json={"q": query, "num": min(max_results, 10)},
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for r in data.get("organic", []):
            results.append({
                "title": r.get("title", ""),
                "url":   r.get("link", ""),
                "body":  r.get("snippet", "")[:400],
            })
        return results
    except Exception as e:
        return [{"title": "", "url": "", "body": f"Erreur Serper: {e}"}]


def _search_ddg(query: str, max_results: int) -> list[dict]:
    """Fallback DuckDuckGo si pas de clé Serper."""
    try:
        from ddgs import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=min(max_results, 20)):
                results.append({
                    "title": r.get("title", ""),
                    "url":   r.get("href", ""),
                    "body":  r.get("body", "")[:400],
                })
        return results
    except Exception as e:
        return [{"title": "", "url": "", "body": f"Erreur DDG: {e}"}]


@mcp.tool()
async def scrape_url(url: str, max_chars: int = 20000) -> dict:
    """Scraping d'une URL avec Crawl4AI."""
    return await _scrape_url_async(url, max_chars)


async def _scrape_url_async(url: str, max_chars: int) -> dict:
    config = CrawlerRunConfig(cache_mode=CacheMode.BYPASS)

    _real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        async with AsyncWebCrawler(verbose=False) as crawler:
            result = await crawler.arun(url=url, config=config)
    finally:
        sys.stdout = _real_stdout

    if not result.success:
        return {
            "url":      url,
            "status":   "error",
            "error":    result.error_message or "Echec du crawl",
            "markdown": "",
            "emails":   [],
            "phones":   [],
        }

    md = ""
    if result.markdown:
        md = (result.markdown.raw_markdown or "")[:max_chars]

    emails = sorted(set(EMAIL_RE.findall(md)))
    phones = sorted(set(PHONE_RE.findall(md)))

    return {
        "url":      url,
        "status":   "ok",
        "markdown": md,
        "emails":   emails[:20],
        "phones":   phones[:20],
    }


@mcp.tool()
def extract_contacts(text: str) -> dict:
    """Extrait emails et téléphones depuis un texte brut."""
    emails = sorted(set(EMAIL_RE.findall(text or "")))
    phones = sorted(set(PHONE_RE.findall(text or "")))
    return {"emails": emails, "phones": phones}


if __name__ == "__main__":
    mcp.run()
