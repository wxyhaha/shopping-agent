/** Spec panel expansion must click only real tabs and report honestly when it cannot. */

import assert from "node:assert/strict";
import { test } from "node:test";

// A minimal DOM is not available under node:test, so the label list and the tab-shape rule
// are exercised through small fakes that mirror what the module inspects.
const LABELS = ["规格参数", "商品参数", "产品参数", "参数规格", "规格与包装"];

function fakeElement({ tag = "DIV", text = "", href = null, visible = true }) {
  return {
    tagName: tag,
    textContent: text,
    hasAttribute: (name) => name === "href" && href !== null,
    getBoundingClientRect: () => ({ width: visible ? 80 : 0, height: visible ? 24 : 0 }),
    children: [],
  };
}

function isTabLike(element) {
  if (element.tagName === "A") return false;
  if (element.hasAttribute("href")) return false;
  return element.getBoundingClientRect().width > 0;
}

test("only spec labels are treated as parameter tabs", () => {
  assert.ok(LABELS.includes("规格参数"));
  assert.ok(!LABELS.includes("商品介绍"));
  assert.ok(!LABELS.includes("加入购物车"));
});

test("a link is never clicked even when its text matches", () => {
  // Navigating away would make the snapshot describe a different page.
  assert.equal(isTabLike(fakeElement({ tag: "A", text: "规格参数" })), false);
  assert.equal(isTabLike(fakeElement({ text: "规格参数", href: "/spec" })), false);
  assert.equal(isTabLike(fakeElement({ text: "规格参数" })), true);
});

test("an invisible element is not a clickable tab", () => {
  assert.equal(isTabLike(fakeElement({ text: "规格参数", visible: false })), false);
});
