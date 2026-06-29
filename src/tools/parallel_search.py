"""
parallel_search.py
──────────────────
A single LangChain tool that fans out a query across multiple search
engines simultaneously, scrapes the top results in parallel, and returns
a de-duplicated, ranked digest.

Engines (all free, no API key):
  1. DuckDuckGo HTML   – primary, bot-friendly
  2. Bing HTML         – broad index, different ranking
  3. Brave Search HTML – privacy-focused, often different results

Scraping happens in a single shared ThreadPoolExecutor across all
engines so the total wall-clock time ≈ slowest single engine + scrape,
not the sum of all engines.
"""

import re
import html
import logging
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, unquote, parse_qs

from langchain.tools import tool

from src.utils.logger import get_logger, TimedBlock
from src.utils.timeout import TIMEOUTS

log = get_logger(__name__)


# =========================================================
# SHARED HTTP SESSION
# =========================================================

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})


# =========================================================
# TEXT HELPERS
# =========================================================

def _strip(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return url


# =========================================================
# ENGINE PARSERS
# =========================================================

def _ddg(query: str) -> list[dict]:
    """DuckDuckGo HTML scrape."""
    try:
        from bs4 import BeautifulSoup
        with TimedBlock(log, "parallel_search.ddg", query=query):
            r = _SESSION.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                timeout=TIMEOUTS.HTTP_SEARCH,
            )
        if r.status_code != 200:
            log.warning("parallel_search.ddg.bad_status", status=r.status_code)
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for el in soup.select(".result"):
            a = el.select_one(".result__title a")
            snip = el.select_one(".result__snippet")
            if not a:
                continue
            url = a.get("href", "")
            # unwrap DDG redirect
            if "uddg=" in url:
                url = unquote(parse_qs(urlparse(url).query).get("uddg", [url])[0])
            title = a.get_text(strip=True)
            desc  = snip.get_text(strip=True) if snip else ""
            if url and not url.startswith("/") and title:
                results.append({"title": title, "url": url,
                                 "description": desc, "engine": "ddg"})
        log.info("parallel_search.ddg.done", count=len(results))
        return results

    except requests.Timeout:
        log.warning("parallel_search.ddg.timeout")
        return []
    except Exception:
        log.exception("parallel_search.ddg.error")
        return []


def _bing(query: str) -> list[dict]:
    """Bing HTML scrape."""
    try:
        from bs4 import BeautifulSoup
        with TimedBlock(log, "parallel_search.bing", query=query):
            r = _SESSION.get(
                "https://www.bing.com/search",
                params={"q": query, "setlang": "en"},
                timeout=TIMEOUTS.HTTP_SEARCH,
            )
        if r.status_code != 200:
            log.warning("parallel_search.bing.bad_status", status=r.status_code)
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a")
            snip = li.select_one(".b_caption p")
            if not a:
                continue
            url   = a.get("href", "")
            title = a.get_text(strip=True)
            desc  = snip.get_text(strip=True) if snip else ""
            if url.startswith("http") and title:
                results.append({"title": title, "url": url,
                                 "description": desc, "engine": "bing"})
        log.info("parallel_search.bing.done", count=len(results))
        return results

    except requests.Timeout:
        log.warning("parallel_search.bing.timeout")
        return []
    except Exception:
        log.exception("parallel_search.bing.error")
        return []


def _brave(query: str) -> list[dict]:
    """Brave Search HTML scrape."""
    try:
        from bs4 import BeautifulSoup
        with TimedBlock(log, "parallel_search.brave", query=query):
            r = _SESSION.get(
                "https://search.brave.com/search",
                params={"q": query, "source": "web"},
                timeout=TIMEOUTS.HTTP_SEARCH,
            )
        if r.status_code != 200:
            log.warning("parallel_search.brave.bad_status", status=r.status_code)
            return []

        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for div in soup.select("[data-type='web'] .snippet"):
            a     = div.select_one("a.heading-serpresult")
            snip  = div.select_one(".snippet-description")
            if not a:
                continue
            url   = a.get("href", "")
            title = a.get_text(strip=True)
            desc  = snip.get_text(strip=True) if snip else ""
            if url.startswith("http") and title:
                results.append({"title": title, "url": url,
                                 "description": desc, "engine": "brave"})
        log.info("parallel_search.brave.done", count=len(results))
        return results

    except requests.Timeout:
        log.warning("parallel_search.brave.timeout")
        return []
    except Exception:
        log.exception("parallel_search.brave.error")
        return []


# =========================================================
# PAGE SCRAPER  (same as web_tools, kept local to avoid
#                circular imports)
# =========================================================

def _scrape(url: str, query: str, max_chars: int = 3000) -> str:
    try:
        import trafilatura
        from readability import Document
        from bs4 import BeautifulSoup

        r = _SESSION.get(url, timeout=TIMEOUTS.HTTP_SCRAPE, allow_redirects=True)
        if r.status_code != 200:
            return ""

        raw = r.text

        text = None
        try:
            text = trafilatura.extract(raw, include_links=False,
                                       favor_precision=True, deduplicate=True)
            if text and len(text) < 200:
                text = None
        except Exception:
            pass

        if not text:
            try:
                doc  = Document(raw)
                soup = BeautifulSoup(doc.summary(), "html.parser")
                text = soup.get_text(" ", strip=True)
                if len(text) < 200:
                    text = None
            except Exception:
                pass

        if not text:
            return ""

        text = re.sub(r"\s+", " ", text).strip()

        # Score and pick best chunks
        chunks  = [text[i:i+1200] for i in range(0, len(text), 1200)]
        words   = query.lower().split()
        scored  = sorted(chunks,
                         key=lambda c: sum(c.lower().count(w) for w in words),
                         reverse=True)
        text = "\n\n".join(c.strip() for c in scored[:3] if len(c.strip()) > 100)

        if len(text) > max_chars:
            text = text[:max_chars] + "..."

        return f"[{_domain(url)}]\n{text}"

    except requests.Timeout:
        log.warning("parallel_search.scrape.timeout", url=url)
        return ""
    except Exception:
        log.debug("parallel_search.scrape.error", url=url)
        return ""


# =========================================================
# DE-DUPLICATION + MERGE
# =========================================================

def _deduplicate(all_results: list[dict], top_n: int = 8) -> list[dict]:
    """
    Merge results from multiple engines.
    - De-duplicate by domain (keep first occurrence).
    - Rank by engine agreement: results appearing in more engines rank higher.
    - Cap at top_n.
    """
    domain_seen: dict[str, dict] = {}
    engine_count: dict[str, int] = {}

    for r in all_results:
        d = _domain(r["url"])
        if d in domain_seen:
            engine_count[d] += 1
        else:
            domain_seen[d] = r
            engine_count[d] = 1

    ranked = sorted(domain_seen.values(),
                    key=lambda r: engine_count[_domain(r["url"])],
                    reverse=True)
    return ranked[:top_n]


# =========================================================
# PARALLEL SEARCH TOOL
# =========================================================

@tool(description=(
    "Search the web in parallel across DuckDuckGo, Bing, and Brave, "
    "then scrape the top results. Returns a rich, de-duplicated digest. "
    "Use this for any web search or research task."
))
def parallel_search(query: str) -> str:
    """Fan-out search across three engines, scrape results in parallel."""
    log.info("parallel_search.start", query=query)

    # ── Phase 1: hit all three search engines at the same time ──
    with TimedBlock(log, "parallel_search.engines"):
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="srch-eng") as ex:
            futures = {
                ex.submit(_ddg,   query): "ddg",
                ex.submit(_bing,  query): "bing",
                ex.submit(_brave, query): "brave",
            }
            all_raw: list[dict] = []
            for fut in as_completed(futures, timeout=TIMEOUTS.HTTP_SEARCH + 3):
                engine = futures[fut]
                try:
                    all_raw.extend(fut.result())
                except Exception:
                    log.warning("parallel_search.engine_failed", engine=engine)

    log.info("parallel_search.raw_total", count=len(all_raw))

    if not all_raw:
        return f"No search results found for: {query}"

    # ── Phase 2: de-duplicate and pick top results ───────────
    merged = _deduplicate(all_raw, top_n=6)
    log.info("parallel_search.after_dedup", count=len(merged))

    # ── Phase 3: scrape all top pages in parallel ────────────
    with TimedBlock(log, "parallel_search.scrape"):
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="srch-scrp") as ex:
            scrape_futures = {
                ex.submit(_scrape, r["url"], query): r
                for r in merged
            }
            for fut in as_completed(scrape_futures,
                                    timeout=TIMEOUTS.HTTP_SCRAPE + 5):
                r = scrape_futures[fut]
                try:
                    content = fut.result()
                    if content:
                        r["page_content"] = content
                except Exception:
                    pass

    # ── Phase 4: format output ────────────────────────────────
    lines = []
    for i, r in enumerate(merged, 1):
        engines_tag = f"[{r['engine']}]"
        entry = (
            f"[{i}] {r['title']} {engines_tag}\n"
            f"URL: {r['url']}\n"
            f"Snippet: {r.get('description', '')}"
        )
        if r.get("page_content"):
            entry += f"\n\n{r['page_content']}"
        lines.append(entry)

    result = "\n\n" + ("─" * 60 + "\n\n").join(lines)
    log.info("parallel_search.done",
             result_count=len(merged), total_chars=len(result))
    return result
