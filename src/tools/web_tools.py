# src/tools/web_tools.py — replace entire file with:

import logging
import webbrowser

from src.tools.base import safe_tool
from src.utils.logger import get_logger

log = get_logger(__name__)


@safe_tool("Open URL")
def open_url_in_browser(url: str) -> str:
    log.info("open_url", url=url)

    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url

    webbrowser.open(url)
    return f"Opened URL:\n{url}"


ALL_WEB_TOOLS = [
    open_url_in_browser,
]