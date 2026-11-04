/** Reveal a collapsed parameter table before the page snapshot is taken.
 *
 * Marketplace item pages can keep the specification table behind a tab that only renders
 * on click. Reading the DOM without that click records "specifications missing" even
 * though the data is one interaction away, so the table is expanded first.
 *
 * Only tab-shaped elements are clicked. A control that navigates elsewhere is skipped,
 * because the snapshot must keep describing the same product page.
 */

const SPEC_TAB_LABELS = ["规格参数", "商品参数", "产品参数", "参数规格", "规格与包装"];

const DEFAULT_WAIT_MS = 1200;

function isTabLike(element: Element): boolean {
  if (!["BUTTON", "DIV", "SPAN", "LI"].includes(element.tagName)) return false;
  if (element.closest("a, [href], form")) return false;
  if (element.hasAttribute("disabled") || element.getAttribute("aria-disabled") === "true") return false;
  const style = getComputedStyle(element);
  if (style.display === "none" || style.visibility === "hidden") return false;
  return element.getBoundingClientRect().width > 0;
}

function findSpecTab(document: Document): HTMLElement | null {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  const candidates: HTMLElement[] = [];
  let node: Node | null = walker.nextNode();
  while (node) {
    if (node instanceof HTMLElement && SPEC_TAB_LABELS.includes((node.textContent || "").trim())) {
      if (isTabLike(node)) candidates.push(node);
    }
    node = walker.nextNode();
  }
  return candidates[0] ?? null;
}

/** Click the specification tab when present; return its label, or null when none applies. */
export async function expandSpecPanel(
  document: Document,
  waitMs = DEFAULT_WAIT_MS,
): Promise<string | null> {
  const tab = findSpecTab(document);
  if (!tab) return null;
  const label = (tab.textContent || "").trim();
  const before = document.body.innerText.length;
  try {
    tab.click();
  } catch {
    return null;
  }
  await new Promise((resolve) => setTimeout(resolve, waitMs));
  // Report the label only when the click actually produced more content to read.
  return document.body.innerText.length > before ? label : null;
}
