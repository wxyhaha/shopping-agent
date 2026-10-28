import assert from "node:assert/strict";
import { test } from "node:test";
import { pollCaptureBridge } from "../src/extension/capture-bridge.ts";

const url = "https://item.jd.com/100001.html";
const job = { id: "job-1", lease_token: "lease-1", reason: "核对已选规格" };

function host(send) {
  let reads = 0;
  return {
    pageUrl: () => url,
    send,
    capture: (pageUrl) => { reads += 1; return { page_url: pageUrl, captured_at: "synthetic" }; },
    status: () => {},
    reads: () => reads,
  };
}

test("no job does not read the DOM or submit a snapshot", async () => {
  const messages = [];
  const page = host(async (message) => { messages.push(message); return { ok: true, job: null }; });
  assert.equal(await pollCaptureBridge(page), "idle");
  assert.equal(page.reads(), 0);
  assert.deepEqual(messages.map((message) => message.type), ["shopping-agent:claim-capture"]);
});

test("job reads current DOM once and submits matching lease", async () => {
  const messages = [];
  const page = host(async (message) => {
    messages.push(message);
    return message.type.endsWith("claim-capture") ? { ok: true, job } : { ok: true };
  });
  assert.equal(await pollCaptureBridge(page), "captured");
  assert.equal(page.reads(), 1);
  assert.equal(messages[1].jobId, job.id);
  assert.equal(messages[1].leaseToken, job.lease_token);
  assert.equal(messages[1].capture.page_url, url);
});

test("lost completion acknowledgment replays exactly the same captured payload", async () => {
  const completions = [];
  const page = host(async (message) => {
    if (message.type.endsWith("claim-capture")) return { ok: true, job };
    completions.push(message);
    if (completions.length === 1) throw new Error("network response lost");
    return { ok: true };
  });
  assert.equal(await pollCaptureBridge(page), "captured");
  assert.equal(page.reads(), 1);
  assert.strictEqual(completions[0].capture, completions[1].capture);
});

test("navigation before collection reports failure without reading the new page", async () => {
  let current = url;
  const messages = [];
  const page = host(async (message) => {
    messages.push(message);
    if (message.type.endsWith("claim-capture")) { current = `${url}?changed=1`; return { ok: true, job }; }
    return { ok: true };
  });
  page.pageUrl = () => current;
  assert.equal(await pollCaptureBridge(page), "failed");
  assert.equal(page.reads(), 0);
  assert.equal(messages[1].errorCode, "page_changed");
});

test("navigation during collection never submits the stale page snapshot", async () => {
  let current = url;
  const messages = [];
  const page = host(async (message) => {
    messages.push(message);
    return message.type.endsWith("claim-capture") ? { ok: true, job } : { ok: true };
  });
  page.pageUrl = () => current;
  page.capture = () => { current = "https://item.jd.com/200002.html"; return { page_url: url }; };
  assert.equal(await pollCaptureBridge(page), "failed");
  assert.equal(messages[1].type, "shopping-agent:fail-capture");
  assert.equal(messages[1].errorCode, "page_changed");
});

test("adapter exceptions return a capture failure instead of a snapshot", async () => {
  const messages = [];
  const page = host(async (message) => {
    messages.push(message);
    return message.type.endsWith("claim-capture") ? { ok: true, job } : { ok: true };
  });
  page.capture = () => { throw new Error("DOM unavailable"); };
  assert.equal(await pollCaptureBridge(page), "failed");
  assert.equal(messages[1].errorCode, "capture_failed");
});
