"""Expand a collapsed specification panel before the page is parsed.

Marketplace item pages often hide the parameter table behind a tab that renders on click.
Without that click the table is absent from the DOM, so the parser faithfully reports
"specifications missing" and the agent has nothing structured to compare. Expanding the
tab is a read-only page interaction: it reveals content already on the page.

Only elements that are tabs are clicked. A control that navigates to another page is left
alone, because moving off the product would invalidate the snapshot.
"""

from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError

# Tab labels that reveal a parameter table. Matched exactly on short element text so that
# ordinary prose mentioning the words is not clicked.
SPEC_TAB_LABELS = ("规格参数", "商品参数", "产品参数", "参数规格", "规格与包装")

# Survey the page for candidate tabs, reporting enough to decide whether clicking is safe.
_FIND_TABS = """
(labels) => {
  const found = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  let node;
  while ((node = walker.nextNode())) {
    const text = (node.textContent || '').trim();
    if (!labels.includes(text)) continue;
    const rect = node.getBoundingClientRect();
    const href = node.getAttribute('href') || '';
    const anchors = node.tagName === 'A' || Boolean(href);
    found.push({
      text,
      visible: rect.width > 0 && rect.height > 0,
      navigates: anchors,
    });
    if (found.length >= 20) break;
  }
  return found;
}
"""


def find_spec_tab(page, labels=SPEC_TAB_LABELS):
    """Return the first visible tab-like element whose text matches a spec label."""
    try:
        candidates = page.evaluate(_FIND_TABS, list(labels))
    except PlaywrightError:
        return None
    for candidate in candidates:
        if candidate.get("visible") and not candidate.get("navigates"):
            return candidate.get("text")
    return None


def expand_spec_panel(page, *, wait_ms: int = 1500) -> str | None:
    """Click the specification tab when present; return the label that was expanded.

    Returns ``None`` when there is nothing to expand or the click did not take effect, so a
    caller can record the absence honestly instead of assuming parameters were read.
    """
    label = find_spec_tab(page)
    if label is None:
        return None
    url_before = page.url
    try:
        target = page.get_by_text(label, exact=True).first
        target.click(timeout=4000)
        page.wait_for_timeout(wait_ms)
    except PlaywrightError:
        return None
    # A click that navigated away would make any later read describe a different page.
    if page.url != url_before:
        return None
    return label
