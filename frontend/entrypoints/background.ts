import { browser, type Browser } from "wxt/browser";
import type { components } from "../src/api/schema";
import {
  BACKEND_STORAGE_KEY,
  DEFAULT_BACKEND_URL,
  LAST_TASK_STORAGE_KEY,
  CAPTURE_CLIENT_STORAGE_KEY,
  validateBackendUrl,
} from "../src/extension/config";
import { isSupportedMarketplaceUrl, isSupportedProductUrl } from "../src/extension/page-rules";

type TaskSummary = components["schemas"]["TaskSummary"];
type TaskRead = components["schemas"]["TaskRead"];
type ItemInput = components["schemas"]["ItemInput"];
type ItemRead = components["schemas"]["ItemRead"];
type CaptureSnapshotSubmit = components["schemas"]["CaptureSnapshotSubmit"];
type CaptureSnapshotRead = components["schemas"]["CaptureSnapshotRead"];
type BrowserClaim = components["schemas"]["BrowserClaimResponse"];
type BrowserCaptureResult = components["schemas"]["BrowserCaptureResult"];

type ExtensionRequest =
  | { type: "shopping-agent:list-tasks" }
  | { type: "shopping-agent:claim-capture"; url: string }
  | { type: "shopping-agent:complete-capture"; url: string; jobId: string; leaseToken: string; capture: CaptureSnapshotSubmit }
  | { type: "shopping-agent:fail-capture"; url: string; jobId: string; leaseToken: string; errorCode: "page_changed" | "capture_failed"; detail?: string }
  | {
      type: "shopping-agent:add-current-page";
      url: string;
      title: string;
      taskId: string | null;
      taskTitle: string;
      idempotencyKey: string;
      captureIdempotencyKey: string;
      capture: CaptureSnapshotSubmit;
    }
  | { type: "shopping-agent:open-task"; taskId: string };

type ExtensionResponse =
  | { ok: true; tasks: TaskSummary[]; backendUrl: string }
  | { ok: true; taskId: string; taskTitle: string; captureStatus?: string; captureError?: string }
  | { ok: true }
  | { ok: true; job: BrowserClaim["job"] }
  | { ok: true; snapshotId: string | null }
  | { ok: false; error: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseRequest(value: unknown): ExtensionRequest | null {
  if (!isRecord(value) || typeof value.type !== "string") return null;
  if (value.type === "shopping-agent:list-tasks") return { type: value.type };
  if (value.type === "shopping-agent:claim-capture" && typeof value.url === "string") {
    return { type: value.type, url: value.url };
  }
  if ((value.type === "shopping-agent:complete-capture" || value.type === "shopping-agent:fail-capture") &&
      typeof value.url === "string" && typeof value.jobId === "string" && typeof value.leaseToken === "string") {
    if (!/^[0-9a-f-]{36}$/i.test(value.jobId) || !/^[A-Za-z0-9_-]{32,128}$/.test(value.leaseToken)) return null;
    const shared = { url: value.url, jobId: value.jobId, leaseToken: value.leaseToken };
    if (value.type === "shopping-agent:complete-capture" && isRecord(value.capture)) {
      return { type: value.type, ...shared, capture: value.capture as unknown as CaptureSnapshotSubmit };
    }
    if (value.type === "shopping-agent:fail-capture" && ["page_changed", "capture_failed"].includes(String(value.errorCode))) {
      // The detail is a short diagnostic string, not page content; keep it bounded.
      const detail = typeof value.detail === "string" ? value.detail.slice(0, 200) : undefined;
      return { type: value.type, ...shared, errorCode: value.errorCode as "page_changed" | "capture_failed", detail };
    }
    return null;
  }
  if (value.type === "shopping-agent:add-current-page") {
    if (
      typeof value.url !== "string" ||
      typeof value.title !== "string" ||
      !(typeof value.taskId === "string" || value.taskId === null) ||
      typeof value.taskTitle !== "string" ||
      typeof value.idempotencyKey !== "string" ||
      typeof value.captureIdempotencyKey !== "string" ||
      !isRecord(value.capture)
    ) {
      return null;
    }
    return {
      type: value.type,
      url: value.url,
      title: value.title,
      taskId: value.taskId,
      taskTitle: value.taskTitle,
      idempotencyKey: value.idempotencyKey,
      captureIdempotencyKey: value.captureIdempotencyKey,
      capture: value.capture as unknown as CaptureSnapshotSubmit,
    };
  }
  if (value.type === "shopping-agent:open-task" && typeof value.taskId === "string") {
    return { type: value.type, taskId: value.taskId };
  }
  return null;
}

function senderMarketplaceUrl(sender: Browser.runtime.MessageSender): URL {
  const value = sender.url ?? sender.tab?.url;
  if (!value) throw new Error("没有识别到当前商品页面，请刷新后重试。");
  const page = new URL(value);
  if (!isSupportedMarketplaceUrl(page.href)) {
    throw new Error("悬浮助手只在淘宝、天猫和京东页面可用。");
  }
  return page;
}

function samePage(left: URL, right: URL): boolean {
  return left.origin === right.origin && left.pathname === right.pathname && left.search === right.search;
}

function productKey(value: string): string | null {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    const path = url.pathname.toLowerCase();
    if ((host === "item.taobao.com" || host.endsWith(".taobao.com")) && path.endsWith("/item.htm")) {
      const id = url.searchParams.get("id") || url.searchParams.get("itemId");
      return id ? `taobao:${id}` : null;
    }
    if ((host === "detail.tmall.com" || host.endsWith(".tmall.com")) && path.endsWith("/item.htm")) {
      const id = url.searchParams.get("id") || url.searchParams.get("itemId");
      return id ? `tmall:${id}` : null;
    }
    if (host === "item.jd.com") {
      const id = path.match(/^\/(\d+)\.html$/)?.[1];
      return id ? `jd:${id}` : null;
    }
    if (host === "item.m.jd.com") {
      const id = path.match(/^\/product\/(\d+)\.html$/)?.[1] || url.searchParams.get("wareId");
      return id ? `jd:${id}` : null;
    }
  } catch {
    return null;
  }
  return null;
}

async function getBackendUrl(): Promise<string> {
  const stored = await browser.storage.local.get(BACKEND_STORAGE_KEY);
  const value = stored[BACKEND_STORAGE_KEY];
  return validateBackendUrl(typeof value === "string" ? value : DEFAULT_BACKEND_URL);
}

let clientIdPromise: Promise<string> | null = null;
function getCaptureClientId(): Promise<string> {
  clientIdPromise ??= (async () => {
    const stored = await browser.storage.local.get(CAPTURE_CLIENT_STORAGE_KEY);
    const value = stored[CAPTURE_CLIENT_STORAGE_KEY];
    if (typeof value === "string" && /^[0-9a-f-]{36}$/i.test(value)) return value;
    const id = crypto.randomUUID();
    await browser.storage.local.set({ [CAPTURE_CLIENT_STORAGE_KEY]: id });
    return id;
  })();
  return clientIdPromise;
}

function pageIdentityUrl(page: URL): string {
  const safe = new URL(page.href);
  const id = safe.searchParams.get("id") || safe.searchParams.get("itemId");
  const wareId = safe.searchParams.get("wareId");
  safe.search = "";
  safe.hash = "";
  if (id) safe.searchParams.set("id", id);
  else if (wareId) safe.searchParams.set("wareId", wareId);
  return safe.href;
}

async function requestJson<T>(
  backendUrl: string,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${backendUrl}/api/v1${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });
  } catch {
    throw new Error("无法连接本机服务。请检查后台是否运行，并在扩展设置中确认服务地址。");
  }

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = isRecord(body) && typeof body.detail === "string" ? body.detail : "请求未能完成，请稍后重试。";
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function handleRequest(request: ExtensionRequest, sender: Browser.runtime.MessageSender): Promise<ExtensionResponse> {
  try {
    const page = senderMarketplaceUrl(sender);
    const backendUrl = await getBackendUrl();

    if (request.type === "shopping-agent:list-tasks") {
      const tasks = await requestJson<TaskSummary[]>(backendUrl, "/tasks?limit=100");
      return { ok: true, tasks, backendUrl };
    }

    if (request.type === "shopping-agent:open-task") {
      if (!/^[0-9a-f-]{36}$/i.test(request.taskId)) throw new Error("任务编号无效，请重新选择任务。");
      await browser.tabs.create({ url: `${backendUrl}/tasks/${encodeURIComponent(request.taskId)}` });
      return { ok: true };
    }

    if (["shopping-agent:claim-capture", "shopping-agent:complete-capture", "shopping-agent:fail-capture"].includes(request.type)) {
      if (!sender.tab || sender.frameId !== 0 || !isSupportedProductUrl(page.href)) {
        throw new Error("浏览器补采集只接受商品页顶层内容脚本的请求。");
      }
      const browserRequest = request as Extract<ExtensionRequest, { type: "shopping-agent:claim-capture" | "shopping-agent:complete-capture" | "shopping-agent:fail-capture" }>;
      if (browserRequest.type !== "shopping-agent:fail-capture" && !samePage(page, new URL(browserRequest.url))) {
        throw new Error("商品页已变化，无法执行此次补采集。");
      }
      const clientId = await getCaptureClientId();
      if (browserRequest.type === "shopping-agent:claim-capture") {
        const claim = await requestJson<BrowserClaim>(backendUrl, "/browser-captures/claim", {
          method: "POST", body: JSON.stringify({ page_url: pageIdentityUrl(page), client_id: clientId,
            browser: navigator.userAgent.includes("Edg/") ? "edge" : "chrome",
            extension_version: browser.runtime.getManifest().version }),
        });
        if (claim.job && productKey(claim.job.page_url) !== productKey(page.href)) {
          throw new Error("采集请求与当前商品不匹配。");
        }
        return { ok: true, job: claim.job };
      }
      const suffix = browserRequest.type === "shopping-agent:complete-capture" ? "complete" : "fail";
      const body = browserRequest.type === "shopping-agent:complete-capture"
        ? { client_id: clientId, capture: browserRequest.capture }
        : { client_id: clientId, error_code: browserRequest.errorCode };
      const result = await requestJson<BrowserCaptureResult>(backendUrl,
        `/browser-captures/${encodeURIComponent(browserRequest.jobId)}/${suffix}`, {
          method: "POST", headers: { "X-Capture-Lease": browserRequest.leaseToken }, body: JSON.stringify(body),
        });
      return { ok: true, snapshotId: result.snapshot_id ?? null };
    }

    if (request.type !== "shopping-agent:add-current-page") throw new Error("请求类型无效。");
    const currentPage = new URL(request.url);
    if (currentPage.username || currentPage.password || !samePage(page, currentPage)) {
      throw new Error("当前页面已变化，请刷新悬浮助手后再添加。");
    }
    if (!isSupportedProductUrl(currentPage.href)) {
      throw new Error("请先打开淘宝、天猫或京东的商品详情页。");
    }
    if (!/^[A-Za-z0-9._:-]{8,128}$/.test(request.idempotencyKey)) {
      throw new Error("添加请求编号无效，请重试。");
    }

    const item: ItemInput = {
      url: currentPage.href,
      page_title: request.title.trim().slice(0, 512) || null,
      source: "extension",
      browser: navigator.userAgent.includes("Edg/") ? "edge" : "chrome",
    };
    let taskId = request.taskId;
    let taskTitle = request.taskTitle.trim();
    let itemId: string | null = null;
    if (taskId) {
      if (!/^[0-9a-f-]{36}$/i.test(taskId)) throw new Error("任务编号无效，请重新选择任务。");
      const task = await requestJson<TaskRead>(backendUrl, `/tasks/${encodeURIComponent(taskId)}`);
      taskTitle = task.title;
      const pageKey = productKey(currentPage.href);
      const existingItem = task.items.find((candidate) => productKey(candidate.url) === pageKey && pageKey !== null);
      if (existingItem) {
        itemId = existingItem.id;
      } else {
        const added = await requestJson<ItemRead>(backendUrl, `/tasks/${encodeURIComponent(taskId)}/items`, {
          method: "POST",
          headers: { "Idempotency-Key": request.idempotencyKey },
          body: JSON.stringify(item),
        });
        itemId = added.id;
      }
    } else {
      const task = await requestJson<TaskRead>(backendUrl, "/tasks", {
        method: "POST",
        headers: { "Idempotency-Key": request.idempotencyKey },
        body: JSON.stringify({ title: null, items: [item] }),
      });
      taskId = task.id;
      taskTitle = task.title;
      itemId = task.items[0]?.id ?? null;
    }
    if (!taskId || !itemId) throw new Error("商品链接已保存，但没有收到商品编号。请在工作台检查当前任务。");
    await browser.storage.local.set({ [LAST_TASK_STORAGE_KEY]: taskId });
    try {
      const capture = await requestJson<CaptureSnapshotRead>(
        backendUrl,
        `/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(itemId)}/captures`,
        {
          method: "POST",
          headers: { "Idempotency-Key": request.captureIdempotencyKey },
          body: JSON.stringify(request.capture),
        },
      );
      return { ok: true, taskId, taskTitle: taskTitle || "商品比较", captureStatus: capture.status };
    } catch (cause) {
      return {
        ok: true,
        taskId,
        taskTitle: taskTitle || "商品比较",
        captureError: cause instanceof Error ? cause.message : "快照保存失败。",
      };
    }
  } catch (cause) {
    return { ok: false, error: cause instanceof Error ? cause.message : "操作失败，请稍后重试。" };
  }
}

export default defineBackground(() => {
  browser.runtime.onMessage.addListener((message, sender, sendResponse) => {
    const request = parseRequest(message);
    if (!request) return;
    void handleRequest(request, sender).then(sendResponse, () => {
      sendResponse({ ok: false, error: "悬浮助手暂时无法处理请求，请刷新页面后重试。" });
    });
    return true;
  });
});
