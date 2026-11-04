import assert from "node:assert/strict";
import { test } from "node:test";
import { prepareDetails, addPreparationCoverage, captureSummary } from "../src/extension/capture-preparation.ts";

function fixture(inspect) {
  let now = 0;
  let steps = 0;
  let restores = 0;
  const host = {
    now: () => now, check: () => {}, wait: async ms => { now += ms; },
    inspect: () => inspect(now, steps), advance: () => { steps += 1; },
    restore: () => { restores += 1; }, status: () => {},
  };
  return { host, steps: () => steps, restores: () => restores };
}
const detail = (extra = {}) => ({
  found: true, atEnd: true, loaded: 2, pending: 0, positionedImages: 2, failedImages: 0,
  lastImageBottom: 1200, imageCutoffReached: true, signature: "two", ...extra,
});

test("late detail insertion and changing images must settle before returning", async () => {
  const f = fixture(now => now < 600 ? detail({ found: false, loaded: 0, signature: "none" })
    : now < 1200 ? detail({ pending: 1, signature: "loading" }) : detail());
  const result = await prepareDetails(f.host, { scroll: true });
  assert.equal(result.reason, "settled");
  assert.ok(f.host.now() >= 2200);
  assert.equal(f.restores(), 1);
});

test("unloaded images expire as unknown instead of complete", async () => {
  const f = fixture(() => detail({ pending: 1 }));
  const result = await prepareDetails(f.host, { scroll: true, budgetMs: 950 });
  assert.equal(result.reason, "time_limit");
  assert.equal(result.detail.pending, 1);
  assert.equal(f.host.now(), 950);
});

test("the capture reaches the last image and the detail container before settling", async () => {
  const f = fixture((_now, steps) => detail({
    atEnd: steps >= 3,
    lastImageBottom: steps >= 2 ? 1200 : 1800,
    imageCutoffReached: steps >= 2,
    signature: `position-${steps}`,
  }));
  const result = await prepareDetails(f.host, { scroll: true });
  assert.equal(result.reason, "settled");
  assert.equal(f.steps(), 3);
  assert.equal(result.detail.imageCutoffReached, true);
});

test("reaching the detail container without a positioned image cannot confirm the cutoff", async () => {
  const f = fixture(() => detail({
    positionedImages: 0, lastImageBottom: null, imageCutoffReached: false,
  }));
  const result = await prepareDetails(f.host, { scroll: true });
  assert.equal(result.reason, "cutoff_not_found");
  assert.equal(f.steps(), 0);
});

test("scrolling stops at the step cap even if details keep growing", async () => {
  const f = fixture(() => detail({ atEnd: false }));
  assert.equal((await prepareDetails(f.host, { scroll: true, maxSteps: 3 })).reason, "step_limit");
  assert.equal(f.steps(), 3);
});

test("passive bridge reads do not scroll or wait", async () => {
  const f = fixture(() => detail());
  assert.equal((await prepareDetails(f.host, { scroll: false })).reason, "read_only");
  assert.equal(f.steps(), 0);
  assert.equal(f.host.now(), 0);
});

test("cancellation interrupts the loop and still executes restore policy", async () => {
  const f = fixture(() => detail({ atEnd: false }));
  f.host.check = () => { if (f.host.now() >= 300) throw new Error("cancelled"); };
  await assert.rejects(prepareDetails(f.host, { scroll: true }), /cancelled/);
  assert.equal(f.steps(), 1);
  assert.equal(f.restores(), 1);
});

test("settled metadata never claims page completeness or image text recognition", () => {
  const snapshot = { status: "complete", fields: [], coverage: [], images: [], selected_variant: { status: "missing" } };
  addPreparationCoverage(snapshot, { reason: "settled", steps: 2, detail: detail() });
  assert.equal(snapshot.status, "partial");
  assert.equal(snapshot.coverage[0].status, "ambiguous");
  assert.match(snapshot.coverage[0].note, /不保证页面完整/);
  assert.match(captureSummary(snapshot), /待核对：品牌、选中规格、明确价格、参数/);
  assert.match(captureSummary(snapshot), /文字识别(?:另行|单独)处理/);
});
