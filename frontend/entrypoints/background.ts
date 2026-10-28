import { browser, type Browser } from "wxt/browser";
import type { components } from "../src/api/schema";
import {
  BACKEND_STORAGE_KEY,
  DEFAULT_BACKEND_URL,
  LAST_TASK_STORAGE_KEY,
  validateBackendUrl,
} from "../src/extension/config";
import { isSupportedMarketplaceUrl, isSupportedProductUrl } from "../src/extension/page-rules";

type TaskSummary = components["schemas"]["TaskSummary"];
type TaskRead = components["schemas"]["TaskRead"];
type ItemInput = components["schemas"]["ItemInput"];

type ExtensionRequest =
  | { type: "shopping-agent:list-tasks" }
  | {
      type: "shopping-agent:add-current-page";
      url: string;
      title: string;
      taskId: string | null;
      taskTitle: string;
      idempotencyKey: string;
    }
  | { type: "shopping-agent:open-task"; taskId: string };

type ExtensionResponse =
  | { ok: true; tasks: TaskSummary[]; backendUrl: string }
  | { ok: true; taskId: string; taskTitle: string }
  | { ok: true }
  | { ok: false; error: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseRequest(value: unknown): ExtensionRequest | null {
  if (!isRecord(value) || typeof value.type !== "string") return null;
  if (value.type === "shopping-agent:list-tasks") return { type: value.type };
  if (value.type === "shopping-agent:add-current-page") {
    if (
      typeof value.url !== "string" ||
      typeof value.title !== "string" ||
      !(typeof value.taskId === "string" || value.taskId === null) ||
      typeof value.taskTitle !== "string" ||
      typeof value.idempotencyKey !== "string"
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

async function getBackendUrl(): Promise<string> {
  const stored = await browser.storage.local.get(BACKEND_STORAGE_KEY);
  const value = stored[BACKEND_STORAGE_KEY];
  return validateBackendUrl(typeof value === "string" ? value : DEFAULT_BACKEND_URL);
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
    if (taskId) {
      if (!/^[0-9a-f-]{36}$/i.test(taskId)) throw new Error("任务编号无效，请重新选择任务。");
      await requestJson<void>(backendUrl, `/tasks/${encodeURIComponent(taskId)}/items`, {
        method: "POST",
        headers: { "Idempotency-Key": request.idempotencyKey },
        body: JSON.stringify(item),
      });
    } else {
      const task = await requestJson<TaskRead>(backendUrl, "/tasks", {
        method: "POST",
        headers: { "Idempotency-Key": request.idempotencyKey },
        body: JSON.stringify({ title: null, items: [item] }),
      });
      taskId = task.id;
      taskTitle = task.title;
    }
    if (!taskId) throw new Error("任务保存成功，但没有收到任务编号。请在工作台检查最近任务。");
    await browser.storage.local.set({ [LAST_TASK_STORAGE_KEY]: taskId });
    return { ok: true, taskId, taskTitle: taskTitle || "商品比较" };
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
