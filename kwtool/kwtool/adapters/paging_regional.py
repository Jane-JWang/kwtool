"""Native pagination controls and result fingerprints for provincial sites."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Pager:
    results: str
    controls: str
    numbered_only: bool = False


def result_signature(scope, pager: Pager) -> tuple[str, ...]:
    """Compare article content, excluding page numbers, clocks and query echoes."""
    # Read the list in one browser operation. AJAX may replace it between
    # successive locator.nth() reads, otherwise leaving a stale index waiting.
    return tuple(scope.locator(pager.results).evaluate_all("""elements => elements
        .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')
        .map(e => (e.innerText || '').trim()).filter(Boolean)"""))


def next_control(scope, pager: Pager, number: int):
    """Use the site's real numbered/next control, including JS-only spans."""
    choices = []
    for container in scope.locator(pager.controls).all():
        if not container.is_visible():
            continue
        for control in container.locator("a, button, span, input[type=button], input[type=submit]").all():
            if not control.is_visible() or not control.is_enabled():
                continue
            if control.evaluate("e => !!e.closest('[disabled], [aria-disabled=true], .disabled, .disable, .prev-disabled, .next-disabled')"):
                continue
            label = (control.inner_text().strip() or control.get_attribute("value") or "").strip()
            title = control.get_attribute("title") or control.get_attribute("aria-label") or ""
            if label == str(number):
                choices.append((2, control))
            elif not pager.numbered_only and (re.fullmatch(r"[\[【]?\s*(?:下\s*一?\s*页|next|»|›|>)\s*[\]】]?", label, re.I) or "下一页" in title):
                choices.append((1, control))
    return max(choices, key=lambda item: item[0])[1] if choices else None
