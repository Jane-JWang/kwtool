"""Reusable, opt-in browser snapshots for developing site adapters."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import BrowserContext, Page

from kwtool.output import safe_filename


LOGGER = logging.getLogger("kwtool")


def browser_pages(context: BrowserContext) -> list[Page]:
    browser = context.browser
    contexts = browser.contexts if browser is not None else [context]
    return [page for browser_context in contexts for page in browser_context.pages]


def safe_url(url: str) -> str:
    """Keep route information while omitting query values and fragments."""
    parsed = urlparse(url)
    if parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return f"{parsed.scheme}:{parsed.path}" if parsed.scheme else parsed.path


def browser_snapshot(context: BrowserContext, *, query_match: tuple[str, str] | None = None) -> str:
    tabs = []
    for page in browser_pages(context):
        if page.is_closed():
            continue
        summary = safe_url(page.url)
        if query_match is not None:
            key, value = query_match
            matched = value in parse_qs(urlparse(page.url).query).get(key, [])
            summary += f" (subject_match={matched})"
        tabs.append(summary)
    return "; ".join(tabs) or "no open tabs"


PAGE_STRUCTURE = """() => {
  const body = document.body;
  const elements = Array.from(document.querySelectorAll('body *')).filter(el => {
    return el.id || el.className && typeof el.className === 'string' ||
      el.getAttribute('role') || el.getAttribute('placeholder');
  }).slice(0, 800).map(el => ({
    tag: el.tagName.toLowerCase(),
    id: el.id || '',
    class: typeof el.className === 'string' ? el.className.slice(0, 160) : '',
    role: el.getAttribute('role') || '',
    placeholder: el.getAttribute('placeholder') || '',
    text: (el.children.length ? '' : el.textContent || '').trim().slice(0, 160)
  }));
  return {title: document.title, text: (body?.innerText || '').slice(0, 12000), elements};
}"""


def capture_page(page: Page, directory: Path, label: str) -> tuple[Path, Path]:
    """Save a screenshot and safe DOM outline; never store URL query values."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(label, limit=120)
    image_path = directory / f"{stem}.png"
    json_path = directory / f"{stem}.json"
    details = page.evaluate(PAGE_STRUCTURE)
    details["url"] = safe_url(page.url)
    image_path.write_bytes(page.screenshot(full_page=True, timeout=15000))
    json_path.write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.info("已保存诊断快照：%s；%s", image_path, json_path)
    return image_path, json_path
