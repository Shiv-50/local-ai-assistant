import logging
import re
import html
import requests
import webbrowser

from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import trafilatura
from readability import Document
from bs4 import BeautifulSoup

from src.tools.base import safe_tool
from src.utils.logger import get_logger, TimedBlock
from src.utils.timeout import TIMEOUTS

log = get_logger(__name__)


# =========================================================
# TEXT UTILITIES
# =========================================================

def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_html_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# =========================================================
# EXTRACTION STRATEGIES
# =========================================================

def extract_with_trafilatura(raw_html: str) -> str | None:
    try:
        extracted = trafilatura.extract(
            raw_html,
            include_links=False,
            include_images=False,
            favor_precision=True,
            deduplicate=True,
        )
        if extracted and len(extracted) > 200:
            return extracted
    except Exception:
        pass
    return None


def extract_with_readability(raw_html: str) -> str | None:
    try:
        doc = Document(raw_html)
        soup = BeautifulSoup(doc.summary(), "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        if len(text) > 200:
            return text
    except Exception:
        pass
    return None


def extract_basic(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "noscript", "svg", "iframe"]):
        tag.decompose()
    paragraphs = soup.find_all("p")
    return " ".join(
        p.get_text(" ", strip=True)
        for p in paragraphs
        if len(p.get_text(strip=True)) > 40
    )


# =========================================================
# CHUNKING
# =========================================================

def split_into_chunks(text: str, chunk_size: int = 1200):
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def score_chunk(chunk: str, query: str) -> int:
    query_words = query.lower().split()
    chunk_lower = chunk.lower()
    return sum(chunk_lower.count(w) for w in query_words)


def get_relevant_content(text: str, query: str, top_k: int = 3) -> str:
    chunks = split_into_chunks(text)
    scored = sorted(
        [(score_chunk(c, query), c) for c in chunks],
        reverse=True,
        key=lambda x: x[0],
    )
    selected = [
        c.strip() for _, c in scored[:top_k]
        if len(c.strip()) > 100
    ]
    return "\n\n".join(selected)


# =========================================================
# SCRAPER
# =========================================================

_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    )
}


def scrape_page_content(url: str, query: str = "", max_chars: int = 4000) -> str:
    domain = urlparse(url).netloc

    try:
        with TimedBlock(log, "scrape.get", url=url):
            response = requests.get(
                url,
                headers=_SCRAPE_HEADERS,
                timeout=TIMEOUTS.HTTP_SCRAPE,   # was hard-coded 15s – now centralised
                allow_redirects=True,
            )

        if response.status_code != 200:
            log.warning("scrape.bad_status", url=url, status=response.status_code)
            return ""

        raw = response.text

        text = (
            extract_with_trafilatura(raw)
            or extract_with_readability(raw)
            or extract_basic(raw)
        )

        text = clean_text(text or "")
        if not text:
            log.debug("scrape.no_content", url=url)
            return ""

        if query:
            text = get_relevant_content(text, query)

        if len(text) > max_chars:
            text = text[:max_chars] + "..."

        log.debug("scrape.success", domain=domain, chars=len(text))
        return f"[Source: {domain}]\n{text}"

    except requests.Timeout:
        log.warning("scrape.timeout", url=url, timeout_s=TIMEOUTS.HTTP_SCRAPE)
        return ""
    except requests.RequestException as e:
        log.debug("scrape.request_error", url=url, error=str(e))
        return ""
    except Exception:
        log.debug("scrape.unexpected_error", url=url)
        return ""


# =========================================================
# YAHOO PARSER
# =========================================================

def clean_yahoo_title(raw_title: str) -> str:
    cleaned = re.sub(r"^[A-Za-z0-9\s\.\-]+https?://[^\s]*[\s›]+", "", raw_title)
    cleaned = re.sub(r"^[A-Za-z0-9\.\-]+https?://\S+\s*", "", cleaned)
    cleaned = cleaned.lstrip("› ").strip()
    return cleaned if len(cleaned) > 3 else raw_title


def parse_yahoo_regex(html_content: str) -> list[dict]:
    import urllib.parse
    results = []
    blocks = re.split(r'class="compTitle\b', html_content)

    for b in blocks[1:]:
        url_match = re.search(r'href="([^"]+)"', b)
        if not url_match:
            continue

        url = url_match.group(1)
        match = re.search(r"/RU=([^/]+)", url)
        if match:
            url = urllib.parse.unquote(match.group(1))

        if (
            url.startswith("/")
            or "search.yahoo.com" in url
            or "bing.com/aclick" in url
            or "yahoo.com/aclick" in url
        ):
            continue

        title_match = re.search(r"<a[^>]*>([\s\S]*?)</a>", b)
        title = title_match.group(1) if title_match else ""

        desc_match = re.search(
            r'class="[^"]*(?:compText|desc)[^"]*"[^>]*>([\s\S]*?)</div>', b
        )
        desc = desc_match.group(1) if desc_match else ""

        if not desc:
            alt = re.search(r"</h3>[\s\S]*?<div[^>]*>([\s\S]*?)</div>", b)
            if alt:
                desc = alt.group(1)

        title_c = clean_yahoo_title(strip_html_tags(title))
        desc_c = strip_html_tags(desc)

        if "Ad ·" in title_c:
            continue

        if url and title_c and len(title_c) > 3:
            results.append({"title": title_c, "url": url, "description": desc_c})

    return results


# =========================================================
# SEARCH TOOL
# =========================================================

@safe_tool("Web Search")
def search_web(query: str) -> str:
    log.info("web_search.start", query=query)

    headers = _SCRAPE_HEADERS
    results: list[dict] = []

    # ── Yahoo search ──────────────────────────────────────────
    try:
        with TimedBlock(log, "yahoo_search", query=query):
            response = requests.get(
                "https://search.yahoo.com/search",
                params={"q": query},
                headers=headers,
                timeout=TIMEOUTS.HTTP_SEARCH,   # was hard-coded 12s
            )

        if response.status_code == 200:
            results = parse_yahoo_regex(response.text)
            log.info("yahoo_search.results", count=len(results))
        else:
            log.warning("yahoo_search.bad_status", status=response.status_code)

    except requests.Timeout:
        log.warning("yahoo_search.timeout", timeout_s=TIMEOUTS.HTTP_SEARCH)
    except Exception:
        log.exception("yahoo_search.error")

    if not results:
        log.info("web_search.no_results", query=query)
        return f"No results found for: {query}"

    # ── Parallel page scraping ────────────────────────────────
    log.info("web_search.scraping_pages", urls=len(results[:5]))

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_map = {
            executor.submit(scrape_page_content, r["url"], query, 5000): r
            for r in results[:5]
        }
        for future in as_completed(
            future_map,
            timeout=TIMEOUTS.HTTP_SCRAPE + 5,  # overall scrape budget
        ):
            r = future_map[future]
            try:
                content = future.result()
                if content:
                    r["page_content"] = content
            except Exception:
                log.debug("scrape.future_error", url=r.get("url"))

    # ── Format ────────────────────────────────────────────────
    formatted = []
    for idx, r in enumerate(results[:5]):
        entry = (
            f"[{idx + 1}] {r['title']}\n"
            f"URL: {r['url']}\n"
            f"Snippet: {r['description']}"
        )
        if r.get("page_content"):
            entry += f"\n\n{r['page_content']}"
        formatted.append(entry)

    final_content = "\n\n".join(formatted)
    log.info("web_search.done", result_count=len(results[:5]), total_chars=len(final_content))
    return final_content


# =========================================================
# OPEN URL
# =========================================================

@safe_tool("Open URL")
def open_url_in_browser(url: str) -> str:
    log.info("open_url", url=url)

    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url

    webbrowser.open(url)
    return f"Opened URL:\n{url}"


# =========================================================
# EXPORTS
# =========================================================

ALL_WEB_TOOLS = [
    search_web,
    open_url_in_browser,
]
