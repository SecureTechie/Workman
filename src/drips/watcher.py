import json
import logging
import re
from pathlib import Path

from playwright.async_api import async_playwright, Page, BrowserContext

import config
from .models import DripsIssue

logger = logging.getLogger(__name__)


class DripsWatcher:
    def __init__(self):
        self.cookies_file = config.DRIPS_COOKIES_FILE

    async def get_assigned_issues(self) -> list[DripsIssue]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            )
            await self._load_cookies(context)

            page = await context.new_page()
            try:
                issues = await self._scrape_assigned_issues(page)
                logger.info(f"Found {len(issues)} assigned issues on Drips")
                return issues
            finally:
                await browser.close()

    async def _load_cookies(self, context: BrowserContext) -> None:
        path = Path(self.cookies_file)
        if not path.exists():
            raise FileNotFoundError(
                f"Cookies file '{self.cookies_file}' not found.\n"
                "Export your drips.network cookies as JSON using the 'Cookie-Editor' "
                "browser extension after logging in, then save them as cookies.json."
            )

        with open(path) as f:
            raw = json.load(f)

        playwright_cookies = []
        for c in raw:
            cookie: dict = {
                "name": c["name"],
                "value": c["value"],
                "domain": c.get("domain", ".drips.network"),
                "path": c.get("path", "/"),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", True),
                "sameSite": c.get("sameSite", "Lax"),
            }
            if "expirationDate" in c:
                cookie["expires"] = int(c["expirationDate"])
            playwright_cookies.append(cookie)

        await context.add_cookies(playwright_cookies)
        logger.debug(f"Loaded {len(playwright_cookies)} cookies")

    async def _scrape_assigned_issues(self, page: Page) -> list[DripsIssue]:
        logger.info(f"Navigating to {config.DRIPS_ISSUES_URL}")
        await page.goto(config.DRIPS_ISSUES_URL, wait_until="networkidle", timeout=30000)

        if any(kw in page.url for kw in ["sign-in", "login", "auth"]):
            raise RuntimeError(
                "Drips redirected to login — your cookies may have expired. "
                "Re-export cookies.json from your browser."
            )

        # Wait for the page to populate
        try:
            await page.wait_for_selector("main", timeout=15000)
        except Exception:
            logger.warning("Timed out waiting for main content — proceeding anyway")

        content = await page.content()
        logger.debug(f"Page URL after load: {page.url}")

        issues = await self._extract_issues_from_page(page, content)
        return issues

    async def _extract_issues_from_page(self, page: Page, content: str) -> list[DripsIssue]:
        issues = []
        seen_ids: set[str] = set()

        # Primary strategy: find all GitHub issue links in the page
        github_issue_pattern = re.compile(
            r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/(\d+)'
        )

        # Try to get structured elements first
        elements = await page.query_selector_all(
            "article, [class*='issue'], [data-testid*='issue'], li[class*='issue']"
        )

        if elements:
            for el in elements:
                inner = await el.inner_html()
                inner_text = await el.inner_text()
                issue = self._parse_element_html(inner, inner_text, github_issue_pattern)
                if issue and issue.id not in seen_ids:
                    seen_ids.add(issue.id)
                    issues.append(issue)
        else:
            # Fallback: scan raw page HTML for GitHub issue URLs near "assigned" markers
            issues = self._parse_raw_html(content, github_issue_pattern, seen_ids)

        return issues

    def _parse_element_html(
        self,
        html: str,
        text: str,
        pattern: re.Pattern,
    ) -> DripsIssue | None:
        match = pattern.search(html)
        if not match:
            return None

        # Only include issues that show an "assigned" state
        text_lower = text.lower()
        if not any(w in text_lower for w in ["assigned", "in progress", "claimed", "working"]):
            return None

        owner, repo, num = match.group(1), match.group(2), int(match.group(3))

        # Try to extract title from the text (first meaningful line)
        title_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        title = title_lines[0] if title_lines else f"Issue #{num}"

        return DripsIssue(
            id=f"{owner}/{repo}#{num}",
            title=title,
            description="",
            github_issue_url=match.group(0),
            github_repo_url=f"https://github.com/{owner}/{repo}",
            repo_owner=owner,
            repo_name=repo,
            issue_number=num,
        )

    def _parse_raw_html(
        self,
        html: str,
        pattern: re.Pattern,
        seen_ids: set[str],
    ) -> list[DripsIssue]:
        issues: list[DripsIssue] = []

        # Find all GitHub issue URLs and look for "assigned" context nearby
        for match in pattern.finditer(html):
            start = max(0, match.start() - 500)
            end = min(len(html), match.end() + 500)
            context_block = html[start:end].lower()

            if not any(w in context_block for w in ["assigned", "in progress", "claimed"]):
                continue

            owner, repo, num = match.group(1), match.group(2), int(match.group(3))
            issue_id = f"{owner}/{repo}#{num}"

            if issue_id in seen_ids:
                continue
            seen_ids.add(issue_id)

            issues.append(
                DripsIssue(
                    id=issue_id,
                    title=f"Issue #{num} in {owner}/{repo}",
                    description="",
                    github_issue_url=match.group(0),
                    github_repo_url=f"https://github.com/{owner}/{repo}",
                    repo_owner=owner,
                    repo_name=repo,
                    issue_number=num,
                )
            )

        return issues
