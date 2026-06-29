"""
Request timeout utilities.

Provides:
- TimeoutError          – raised on any timeout
- run_with_timeout()    – wraps a callable in a thread with a hard deadline
- async_with_timeout()  – wraps a coroutine with asyncio.wait_for
- TIMEOUTS              – single place to tune all deadlines
"""

import asyncio
import concurrent.futures
import logging
from typing import Any, Callable, TypeVar

log = logging.getLogger("assistant.timeout")

T = TypeVar("T")


# ─────────────────────────────────────────────
# Central timeout registry
# ─────────────────────────────────────────────

class TIMEOUTS:
    """All timeout values in seconds.  Edit here; nowhere else."""

    # Ollama inference (local GPU/CPU – can be slow on first token)
    LLM_INFERENCE: int = 120

    # Gemini / remote API calls
    REMOTE_LLM: int = 30

    # HTTP GET for web scraping
    HTTP_SCRAPE: int = 15

    # Yahoo search request
    HTTP_SEARCH: int = 12

    # Ollama model unload request
    OLLAMA_UNLOAD: int = 5

    # MCP tool execution (Playwright can be slow)
    MCP_TOOL: int = 60

    # Full orchestrator.invoke() call – safety net
    ORCHESTRATOR: int = 180

    # PowerShell / subprocess commands
    SHELL_COMMAND: int = 15
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
    """Run *fn* in a thread pool; raise TimeoutError if it takes too long.

    Args:
        fn:        The callable to run.
        *args:     Positional arguments forwarded to *fn*.
        timeout:   Seconds to wait before giving up.
        operation: Human-readable label for log messages.
        **kwargs:  Keyword arguments forwarded to *fn*.

    Raises:
        TimeoutError: If *fn* does not complete within *timeout* seconds.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            log.error(
                "timeout",
                operation=operation,
                timeout_s=timeout,
            )
            raise TimeoutError(
                f"{operation} timed out after {timeout}s"
            ) from None


# ─────────────────────────────────────────────
# Async wrapper
# ─────────────────────────────────────────────

async def async_with_timeout(
    coro,
    timeout: float,
    operation: str = "async_operation",
):
    """Wrap a coroutine with asyncio.wait_for and log on timeout.

    Args:
        coro:      Awaitable to run.
        timeout:   Seconds to wait.
        operation: Label for log messages.

    Raises:
        asyncio.TimeoutError: If *coro* does not complete in time.
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        log.error(
            "async_timeout",
            operation=operation,
            timeout_s=timeout,
        )
        raise asyncio.TimeoutError(
            f"{operation} timed out after {timeout}s"
        ) from None
