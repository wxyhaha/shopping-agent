import { browser } from "wxt/browser";
import { createShadowRootUi } from "wxt/utils/content-script-ui/shadow-root";
import type { ContentScriptContext } from "wxt/utils/content-script-context";
import type { components } from "../src/api/schema";
import { collectCurrentProductSnapshot, getCurrentProductTitle } from "../src/extension/capture-adapters";
import { captureOpenedProduct, captureOpenedProductWithScreenshots, isSamePageDocumentAddress } from "../src/extension/page-capture";
import { captureSummary } from "../src/extension/capture-preparation";
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
    max-height: calc(100vh - 124px);
    overflow: auto;
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
  .sa-detail-action { padding: 0 15px 10px; }
  .sa-cancel-capture { display: block; min-height: 30px; margin: 4px auto 0; color: #9d4138; font-size: 12px; text-decoration: underline; }
  .sa-cancel-capture[hidden] { display: none; }
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
  .sa-detail-read-button[hidden] { display: none; }
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
  itemId?: string;
  snapshotId?: string;
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
        <button class="sa-preview-refresh" type="button">读取预览</button>
      </div>
      <div class="sa-detail-action">
        <button class="sa-button sa-detail-read-button" type="button" title="同一趟滚动保存商品信息并读取详情图文字" hidden>
          <span class="sa-spinner">⋯ </span><span class="sa-detail-read-text">采集商品并识别详情图</span>
        </button>
        <button class="sa-link-button sa-cancel-capture" type="button" hidden>取消本次读取</button>
      </div>
      <div class="sa-bridge-connection" role="status">自动采集通道：正在连接…</div>
      <div class="sa-form">
        <label class="sa-label" for="sa-task-select">加入到</label>
        <select id="sa-task-select" class="sa-select" aria-label="选择比较任务"></select>
        <button class="sa-button sa-add-button" type="button" disabled><span class="sa-spinner">⋯ </span><span class="sa-button-text">正在连接本机服务…</span></button>
        <div class="sa-status" role="status" aria-live="polite"></div>
      </div>
      <footer class="sa-footer">
        <span>工具栏读取可在同一趟滚动中保存商品信息并识别详情图</span>
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
  const addButton = element<HTMLButtonElement>(widget, ".sa-add-button");
  const addButtonText = element<HTMLSpanElement>(widget, ".sa-button-text");
  const detailReadButton = element<HTMLButtonElement>(widget, ".sa-detail-read-button");
  const detailReadButtonText = element<HTMLSpanElement>(widget, ".sa-detail-read-text");
  const statusElement = element<HTMLDivElement>(widget, ".sa-status");
  const openTaskButton = element<HTMLButtonElement>(widget, ".sa-open-task");
  const collapsedButton = element<HTMLButtonElement>(widget, ".sa-collapsed");
  const cancelCaptureButton = element<HTMLButtonElement>(widget, ".sa-cancel-capture");
  let captureAbort: AbortController | null = null;
  cancelCaptureButton.addEventListener("click", () => {
    if (!captureAbort || captureAbort.signal.aborted) return;
    captureAbort.abort();
    setStatus("正在停止当前读取…", "warning");
  });

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
  let lastSavedSnapshotTaskId = "";
  let lastSavedSnapshotPageUrl = "";
  let bridgeBusy = false;
  let bridgeNextPoll = 0;
  let bridgeFailures = 0;

  async function pollResearchCapture(): Promise<"idle" | "captured" | "failed" | "busy"> {
    if (busy || bridgeBusy || Date.now() < bridgeNextPoll || !isSupportedProductUrl(location.href)) return "busy";
    bridgeBusy = true;
    addButton.disabled = true;
    try {
      const result = await pollCaptureBridge({
        pageUrl: () => location.href,
        send: (message) => browser.runtime.sendMessage(message),
        capture: async (url) => {
          const snapshot = await captureOpenedProduct(url, { scroll: false, status: setStatus });
          capturePreviewElement.textContent = captureSummary(snapshot);
          return snapshot;
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
      updateButtonText();
      detailReadButton.disabled = busy || !tasksLoaded || !isSupportedDetailImagePage(location.href);
    }
  }

  function setStatus(message: string, tone: "normal" | "error" | "success" | "warning" = "normal"): void {
    statusElement.textContent = message;
    statusElement.dataset.tone = tone === "normal" ? "" : tone;
  }

  function isSupportedDetailImagePage(url: string): boolean {
    try {
      const host = new URL(url).hostname.toLowerCase();
      return isSupportedProductUrl(url) && (host.endsWith(".taobao.com") || host.endsWith(".tmall.com"));
    } catch {
      return false;
    }
  }

  function selectedTask(): TaskSummary | undefined {
    return tasks.find((task) => task.id === taskSelect.value);
  }

  function updateButtonText(): void {
    const alreadySavedForTask = lastSavedSnapshotTaskId === taskSelect.value &&
      Boolean(lastSavedSnapshotPageUrl) && isSamePageDocumentAddress(location.href, lastSavedSnapshotPageUrl);
    if (alreadySavedForTask) {
      addButtonText.textContent = "已加入当前任务";
      addButton.disabled = true;
      addButton.title = "当前商品已保存到所选任务；需要重读时点击工具栏图标。";
      return;
    }
    addButtonText.textContent = taskSelect.value === "new" ? "创建任务并采集" : "加入并采集商品信息";
    addButton.disabled = busy || bridgeBusy || !tasksLoaded || !isSupportedProductUrl(location.href);
    addButton.title = "只保存商品基础快照；详情图完整读取请点击工具栏图标。";
  }

  function getAddRequestKeys(
    url: string,
    pageTitle: string,
    targetId: string | null,
    capture: CaptureSnapshotSubmit,
    forceNewCapture = false,
  ): { idempotencyKey: string; captureIdempotencyKey: string } {
    const addSignature = JSON.stringify([url, pageTitle, targetId]);
    if (addSignature !== requestSignature) {
      requestSignature = addSignature;
      requestKey = crypto.randomUUID();
    }
    const nextCaptureSignature = JSON.stringify({ ...capture, captured_at: "" });
    if (forceNewCapture || nextCaptureSignature !== captureSignature) {
      captureSignature = nextCaptureSignature;
      captureKey = crypto.randomUUID();
    }
    return { idempotencyKey: requestKey, captureIdempotencyKey: captureKey };
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
    capturePreviewElement.textContent = captureSummary(snapshot);
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
    detailReadButton.hidden = !isSupportedDetailImagePage(url);
    detailReadButton.disabled = busy || bridgeBusy || !tasksLoaded || !isSupportedDetailImagePage(url);
    detailReadButtonText.textContent = busy && detailReadButton.dataset.reading === "true" ? "正在读取详情图…" : "采集商品并识别详情图";
    pageTitleElement.textContent = (isProduct ? getCurrentProductTitle(document, document.title) : document.title.trim()) || "当前商品页面";
    renderCapturePreview();
    updateButtonText();
    if (!isMarketplace) setStatus("悬浮助手只在淘宝、天猫和京东页面可用。");
    else if (!isProduct) setStatus("请进入商品详情页后添加，购物车和订单页不会加入。");
    else if (!tasksLoaded) setStatus("正在读取本地比较任务…");
    else if (!busy && !["success", "error", "warning"].includes(statusElement.dataset.tone ?? "")) {
      setStatus(taskSelect.value === "new"
        ? "工具栏读取会创建新任务，并同时保存商品信息和详情图文字。"
        : "工具栏读取会同时保存商品信息和详情图文字；也可单独加入基础快照。");
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
    detailReadButton.disabled = true;
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
      detailReadButton.disabled = true;
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
    if (selectedTaskId === "new") setStatus("工具栏读取会创建新任务，并同时保存商品信息和详情图文字。");
    else setStatus("工具栏读取会同时保存商品信息和详情图文字；也可单独加入基础快照。");
  });

  element<HTMLButtonElement>(widget, ".sa-refresh").addEventListener("click", () => {
    if (!busy && !bridgeBusy) void loadTasks();
  });
  element<HTMLButtonElement>(widget, ".sa-preview-refresh").addEventListener("click", async () => {
    if (busy || bridgeBusy || !isSupportedProductUrl(location.href)) return;
    busy = true;
    captureAbort = new AbortController();
    addButton.disabled = true;
    detailReadButton.disabled = true;
    taskSelect.disabled = true;
    cancelCaptureButton.hidden = false;
    widget.classList.add("is-busy");
    try {
      const snapshot = await captureOpenedProduct(location.href, {
        scroll: true, signal: captureAbort.signal, untilCutoff: true,
        status: setStatus,
      });
      capturePreviewElement.textContent = captureSummary(snapshot);
      setStatus("已在本地读取预览，尚未保存到任务。连接服务后可点击加入并采集。", "warning");
    } catch (cause) {
      setStatus(cause instanceof Error ? cause.message : "读取预览失败。", "error");
    } finally {
      busy = false;
      captureAbort = null;
      taskSelect.disabled = false;
      cancelCaptureButton.hidden = true;
      widget.classList.remove("is-busy");
      updateButtonText();
      detailReadButton.disabled = bridgeBusy || !tasksLoaded || !isSupportedDetailImagePage(location.href);
    }
  });
  detailReadButton.addEventListener("click", () => void readDetailImages());
  element<HTMLButtonElement>(widget, ".sa-collapse").addEventListener("click", () => {
    if (!busy) widget.classList.add("is-collapsed");
  });
  collapsedButton.addEventListener("click", () => widget.classList.remove("is-collapsed"));

  function assertNotCancelled(signal: AbortSignal): void {
    if (signal.aborted) throw new Error("DETAIL_READ_CANCELLED");
  }

  function waitForDetailPoll(milliseconds: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        signal.removeEventListener("abort", onAbort);
        resolve();
      }, milliseconds);
      const onAbort = () => {
        window.clearTimeout(timer);
        reject(new Error("DETAIL_READ_CANCELLED"));
      };
      signal.addEventListener("abort", onAbort, { once: true });
      if (signal.aborted) onAbort();
    });
  }

  async function readDetailImages(): Promise<void> {
    const url = location.href;
    const initialScroll = { left: window.scrollX, top: window.scrollY };
    if (busy || bridgeBusy) return;
    if (!isSupportedDetailImagePage(url)) {
      setStatus("详情图文字读取目前支持淘宝和天猫商品页。", "warning");
      return;
    }
    const taskId = taskSelect.value === "new" ? null : taskSelect.value;
    const targetTitle = selectedTask()?.title ?? "新比较任务";

    busy = true;
    const controller = new AbortController();
    captureAbort = controller;
    detailReadButton.dataset.reading = "true";
    detailReadButton.disabled = true;
    detailReadButtonText.textContent = "正在读取详情图…";
    addButton.disabled = true;
    taskSelect.disabled = true;
    cancelCaptureButton.hidden = false;
    widget.classList.add("is-busy");
    let submittedCount = 0;
    let inFlightBatchSize = 0;
    let baseSnapshotSaved = false;

    try {
      setStatus("正在同一趟滚动中读取商品信息并截取详情图…");
      const { snapshot, result } = await captureOpenedProductWithScreenshots(url, {
        scroll: true,
        signal: controller.signal,
        untilCutoff: true,
        status: setStatus,
        captureVisibleTab: async () => {
          const response = await browser.runtime.sendMessage({
            type: "shopping-agent:capture-visible-tab",
          }) as { ok?: boolean; screenshot_url?: string; error?: string };
          if (!response?.ok || !response.screenshot_url) {
            throw new Error(response?.error || "无法截取当前详情图区域。");
          }
          return response.screenshot_url;
        },
      });
      if (!isSamePageDocumentAddress(location.href, url)) throw new Error("商品页已变化，详情截图已停止。");
      capturePreviewElement.textContent = captureSummary(snapshot);
      const pageTitle = getCurrentProductTitle(document, document.title).slice(0, 512);
      const keys = getAddRequestKeys(url, pageTitle, taskId, snapshot, true);
      assertNotCancelled(controller.signal);

      setStatus("正在保存本次读取的商品基础快照…");
      const saved = await browser.runtime.sendMessage({
        type: "shopping-agent:add-current-page",
        url,
        title: pageTitle,
        taskId,
        taskTitle: targetTitle,
        ...keys,
        capture: snapshot,
      }) as AddItemResponse;
      if (!saved?.ok || !saved.taskId) throw new Error(saved?.error || "商品基础快照保存失败。");
      lastAddedTaskId = saved.taskId;
      selectedTaskId = saved.taskId;
      await browser.storage.local.set({ [LAST_TASK_STORAGE_KEY]: saved.taskId });
      await loadTasks();
      taskSelect.value = saved.taskId;
      updateButtonText();
      openTaskButton.hidden = false;
      if (saved.captureError || !saved.itemId || !saved.snapshotId) {
        throw new Error(`商品已加入“${saved.taskTitle || targetTitle}”，但基础快照未能保存，未开始详情图识别：${saved.captureError || "服务未返回快照编号。"}`);
      }
      baseSnapshotSaved = true;
      lastSavedSnapshotTaskId = saved.taskId;
      lastSavedSnapshotPageUrl = url;
      updateButtonText();
      assertNotCancelled(controller.signal);

      const screenshots = result.screenshots;
      if (!screenshots.length) {
        const missing = result.reason === "cutoff_not_found"
          ? "已到达详情容器末端，但没有定位到详情图片，截止位置未确认。"
          : "本次读取范围没有找到已加载的详情图图片。";
        setStatus(`商品基础快照已保存，但${missing}本次未进行图片文字识别。`, "warning");
        return;
      }

      const batchSize = 16;
      const batchCount = Math.ceil(screenshots.length / batchSize);
      for (let offset = 0; offset < screenshots.length; offset += batchSize) {
        assertNotCancelled(controller.signal);
        if (!isSamePageDocumentAddress(location.href, url)) throw new Error("商品页已变化，详情截图已停止。");
        const batch = screenshots.slice(offset, offset + batchSize);
        const batchIndex = Math.floor(offset / batchSize) + 1;
        setStatus(`正在提交第 ${batchIndex}/${batchCount} 批截图…`);
        inFlightBatchSize = batch.length;
        const uploaded = await browser.runtime.sendMessage({
          type: "shopping-agent:detail-upload",
          taskId: saved.taskId,
          itemId: saved.itemId,
          snapshotId: saved.snapshotId,
          pageUrl: url,
          screenshots: batch,
        }) as { ok?: boolean; snapshot_id?: string; status?: "queued" | "already_processed"; screenshot_count?: number; error?: string };
        if (!uploaded?.ok || typeof uploaded.screenshot_count !== "number") {
          throw new Error(uploaded?.error || "本机服务未能接收本批截图。");
        }
        submittedCount += uploaded.screenshot_count;
        inFlightBatchSize = 0;
        assertNotCancelled(controller.signal);

        if (uploaded.status === "queued") {
          const isLastBatch = offset + batch.length >= screenshots.length;
          setStatus(isLastBatch
            ? `最后一批已提交，正在等待详情图识别完成…`
            : `第 ${batchIndex}/${batchCount} 批已提交，等待识别完成后继续…`);
          while (true) {
            await waitForDetailPoll(1000, controller.signal);
            assertNotCancelled(controller.signal);
            const latest = await browser.runtime.sendMessage({
              type: "shopping-agent:detail-captures",
              taskId: saved.taskId,
              itemId: saved.itemId,
              snapshotId: saved.snapshotId,
            }) as { ok?: boolean; captures?: components["schemas"]["CaptureSnapshotRead"][]; error?: string };
            if (!latest?.ok || !Array.isArray(latest.captures)) {
              throw new Error(latest?.error || "无法确认详情图识别状态，已停止后续批次。");
            }
            const current = latest.captures.find((capture) => capture.id === saved.snapshotId);
            if (!current) throw new Error("原始商品快照已不在最近记录中，后续截图批次已停止。");
            if (current.detail_enrichment !== "pending") break;
          }
        }
      }

      assertNotCancelled(controller.signal);
      const partial = result.reason === "cutoff_not_found"
        ? "已到达详情区域末端，但没有定位到最后一张图片，截止位置未确认。"
        : result.reason === "settled"
          ? "已识别最后一张图片的位置并到达详情区域末端。"
          : "本次读取没有确认详情图片截止位置。";
      setStatus(`基础快照已保存；已分 ${batchCount} 批提交 ${submittedCount} 段截图，详情图识别已完成。本地不保存原图。${partial}`,
        result.reason === "settled" ? "success" : "warning");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "详情图读取失败。";
      if (controller.signal.aborted || message === "DETAIL_READ_CANCELLED") {
        const confirmed = submittedCount > 0 ? `已确认接收 ${submittedCount} 段截图` : "此前没有确认接收截图";
        const uncertain = inFlightBatchSize > 0
          ? `；取消时正在发送的 ${inFlightBatchSize} 段可能已被服务端接收`
          : "";
        const retained = submittedCount > 0 ? "，其后台识别会继续" : "";
        const baseNote = baseSnapshotSaved ? "基础快照已保存；" : "基础快照尚未确认保存；";
        setStatus(`已取消本次读取；${baseNote}${confirmed}${uncertain}${retained}，未开始的后续批次已停止。`, "warning");
      } else {
        const baseNote = baseSnapshotSaved ? "基础快照已保存；" : "";
        const detail = inFlightBatchSize > 0
          ? `${message} 当前 ${inFlightBatchSize} 段截图的提交状态未知，请先检查工作台再重试。`
          : message;
        setStatus(`${baseNote}${detail}`, "error");
      }
    } finally {
      busy = false;
      captureAbort = null;
      taskSelect.disabled = false;
      cancelCaptureButton.hidden = true;
      detailReadButton.dataset.reading = "false";
      detailReadButtonText.textContent = "采集商品并识别详情图";
      detailReadButton.disabled = bridgeBusy || !tasksLoaded || !isSupportedDetailImagePage(location.href);
      widget.classList.remove("is-busy");
      updateButtonText();
      if (baseSnapshotSaved) {
        captureSignature = "";
        captureKey = "";
      }
      if (controller.signal.aborted) {
        const restoreInitialScroll = () => {
          if (document.visibilityState !== "visible" || !isSamePageDocumentAddress(location.href, url)) return;
          window.scrollTo({ ...initialScroll, behavior: "instant" });
        };
        restoreInitialScroll();
        window.requestAnimationFrame(restoreInitialScroll);
      }
    }
  }

  addButton.addEventListener("click", async () => {
    const url = location.href;
    if (busy || bridgeBusy) return;
    if (!isSupportedProductUrl(url)) return updatePage();
    const targetId = taskSelect.value === "new" ? null : taskSelect.value;
    const targetTitle = selectedTask()?.title ?? "新比较任务";
    busy = true;
    captureAbort = new AbortController();
    widget.classList.add("is-busy");
    addButton.disabled = true;
    detailReadButton.disabled = true;
    taskSelect.disabled = true;
    cancelCaptureButton.hidden = false;
    openTaskButton.hidden = true;
    try {
      const capture: CaptureSnapshotSubmit = await captureOpenedProduct(url, {
        scroll: true, signal: captureAbort.signal, untilCutoff: true,
        status: setStatus,
      });
      capturePreviewElement.textContent = captureSummary(capture);
      const pageTitle = getCurrentProductTitle(document, document.title).slice(0, 512);
      const keys = getAddRequestKeys(url, pageTitle, targetId, capture);
      // Cancellation only applies before upload; after sending, the server may commit.
      if (captureAbort.signal.aborted || location.href !== url) throw new Error("采集已取消或页面已变化，未提交快照。");
      cancelCaptureButton.hidden = true;
      setStatus("正在保存商品快照…");
      const response = (await browser.runtime.sendMessage({
        type: "shopping-agent:add-current-page",
        url,
        title: pageTitle,
        taskId: targetId,
        taskTitle: targetTitle,
        ...keys,
        capture,
      })) as AddItemResponse;
      if (!response?.ok || !response.taskId) throw new Error(response?.error || "添加失败，请重试。");

      lastAddedTaskId = response.taskId;
      selectedTaskId = response.taskId;
      await browser.storage.local.set({ [LAST_TASK_STORAGE_KEY]: response.taskId });
      if (!response.captureError && response.snapshotId) {
        lastSavedSnapshotTaskId = response.taskId;
        lastSavedSnapshotPageUrl = url;
      }
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
      captureAbort = null;
      taskSelect.disabled = false;
      cancelCaptureButton.hidden = true;
      widget.classList.remove("is-busy");
      updateButtonText();
      detailReadButton.disabled = bridgeBusy || !tasksLoaded || !isSupportedDetailImagePage(location.href);
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
  browser.runtime.onMessage.addListener((message: unknown, sender, sendResponse) => {
    const request = message as { type?: string; pageUrl?: string; url?: string } | null;
    if (request?.type === "shopping-agent:start-detail-reading") {
      const senderUrl = sender.url ?? "";
      if (sender.id !== browser.runtime.id || !senderUrl.startsWith(browser.runtime.getURL(""))) {
        sendResponse({ ok: false, error: "请从 Shopping Agent 工具栏启动详情图读取。" });
        return;
      }
      if (!request.pageUrl || !isSamePageDocumentAddress(request.pageUrl, location.href)) {
        sendResponse({ ok: false, error: "商品页已变化，请重新从工具栏启动。" });
        return;
      }
      if (busy || bridgeBusy || !isSupportedDetailImagePage(location.href)) {
        sendResponse({ ok: false, error: "当前页面正在处理其他采集，或不是支持的商品页。" });
        return;
      }
      sendResponse({ ok: true, started: true });
      void readDetailImages();
      return;
    }
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
