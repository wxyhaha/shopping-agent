import { browser } from "wxt/browser";
import { createShadowRootUi } from "wxt/utils/content-script-ui/shadow-root";
import type { ContentScriptContext } from "wxt/utils/content-script-context";
import type { components } from "../src/api/schema";
import { collectCurrentProductSnapshot, getCurrentProductTitle } from "../src/extension/capture-adapters";
import { expandSpecPanel } from "../src/extension/spec-panel";
import { pollCaptureBridge } from "../src/extension/capture-bridge";
import { LAST_TASK_STORAGE_KEY } from "../src/extension/config";
import { isSupportedMarketplaceUrl, isSupportedProductUrl } from "../src/extension/page-rules";

type TaskSummary = components["schemas"]["TaskSummary"];
type CaptureSnapshotSubmit = components["schemas"]["CaptureSnapshotSubmit"];
const EXTENSION_VERSION = browser.runtime.getManifest().version;

const OVERLAY_CSS = `
  *, *::before, *::after { box-sizing: border-box; }
  #sa-widget {
    position: fixed;
    right: 20px;
    bottom: 108px;
    z-index: 2147483647;
    width: min(340px, calc(100vw - 28px));
    color: #24322a;
    font: 14px/1.45 Inter, "Noto Sans SC", "Microsoft YaHei", sans-serif;
  }
  .sa-panel {
    overflow: hidden;
    border: 1px solid #dce5dc;
    border-radius: 14px;
    background: rgba(255, 255, 255, .98);
    box-shadow: 0 12px 34px rgba(28, 43, 32, .22);
  }
  .sa-header {
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 13px 15px 10px;
    border-bottom: 1px solid #edf1ed;
  }
  .sa-mark {
    display: grid;
    width: 28px;
    height: 28px;
    flex: 0 0 auto;
    place-items: center;
    border-radius: 8px;
    color: #fff;
    background: #4b6d54;
    font-size: 11px;
    font-weight: 700;
  }
  .sa-header-copy { min-width: 0; flex: 1; }
  .sa-header-copy strong, .sa-header-copy small { display: block; }
  .sa-header-copy strong { font-size: 14px; font-weight: 650; }
  .sa-header-copy small { margin-top: 1px; color: #7a897c; font-size: 11px; }
  .sa-icon-button {
    display: inline-grid;
    width: 30px;
    height: 30px;
    place-items: center;
    border: 0;
    border-radius: 7px;
    color: #637267;
    background: transparent;
    cursor: pointer;
    font: inherit;
    font-size: 18px;
  }
  .sa-icon-button:hover { background: #f1f5f1; }
  .sa-preview { padding: 12px 15px 9px; }
  .sa-preview-label { display: block; color: #89968b; font-size: 11px; }
  .sa-preview-title {
    display: -webkit-box;
    overflow: hidden;
    margin-top: 3px;
    color: #36463a;
    font-size: 13px;
    font-weight: 600;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
  }
  .sa-data-preview { display: flex; align-items: flex-start; gap: 7px; padding: 0 15px 10px; color: #68776c; font-size: 11px; }
  .sa-data-preview-copy { min-width: 0; flex: 1; }
  .sa-data-preview-copy strong, .sa-data-preview-copy span { display: block; }
  .sa-data-preview-copy strong { color: #56675a; font-size: 11px; font-weight: 650; }
  .sa-data-preview-copy span { margin-top: 3px; line-height: 1.45; }
  .sa-preview-refresh { flex: 0 0 auto; padding: 0; border: 0; color: #55745b; background: transparent; cursor: pointer; font: inherit; font-size: 11px; }
  .sa-bridge-connection { padding: 0 15px 10px; color: #68776c; font-size: 11px; overflow-wrap: anywhere; }
  .sa-bridge-connection[data-error="true"] { color: #a36a24; }
  .sa-form { padding: 0 15px 12px; }
  .sa-label { display: block; margin-bottom: 5px; color: #68776c; font-size: 12px; }
  .sa-select {
    display: block;
    width: 100%;
    height: 38px;
    padding: 0 10px;
    border: 1px solid #dbe4db;
    border-radius: 8px;
    color: #344338;
    background: #fff;
    font: inherit;
  }
  .sa-select:focus, .sa-button:focus-visible, .sa-icon-button:focus-visible, .sa-link-button:focus-visible {
    outline: 2px solid #83a489;
    outline-offset: 2px;
  }
  .sa-button {
    display: block;
    width: 100%;
    min-height: 40px;
    margin-top: 9px;
    padding: 7px 12px;
    border: 1px solid #4b6d54;
    border-radius: 8px;
    color: #fff;
    background: #4b6d54;
    cursor: pointer;
    font: inherit;
    font-weight: 650;
  }
  .sa-button:hover:not(:disabled) { background: #3d6045; }
  .sa-button:disabled { border-color: #c8d1c9; color: #879287; background: #e9ede9; cursor: not-allowed; }
  .sa-status { min-height: 21px; margin-top: 7px; color: #6c7c6f; font-size: 12px; }
  .sa-status[data-tone="error"] { color: #b33f35; }
  .sa-status[data-tone="success"] { color: #3e7048; }
  .sa-status[data-tone="warning"] { color: #a36a24; }
  .sa-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 9px 15px;
    border-top: 1px solid #edf1ed;
    color: #879287;
    font-size: 10px;
  }
  .sa-footer span { min-width: 0; }
  .sa-link-button {
    flex: 0 0 auto;
    padding: 2px 0;
    border: 0;
    color: #42694a;
    background: transparent;
    cursor: pointer;
    font: inherit;
    font-size: 11px;
  }
  .sa-collapsed { display: none; }
  #sa-widget.is-collapsed .sa-panel { display: none; }
  #sa-widget.is-collapsed .sa-collapsed { display: inline-flex; }
  .sa-collapsed {
    align-items: center;
    gap: 8px;
    padding: 10px 13px;
    border: 1px solid #dce5dc;
    border-radius: 999px;
    color: #fff;
    background: #4b6d54;
    box-shadow: 0 7px 22px rgba(28, 43, 32, .22);
    cursor: pointer;
    font: inherit;
    font-weight: 650;
  }
  .sa-collapsed:hover { background: #3d6045; }
  .sa-spinner { display: none; }
  #sa-widget.is-busy .sa-spinner { display: inline; }
  @media (max-width: 480px) {
    #sa-widget { right: 12px; bottom: 94px; }
  }
`;

interface ListTasksResponse {
  ok: boolean;
  tasks?: TaskSummary[];
  error?: string;
}

interface AddItemResponse {
  ok: boolean;
  taskId?: string;
  taskTitle?: string;
  captureStatus?: string;
  captureError?: string;
  error?: string;
}

function element<T extends HTMLElement>(root: ParentNode, selector: string): T {
  const result = root.querySelector<T>(selector);
  if (!result) throw new Error(`悬浮助手界面缺少元素：${selector}`);
  return result;
}

function mountOverlay(container: HTMLElement): HTMLElement {
  const widget = document.createElement("div");
  widget.id = "sa-widget";
  widget.innerHTML = `
    <button class="sa-collapsed" type="button" aria-label="展开商品比较助手">
      <span class="sa-mark" aria-hidden="true">SA</span><span>加入比较</span>
    </button>
    <section class="sa-panel" aria-label="Shopping Agent 商品比较助手">
      <header class="sa-header">
        <span class="sa-mark" aria-hidden="true">SA</span>
        <span class="sa-header-copy"><strong>加入商品比较</strong><small>Shopping Agent · v${EXTENSION_VERSION}</small></span>
        <button class="sa-icon-button sa-refresh" type="button" title="刷新任务列表" aria-label="刷新任务列表">↻</button>
        <button class="sa-icon-button sa-collapse" type="button" title="收起助手" aria-label="收起助手">−</button>
      </header>
      <div class="sa-preview">
        <span class="sa-preview-label">当前页面</span>
        <strong class="sa-preview-title"></strong>
      </div>
      <div class="sa-data-preview">
        <div class="sa-data-preview-copy"><strong>采集预览</strong><span class="sa-data-preview-text">进入商品详情页后显示可识别信息。</span></div>
        <button class="sa-preview-refresh" type="button">刷新</button>
      </div>
      <div class="sa-bridge-connection" role="status">自动采集通道：正在连接…</div>
      <div class="sa-form">
        <label class="sa-label" for="sa-task-select">加入到</label>
        <select id="sa-task-select" class="sa-select" aria-label="选择比较任务"></select>
        <button class="sa-button" type="button" disabled><span class="sa-spinner">⋯ </span><span class="sa-button-text">正在连接本机服务…</span></button>
        <div class="sa-status" role="status" aria-live="polite"></div>
      </div>
      <footer class="sa-footer">
        <span>点击加入或收到研究助手请求时，读取此商品页并提交到本机服务</span>
        <button class="sa-link-button sa-open-task" type="button" hidden>查看任务</button>
      </footer>
    </section>
  `;
  container.append(widget);
  return widget;
}

async function mountProductAssistant(
  container: HTMLElement,
  ctx: ContentScriptContext,
): Promise<void> {
  const widget = mountOverlay(container);
  const taskSelect = element<HTMLSelectElement>(widget, ".sa-select");
  const pageTitleElement = element<HTMLElement>(widget, ".sa-preview-title");
  const capturePreviewElement = element<HTMLElement>(widget, ".sa-data-preview-text");
  const bridgeConnection = element<HTMLElement>(widget, ".sa-bridge-connection");
  const addButton = element<HTMLButtonElement>(widget, ".sa-button");
  const addButtonText = element<HTMLSpanElement>(widget, ".sa-button-text");
  const statusElement = element<HTMLDivElement>(widget, ".sa-status");
  const openTaskButton = element<HTMLButtonElement>(widget, ".sa-open-task");
  const collapsedButton = element<HTMLButtonElement>(widget, ".sa-collapsed");

  let tasks: TaskSummary[] = [];
  let selectedTaskId = "new";
  let tasksLoaded = false;
  let busy = false;
  let lastPageUrl = "";
  let lastPreviewUrl = "";
  let requestSignature = "";
  let requestKey = "";
  let captureSignature = "";
  let captureKey = "";
  let lastAddedTaskId = "";
  let bridgeBusy = false;
  let bridgeNextPoll = 0;
  let bridgeFailures = 0;

  async function pollResearchCapture(): Promise<"idle" | "captured" | "failed" | "busy"> {
    if (busy || bridgeBusy || Date.now() < bridgeNextPoll || !isSupportedProductUrl(location.href)) return "busy";
    bridgeBusy = true;
    try {
      const result = await pollCaptureBridge({
        pageUrl: () => location.href,
        send: (message) => browser.runtime.sendMessage(message),
        capture: async (url) => {
          // Report each stage: a silent stall between these steps was invisible before.
          setStatus("正在展开规格参数…");
          let expanded: string | null = null;
          try {
            expanded = await expandSpecPanel(document);
          } catch (cause) {
            // A failed expansion must not block the capture; record and continue.
            setStatus(`展开规格参数失败：${cause instanceof Error ? cause.message : String(cause)}`);
          }
          setStatus(expanded ? `已展开“${expanded}”，正在读取页面…` : "无需展开，正在读取页面…");
          return collectCurrentProductSnapshot(document, url, document.title);
        },
        status: (message) => setStatus(message),
      });
      bridgeConnection.textContent = result === "failed" ? "自动采集通道已连接，本次请求未完成，请查看工作台。" : "自动采集通道：已连接本机后台";
      bridgeConnection.dataset.error = String(result === "failed");
      bridgeFailures = 0;
      bridgeNextPoll = Date.now() + 4000;
      return result;
    } catch (cause) {
      bridgeFailures += 1;
      bridgeConnection.textContent = `自动采集通道未连接：${cause instanceof Error ? cause.message : "请刷新商品页后重试。"}`;
      bridgeConnection.dataset.error = "true";
      bridgeNextPoll = Date.now() + Math.min(60_000, 4000 * 2 ** Math.min(bridgeFailures, 4));
      return "failed";
    } finally {
      bridgeBusy = false;
    }
  }

  function setStatus(message: string, tone: "normal" | "error" | "success" | "warning" = "normal"): void {
    statusElement.textContent = message;
    statusElement.dataset.tone = tone === "normal" ? "" : tone;
  }

  function selectedTask(): TaskSummary | undefined {
    return tasks.find((task) => task.id === taskSelect.value);
  }

  function updateButtonText(): void {
    addButtonText.textContent = taskSelect.value === "new" ? "创建任务并采集" : "加入并采集商品信息";
  }

  function renderCapturePreview(force = false): void {
    const url = location.href;
    if (!force && url === lastPreviewUrl) return;
    lastPreviewUrl = url;
    if (!isSupportedProductUrl(url)) {
      capturePreviewElement.textContent = "进入商品详情页后显示可识别信息。";
      return;
    }
    const snapshot = collectCurrentProductSnapshot(document, url, document.title.trim());
    const specCount = (snapshot.fields ?? []).filter((field) => field.field_path.startsWith("specs.")).length;
    const loadedImages = (snapshot.images ?? []).filter((image) => image.status === "observed" && image.loaded).length;
    const variant = snapshot.selected_variant.label || "规格待核对";
    const price = snapshot.prices?.[0]?.raw_text || "价格未识别";
    capturePreviewElement.textContent = `${variant} · ${price} · ${specCount} 项参数 · ${loadedImages} 张已加载图片。点击时会重新读取。`;
  }

  function updatePage(): boolean {
    const url = location.href;
    const changed = url !== lastPageUrl;
    if (changed) {
      lastPageUrl = url;
      requestSignature = "";
      requestKey = "";
      captureSignature = "";
      captureKey = "";
      lastPreviewUrl = "";
      statusElement.dataset.tone = "";
      statusElement.textContent = "";
      openTaskButton.hidden = true;
      lastAddedTaskId = "";
    }
    const isMarketplace = isSupportedMarketplaceUrl(url);
    const isProduct = isMarketplace && isSupportedProductUrl(url);
    pageTitleElement.textContent = (isProduct ? getCurrentProductTitle(document, document.title) : document.title.trim()) || "当前商品页面";
    renderCapturePreview();
    addButton.disabled = busy || !isProduct || !tasksLoaded;
    if (!isMarketplace) setStatus("悬浮助手只在淘宝、天猫和京东页面可用。");
    else if (!isProduct) setStatus("请进入商品详情页后添加，购物车和订单页不会加入。");
    else if (!tasksLoaded) setStatus("正在读取本地比较任务…");
    else if (!busy && !["success", "error", "warning"].includes(statusElement.dataset.tone ?? "")) {
      setStatus(taskSelect.value === "new"
        ? "会创建新任务，并采集页面上可核对的信息。"
        : "点击后会加入商品，并保存可识别的规格、价格和图片来源。");
    }
    return changed;
  }

  function renderTasks(nextTasks: TaskSummary[], preferredId: string): void {
    tasks = nextTasks;
    const validPreferred = nextTasks.find((task) => task.id === preferredId)?.id;
    const fallback = nextTasks[0]?.id ?? "new";
    selectedTaskId = validPreferred ?? fallback;
    taskSelect.replaceChildren(new Option("创建一个新比较任务", "new"));
    for (const task of nextTasks) {
      const option = new Option(`${task.title} · ${task.item_count} 个商品`, task.id);
      taskSelect.add(option);
    }
    taskSelect.value = selectedTaskId;
    updateButtonText();
  }

  async function loadTasks(): Promise<void> {
    tasksLoaded = false;
    addButton.disabled = true;
    setStatus("正在连接本机服务…");
    try {
      const response = (await browser.runtime.sendMessage({ type: "shopping-agent:list-tasks" })) as ListTasksResponse;
      if (!response?.ok || !Array.isArray(response.tasks)) {
        throw new Error(response?.error || "暂时无法读取任务，请重试。");
      }
      const stored = await browser.storage.local.get(LAST_TASK_STORAGE_KEY);
      const lastTaskId = stored[LAST_TASK_STORAGE_KEY];
      const preferred = typeof lastTaskId === "string" ? lastTaskId : "";
      renderTasks(response.tasks, preferred);
      tasksLoaded = true;
      if (selectedTaskId !== "new") {
        await browser.storage.local.set({ [LAST_TASK_STORAGE_KEY]: selectedTaskId });
      }
      updatePage();
    } catch (cause) {
      tasksLoaded = false;
      addButton.disabled = true;
      setStatus(cause instanceof Error ? cause.message : "无法连接本机服务，请重试。", "error");
    }
  }

  taskSelect.addEventListener("change", () => {
    selectedTaskId = taskSelect.value;
    requestSignature = "";
    requestKey = "";
    captureSignature = "";
    captureKey = "";
    openTaskButton.hidden = true;
    updateButtonText();
    void browser.storage.local.set({ [LAST_TASK_STORAGE_KEY]: selectedTaskId === "new" ? "" : selectedTaskId });
    if (selectedTaskId === "new") setStatus("会创建新任务，并采集页面上可核对的信息。");
    else setStatus("点击后会加入商品，并保存可识别的规格、价格和图片来源。");
  });

  element<HTMLButtonElement>(widget, ".sa-refresh").addEventListener("click", () => void loadTasks());
  element<HTMLButtonElement>(widget, ".sa-preview-refresh").addEventListener("click", () => renderCapturePreview(true));
  element<HTMLButtonElement>(widget, ".sa-collapse").addEventListener("click", () => widget.classList.add("is-collapsed"));
  collapsedButton.addEventListener("click", () => widget.classList.remove("is-collapsed"));

  addButton.addEventListener("click", async () => {
    const url = location.href;
    if (!isSupportedProductUrl(url)) return updatePage();
    const pageTitle = getCurrentProductTitle(document, document.title).slice(0, 512);
    const targetId = taskSelect.value === "new" ? null : taskSelect.value;
    const targetTitle = selectedTask()?.title ?? "新比较任务";
    // Submitting a real snapshot: expand a collapsed parameter table first. A failure here
    // must not prevent the snapshot from being taken.
    try {
      await expandSpecPanel(document);
    } catch {
      // Ignored on purpose: the snapshot below is still worth saving.
    }
    const capture: CaptureSnapshotSubmit = collectCurrentProductSnapshot(document, url, pageTitle);
    const addSignature = JSON.stringify([url, pageTitle, targetId]);
    if (addSignature !== requestSignature) {
      requestSignature = addSignature;
      requestKey = crypto.randomUUID();
    }
    const nextCaptureSignature = JSON.stringify({ ...capture, captured_at: "" });
    if (nextCaptureSignature !== captureSignature) {
      captureSignature = nextCaptureSignature;
      captureKey = crypto.randomUUID();
    }

    busy = true;
    widget.classList.add("is-busy");
    addButton.disabled = true;
    openTaskButton.hidden = true;
    setStatus("正在加入任务…");
    try {
      const response = (await browser.runtime.sendMessage({
        type: "shopping-agent:add-current-page",
        url,
        title: pageTitle,
        taskId: targetId,
        taskTitle: targetTitle,
        idempotencyKey: requestKey,
        captureIdempotencyKey: captureKey,
        capture,
      })) as AddItemResponse;
      if (!response?.ok || !response.taskId) throw new Error(response?.error || "添加失败，请重试。");

      lastAddedTaskId = response.taskId;
      selectedTaskId = response.taskId;
      await browser.storage.local.set({ [LAST_TASK_STORAGE_KEY]: response.taskId });
      await loadTasks();
      taskSelect.value = response.taskId;
      updateButtonText();
      openTaskButton.hidden = false;
      if (response.captureError) {
        setStatus(`链接已加入“${response.taskTitle || targetTitle}”，但快照未保存：${response.captureError}`, "error");
      } else if (response.captureStatus === "complete") {
        setStatus(`已加入“${response.taskTitle || targetTitle}”并保存商品快照。`, "success");
        captureSignature = "";
        captureKey = "";
      } else {
        setStatus("已加入并保存部分商品信息；请在工作台核对缺失字段。", "warning");
        captureSignature = "";
        captureKey = "";
      }
    } catch (cause) {
      setStatus(cause instanceof Error ? cause.message : "添加失败，请重试。", "error");
      if (cause instanceof Error && cause.message.includes("任务不存在")) await loadTasks();
    } finally {
      busy = false;
      widget.classList.remove("is-busy");
      addButton.disabled = !isSupportedProductUrl(location.href) || !tasksLoaded;
    }
  });

  openTaskButton.addEventListener("click", async () => {
    if (!lastAddedTaskId) return;
    const response = (await browser.runtime.sendMessage({
      type: "shopping-agent:open-task",
      taskId: lastAddedTaskId,
    })) as { ok?: boolean; error?: string };
    if (!response?.ok) setStatus(response?.error || "无法打开任务工作台。", "error");
  });

  ctx.addEventListener(window, "wxt:locationchange", () => {
    if (!updatePage()) return;
    if (isSupportedMarketplaceUrl(location.href)) void loadTasks();
  });

  updatePage();
  await loadTasks();
  // The service worker drives polling through an alarm, so a backgrounded tab still answers:
  // a timer in this page is frozen along with its tab. The local interval remains as a fast
  // path while the tab is in front.
  browser.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
    const request = message as { type?: string; url?: string } | null;
    if (request?.type !== "shopping-agent:poll-capture-now") return;
    if (request.url && request.url !== location.href) {
      sendResponse({ ok: true, job: null });
      return;
    }
    void pollResearchCapture().then(
      (result) => sendResponse({ ok: true, job: result === "captured" }),
      () => sendResponse({ ok: false }),
    );
    return true; // Keep the channel open for the async reply.
  });
  ctx.setInterval(() => void pollResearchCapture(), 4000);
  void pollResearchCapture();
}

export default defineContentScript({
  matches: [
    "https://taobao.com/*",
    "https://*.taobao.com/*",
    "https://tmall.com/*",
    "https://*.tmall.com/*",
    "https://jd.com/*",
    "https://*.jd.com/*",
  ],
  runAt: "document_idle",
  allFrames: false,
  async main(ctx) {
    if (window.top !== window) return;
    const ui = await createShadowRootUi(ctx, {
      name: "shopping-agent-product-assistant",
      position: "overlay",
      alignment: "bottom-right",
      zIndex: 2_147_483_647,
      anchor: document.body,
      append: "last",
      isolateEvents: true,
      mode: "closed",
      css: OVERLAY_CSS,
      onMount: (container) => { void mountProductAssistant(container, ctx); },
    });
    ui.mount();
  },
});
