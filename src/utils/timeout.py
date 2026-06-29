"""
Request timeout utilities.

Provides:
- run_with_timeout()    – wraps a callable in a thread with a hard deadline
- async_with_timeout()  – wraps a coroutine with asyncio.wait_for
- TIMEOUTS              – single place to tune all deadlines
"""

import asyncio
import concurrent.futures
import logging
from typing import Callable, TypeVar

# Use a plain stdlib logger here to avoid a circular import
# (logger.py → timeout.py would be fine, but timeout.py → logger.py is not).
# All log calls use plain string formatting so no KwargsLogger is needed.
_log = logging.getLogger("assistant.timeout")

T = TypeVar("T")


# ─────────────────────────────────────────────
# Central timeout registry
# ─────────────────────────────────────────────

class TIMEOUTS:
    """All timeout values in seconds.  Edit here; nowhere else."""

    LLM_INFERENCE:    int = 120   # local Ollama
    REMOTE_LLM:       int = 30    # Gemini / cloud
    HTTP_SCRAPE:      int = 15    # page scraping
    HTTP_SEARCH:      int = 12    # Yahoo search
    OLLAMA_UNLOAD:    int = 5
    MCP_TOOL:         int = 60    # Playwright
    ORCHESTRATOR:     int = 180   # full request budget
    SHELL_COMMAND:    int = 15
    POWERSHELL_QUERY: int = 20


# ─────────────────────────────────────────────
# Sync wrapper
# ─────────────────────────────────────────────

def run_with_timeout(
    fn: Callable[..., T],
    *args,
    timeout: float,
    operation: str = "operation",
    **kwargs,
) -> T:
    """Run *fn* in a thread pool; raise TimeoutError if it takes too long."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            _log.error(f"timeout | operation={operation} | timeout_s={timeout}")
            raise TimeoutError(f"{operation} timed out after {timeout}s") from None


# ─────────────────────────────────────────────
# Async wrapper
# ─────────────────────────────────────────────

async def async_with_timeout(coro, timeout: float, operation: str = "async_operation"):
    """Wrap a coroutine with asyncio.wait_for and log on timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        _log.error(f"async_timeout | operation={operation} | timeout_s={timeout}")
        raise asyncio.TimeoutError(f"{operation} timed out after {timeout}s") from None