import { browser } from "wxt/browser";
import { createShadowRootUi } from "wxt/utils/content-script-ui/shadow-root";
import type { ContentScriptContext } from "wxt/utils/content-script-context";
import type { components } from "../src/api/schema";
import { LAST_TASK_STORAGE_KEY } from "../src/extension/config";
import { isSupportedMarketplaceUrl, isSupportedProductUrl } from "../src/extension/page-rules";

type TaskSummary = components["schemas"]["TaskSummary"];

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
        <span class="sa-header-copy"><strong>加入商品比较</strong><small>Shopping Agent</small></span>
        <button class="sa-icon-button sa-refresh" type="button" title="刷新任务列表" aria-label="刷新任务列表">↻</button>
        <button class="sa-icon-button sa-collapse" type="button" title="收起助手" aria-label="收起助手">−</button>
      </header>
      <div class="sa-preview">
        <span class="sa-preview-label">当前页面</span>
        <strong class="sa-preview-title"></strong>
      </div>
      <div class="sa-form">
        <label class="sa-label" for="sa-task-select">加入到</label>
        <select id="sa-task-select" class="sa-select" aria-label="选择比较任务"></select>
        <button class="sa-button" type="button" disabled><span class="sa-spinner">⋯ </span><span class="sa-button-text">正在连接本机服务…</span></button>
        <div class="sa-status" role="status" aria-live="polite"></div>
      </div>
      <footer class="sa-footer">
        <span>只在点击后提交页面标题和链接</span>
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
  let requestSignature = "";
  let requestKey = "";
  let lastAddedTaskId = "";

  function setStatus(message: string, tone: "normal" | "error" | "success" = "normal"): void {
    statusElement.textContent = message;
    statusElement.dataset.tone = tone === "normal" ? "" : tone;
  }

  function selectedTask(): TaskSummary | undefined {
    return tasks.find((task) => task.id === taskSelect.value);
  }

  function updateButtonText(): void {
    addButtonText.textContent = taskSelect.value === "new" ? "创建任务并添加" : "添加到所选任务";
  }

  function updatePage(): boolean {
    const url = location.href;
    const changed = url !== lastPageUrl;
    if (changed) {
      lastPageUrl = url;
      requestSignature = "";
      requestKey = "";
      openTaskButton.hidden = true;
      lastAddedTaskId = "";
    }
    pageTitleElement.textContent = document.title.trim() || "当前商品页面";
    const isMarketplace = isSupportedMarketplaceUrl(url);
    const isProduct = isMarketplace && isSupportedProductUrl(url);
    addButton.disabled = busy || !isProduct || !tasksLoaded;
    if (!isMarketplace) setStatus("悬浮助手只在淘宝、天猫和京东页面可用。");
    else if (!isProduct) setStatus("请进入商品详情页后添加，购物车和订单页不会加入。");
    else if (!tasksLoaded) setStatus("正在读取本地比较任务…");
    else if (!busy && statusElement.dataset.tone !== "success" && statusElement.dataset.tone !== "error") {
      setStatus(taskSelect.value === "new" ? "会创建新任务并加入当前商品。" : "选择好任务后，点击一次即可加入。");
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
    openTaskButton.hidden = true;
    updateButtonText();
    void browser.storage.local.set({ [LAST_TASK_STORAGE_KEY]: selectedTaskId === "new" ? "" : selectedTaskId });
    if (selectedTaskId === "new") setStatus("会创建新任务并加入当前商品。");
    else setStatus("选择好任务后，点击一次即可加入。");
  });

  element<HTMLButtonElement>(widget, ".sa-refresh").addEventListener("click", () => void loadTasks());
  element<HTMLButtonElement>(widget, ".sa-collapse").addEventListener("click", () => widget.classList.add("is-collapsed"));
  collapsedButton.addEventListener("click", () => widget.classList.remove("is-collapsed"));

  addButton.addEventListener("click", async () => {
    const url = location.href;
    if (!isSupportedProductUrl(url)) return updatePage();
    const pageTitle = document.title.trim().slice(0, 512);
    const targetId = taskSelect.value === "new" ? null : taskSelect.value;
    const targetTitle = selectedTask()?.title ?? "新比较任务";
    const signature = JSON.stringify([url, pageTitle, targetId]);
    if (signature !== requestSignature) {
      requestSignature = signature;
      requestKey = crypto.randomUUID();
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
      })) as AddItemResponse;
      if (!response?.ok || !response.taskId) throw new Error(response?.error || "添加失败，请重试。");

      lastAddedTaskId = response.taskId;
      selectedTaskId = response.taskId;
      await browser.storage.local.set({ [LAST_TASK_STORAGE_KEY]: response.taskId });
      await loadTasks();
      taskSelect.value = response.taskId;
      updateButtonText();
      openTaskButton.hidden = false;
      setStatus(`已加入“${response.taskTitle || targetTitle}” · 尚未采集或分析。`, "success");
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
