"""
Browser scraper helper for JS-rendered sites (Indeed, Trakstar, Foundit, etc.).

Lazy-imports Playwright so the package is only required when you actually
call `BrowserClient`. The plain-HTTP path in `HttpClient` doesn't need it.

Usage:
    async with BrowserClient() as browser:
        html = await browser.fetch_html("https://...")
        # or
        cards = await browser.fetch_all_pages(
            "https://indeed.com/jobs?q=doctor",
            wait_selector=".job_seen_beacon",
            max_pages=3,
        )
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional, List
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class PlaywrightNotInstalledError(ImportError):
    """Raised when a scraper needs Playwright but it's not installed."""
    pass


def _ensure_playwright():
    try:
        from playwright.async_api import async_playwright
        return async_playwright
    except ImportError as e:
        raise PlaywrightNotInstalledError(
            "Playwright is not installed. Run:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium\n"
            "Or set SCRAPER_USE_BROWSER=0 to skip browser-based scrapers."
        ) from e


class BrowserClient:
    """Async context manager around Playwright. Headless Chromium with stealth-ish defaults."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw = None
        self._browser = None

    async def __aenter__(self):
        async_playwright_fn = _ensure_playwright()
        self._pw = await async_playwright_fn().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                # Force HTTP/1.1 - some Indian sites (JobHai, Manipal) have broken HTTP/2 setups
                "--disable-http2",
            ],
        )
        return self

    async def __aexit__(self, *exc):
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    async def new_page(self, user_agent: str | None = None):
        """Create a stealth-ish browser context + page."""
        context = await self._browser.new_context(
            user_agent=user_agent or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        # Hide webdriver flag
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await context.new_page()
        return page

    async def fetch_html(
        self,
        url: str,
        *,
        wait_selector: Optional[str] = None,
        wait_ms: int = 6000,
        timeout_ms: int = 30000,
    ) -> str:
        """Navigate, wait for selector, return final HTML. Retries once on HTTP/2 errors.

        For Next.js / React SPAs, use wait_until='networkidle' which waits for
        XHR/fetch to settle (that's when hydrated content actually appears).
        Default wait_ms=6000 is enough buffer for client-side re-renders.
        """
        import asyncio
        last_err = None
        for attempt in range(2):
            page = await self.new_page()
            try:
                try:
                    # 'networkidle' is the most reliable for JS-heavy SPAs
                    await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                except Exception as e:
                    if "ERR_HTTP2" in str(e) and attempt == 0:
                        logger.warning(f"HTTP/2 error on {url}, retrying with fresh context")
                        last_err = e
                        continue
                    raise
                if wait_selector:
                    try:
                        # state="attached" so we don't fail just because the element
                        # is below the fold / off-screen.
                        await page.wait_for_selector(wait_selector, state="attached", timeout=timeout_ms)
                    except Exception as e:
                        logger.warning(f"Selector {wait_selector!r} not found: {e}")
                else:
                    await page.wait_for_timeout(wait_ms)
                return await page.content()
            finally:
                await page.context.close()
        raise last_err if last_err else RuntimeError("fetch_html failed")

    async def fetch_all_pages(
        self,
        url: str,
        *,
        wait_selector: str,
        next_button_selector: Optional[str] = None,
        max_pages: int = 5,
        wait_ms: int = 6000,
    ) -> List[str]:
        """
        Fetch multiple pages by clicking a 'next' button (if provided) or scrolling.
        Returns list of HTML strings, one per page.
        """
        page = await self.new_page()
        pages: List[str] = []
        try:
            await page.goto(url, wait_until="load")
            for i in range(max_pages):
                try:
                    await page.wait_for_selector(wait_selector, state="attached", timeout=15000)
                except Exception as e:
                    logger.warning(f"Page {i+1}: selector not found: {e}")
                await page.wait_for_timeout(wait_ms)
                pages.append(await page.content())
                if not next_button_selector:
                    break
                # Click next if present
                try:
                    btn = page.locator(next_button_selector).first
                    if await btn.count() == 0:
                        break
                    await btn.click()
                    await page.wait_for_load_state("load")
                except Exception:
                    break
            return pages
        finally:
            await page.context.close()
