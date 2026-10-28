// Trusted DOM observation only. The model never supplies selectors or JavaScript.
export interface VariantOption {
  id: string; label: string; selected: boolean; disabled: boolean;
  selectable: boolean; evidence_ids: string[];
}
export interface VariantGroup { id: string; label: string; options: VariantOption[] }
export interface VariantCatalog {
  status: "ready" | "missing" | "ambiguous"; groups: VariantGroup[]; truncated: boolean;
}
const MARKER = "data-shopping-agent-variant";
const GROUPS = "[class*='skuItem--'], #J_isku dl, .tb-sku dl, #choose-attrs > .li, #choose [id^='choose-'], [data-sku-group]";
const OPTIONS = "[class*='valueItem--'], dd li[data-value], .dd .item, [role='radio'], [data-sku-option]";
const LABEL = "[class*='labelWrapTitle'], [class*='ItemLabel'], dt, .dt, [data-sku-label]";
const text = (value: string | null | undefined, max = 512) => (value || "").replace(/\s+/g, " ").trim().slice(0, max);
function visible(el: Element): boolean {
  const style = getComputedStyle(el);
  return style.display !== "none" && style.visibility !== "hidden" && el.getClientRects().length > 0;
}
function id(prefix: string, value: string): string {
  let hash = 2166136261;
  for (const char of value) { hash ^= char.codePointAt(0)!; hash = Math.imul(hash, 16777619); }
  return `${prefix}-${(hash >>> 0).toString(16)}`;
}
function selected(el: Element): boolean {
  return ["aria-checked", "aria-selected", "aria-pressed", "data-selected"].some(a => el.getAttribute(a) === "true") ||
    /(?:^|\s)(?:(?:is|tb-)?selected|curr|checked)(?:--|\s|$)/i.test(el.className);
}
function disabled(el: Element): boolean {
  return el.hasAttribute("disabled") || ["aria-disabled", "data-disabled"].some(a => /^(true|1)$/.test(el.getAttribute(a) || "")) ||
    /(?:^|\s)(?:isDisabled|disabled|out-of-stock|no-stock)(?:--|\s|$)/i.test(el.className);
}
function safeOption(el: Element, label: string): boolean {
  if (!label || label.length > 512 || /购买|下单|购物车|支付|领取|客服|收藏|登录|注册|数量|件数/.test(label)) return false;
  if (el.matches("button") && el.closest("form") && el.getAttribute("type") !== "button") return false;
  if (!/^(DIV|LI|BUTTON|LABEL|SPAN|A)$/.test(el.tagName)) return false;
  const link = el.matches("a") ? el : el.querySelector("a");
  const href = link?.getAttribute("href")?.trim();
  // Navigation to another product/SKU is not an in-place selection and requires separate proof.
  return !href || /^(#|javascript:;?|javascript:void\(0\);?)$/i.test(href);
}

export function inspectVariants(): VariantCatalog {
  document.querySelectorAll(`[${MARKER}]`).forEach(el => el.removeAttribute(MARKER));
  const groups: VariantGroup[] = [];
  let truncated = false, ambiguous = false, total = 0;
  const rows = Array.from(document.querySelectorAll(GROUPS)).filter(visible)
    .filter(row => !row.parentElement?.closest(GROUPS));
  for (const row of rows.slice(0, 12)) {
    const labelNode = row.querySelector(LABEL);
    const label = text(row.getAttribute("data-sku-group") || labelNode?.textContent, 128);
    if (!label) continue;
    const groupId = id("g", label);
    const options: VariantOption[] = [];
    const nodes = Array.from(row.querySelectorAll(OPTIONS)).filter(visible)
      .filter(node => !node.parentElement?.closest(OPTIONS));
    for (const el of nodes.slice(0, Math.min(40, 160 - total))) {
      const labelEl = el.querySelector("[class*='valueItemText'], [data-sku-text]");
      const value = text(labelEl?.getAttribute("title") || labelEl?.textContent || el.getAttribute("title") || el.getAttribute("aria-label") || el.textContent);
      if (!value) continue;
      const optionId = id("o", `${label}\n${value}\n${el.getAttribute("data-vid") || el.getAttribute("data-value") || ""}`);
      if (options.some(option => option.id === optionId)) ambiguous = true;
      el.setAttribute(MARKER, `${groupId}:${optionId}`);
      options.push({ id: optionId, label: value, selected: selected(el), disabled: disabled(el),
        selectable: safeOption(el, value), evidence_ids: [] });
      total += 1;
    }
    if (nodes.length > options.length) truncated = true;
    if (!options.length) continue;
    if (groups.some(group => group.id === groupId) || options.filter(option => option.selected).length > 1) ambiguous = true;
    groups.push({ id: groupId, label, options });
  }
  if (rows.length > 12 || total >= 160) truncated = true;
  return { status: ambiguous ? "ambiguous" : groups.length ? "ready" : "missing", groups, truncated };
}

export function validateVariantTarget(groupId: string, optionId: string): boolean {
  const catalog = inspectVariants();
  if (catalog.status !== "ready") return false;
  const group = catalog.groups.find(group => group.id === groupId);
  const option = group?.options.find(option => option.id === optionId);
  return Boolean(option && !option.disabled && option.selectable);
}
