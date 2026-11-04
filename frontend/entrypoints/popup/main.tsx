import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { browser } from "wxt/browser";
import { BACKEND_STORAGE_KEY, DEFAULT_BACKEND_URL, LAST_TASK_STORAGE_KEY, validateBackendUrl } from "../../src/extension/config";
import { isSupportedMarketplaceUrl, isSupportedProductUrl } from "../../src/extension/page-rules";
import type { components } from "../../src/api/schema";
import "./popup.css";

type TaskRead = components["schemas"]["TaskRead"];
type CaptureSnapshotRead = components["schemas"]["CaptureSnapshotRead"];

interface ActivePage {
  title: string;
  url: string;
}

interface ScreenshotFrame {
  position: number;
  scroll_y: number;
  image_base64: string;
}

interface ScreenshotResponse {
  ok: boolean;
  error?: string;
  reason?: "settled" | "cutoff_not_found" | "time_limit" | "step_limit" | "read_only";
  screenshots?: ScreenshotFrame[];
}

const DETAIL_SCREENSHOT_BATCH_SIZE = 16;

function productKey(value: string): string | null {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    if (!(host.endsWith(".taobao.com") || host.endsWith(".tmall.com"))) return null;
    if (!url.pathname.toLowerCase().endsWith("/item.htm")) return null;
    const id = url.searchParams.get("id") || url.searchParams.get("itemId");
    if (!id) return null;
    return `${host.endsWith(".tmall.com") ? "tmall" : "taobao"}:${id}`;
  } catch {
    return null;
  }
}

async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
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
    throw new Error("无法连接本机服务，请确认后台正在运行。");
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string"
      ? body.detail
      : "本机服务未能处理详情图。";
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

function wait(milliseconds: number) {
  return new Promise(resolve => setTimeout(resolve, milliseconds));
}

function detectBrowser(): "chrome" | "edge" {
  return navigator.userAgent.includes("Edg/") ? "edge" : "chrome";
}

function Popup() {
  const [backend, setBackend] = useState(DEFAULT_BACKEND_URL);
  const [page, setPage] = useState<ActivePage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [opening, setOpening] = useState(false);
  const [capturingDetails, setCapturingDetails] = useState(false);
  const [detailMessage, setDetailMessage] = useState<string | null>(null);

  useEffect(() => {
    void browser.storage.local.get(BACKEND_STORAGE_KEY).then((value) => {
      const stored = value[BACKEND_STORAGE_KEY];
      if (typeof stored === "string") setBackend(stored);
    });
    void browser.tabs.query({ active: true, currentWindow: true }).then((tabs) => {
      const active = tabs[0];
      if (active?.url) setPage({ title: active.title ?? "", url: active.url });
    });
  }, []);

  async function saveBackend() {
    setError(null);
    setSaved(false);
    try {
      const normalized = validateBackendUrl(backend);
      setSaving(true);
      await browser.storage.local.set({ [BACKEND_STORAGE_KEY]: normalized });
      setBackend(normalized);
      setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法保存服务地址。");
    } finally {
      setSaving(false);
    }
  }

  async function openWorkspace() {
    setError(null);
    try {
      const base = validateBackendUrl(backend);
      setOpening(true);
      await browser.tabs.create({ url: new URL("/", base).toString() });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法打开本地工作台。");
    } finally {
      setOpening(false);
    }
  }

  async function captureDetailImages() {
    setDetailMessage("正在确认当前商品已保存到任务…");
    setError(null);
    setCapturingDetails(true);
    try {
      const tabs = await browser.tabs.query({ active: true, currentWindow: true });
      const active = tabs[0];
      if (!active?.id || !active.url || !productKey(active.url)) {
        throw new Error("请在淘宝或天猫商品详情页使用此功能；京东当前暂停。");
      }
      const stored = await browser.storage.local.get([BACKEND_STORAGE_KEY, LAST_TASK_STORAGE_KEY]);
      const backendUrl = validateBackendUrl(typeof stored[BACKEND_STORAGE_KEY] === "string"
        ? stored[BACKEND_STORAGE_KEY] as string
        : DEFAULT_BACKEND_URL);
      const taskId = stored[LAST_TASK_STORAGE_KEY];
      if (typeof taskId !== "string" || !taskId) {
        throw new Error("请先在商品页悬浮助手中选择任务并保存当前商品。");
      }
      const task = await requestJson<TaskRead>(`${backendUrl}/api/v1/tasks/${encodeURIComponent(taskId)}`);
      const key = productKey(active.url);
      const item = task.items.find((candidate) => productKey(candidate.url) === key);
      if (!item) throw new Error("当前任务中还没有这件商品，请先用悬浮助手加入并采集。");
      const captures = await requestJson<CaptureSnapshotRead[]>(
        `${backendUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(item.id)}/captures?limit=1`,
      );
      const snapshot = captures[0];
      if (!snapshot) throw new Error("没有找到该商品的快照，请先保存一次基础采集。");

      setDetailMessage("正在从当前浏览器截取已显示的详情图区域…");
      const captured = await browser.tabs.sendMessage(active.id, {
        type: "shopping-agent:capture-detail-screenshots",
        pageUrl: active.url,
      }) as ScreenshotResponse;
      if (!captured?.ok) throw new Error(captured?.error || "扩展没有取得详情图，请刷新商品页后重试。");
      const screenshots = captured.screenshots ?? [];
      if (!screenshots.length) {
        throw new Error(captured.reason === "cutoff_not_found"
          ? "已到达详情容器末端，但没有定位到详情图片，无法确认图片截止位置。"
          : "当前读取范围没有找到已加载的详情图图片。");
      }

      let submittedCount = 0;
      const batchCount = Math.ceil(screenshots.length / DETAIL_SCREENSHOT_BATCH_SIZE);
      for (let offset = 0; offset < screenshots.length; offset += DETAIL_SCREENSHOT_BATCH_SIZE) {
        const batch = screenshots.slice(offset, offset + DETAIL_SCREENSHOT_BATCH_SIZE);
        const batchIndex = Math.floor(offset / DETAIL_SCREENSHOT_BATCH_SIZE) + 1;
        setDetailMessage(`正在提交第 ${batchIndex}/${batchCount} 批详情图截图…`);
        const result = await requestJson<{
          snapshot_id: string;
          status: "queued" | "already_processed";
          screenshot_count: number;
        }>(`${backendUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(item.id)}` +
          `/captures/${encodeURIComponent(snapshot.id)}/detail-screenshots`, {
          method: "POST",
          body: JSON.stringify({ page_url: active.url, screenshots: batch }),
        });
        submittedCount += result.screenshot_count;

        if (offset + batch.length < screenshots.length && result.status === "queued") {
          setDetailMessage(`第 ${batchIndex}/${batchCount} 批已提交，等待识别完成后继续…`);
          while (true) {
            await wait(1000);
            const latest = await requestJson<CaptureSnapshotRead[]>(
              `${backendUrl}/api/v1/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(item.id)}` +
                `/captures?limit=20`,
            );
            const current = latest.find(candidate => candidate.id === snapshot.id);
            if (!current) throw new Error("原始商品快照已不在最近记录中，后续截图批次未提交。");
            if (current.detail_enrichment !== "pending") break;
          }
        }
      }

      const partial = captured.reason === "cutoff_not_found"
        ? "已到达详情区域末端，但没有定位到最后一张图片，截止位置未确认。"
        : captured.reason === "settled"
          ? "已识别最后一张图片的位置并到达详情区域末端。"
          : "本次读取没有确认详情图片截止位置。";
      setDetailMessage(`已分 ${batchCount} 批提交 ${submittedCount} 段截图；最后一批由已配置的视觉模型异步识别，本地不保存原图。${partial}`);
    } catch (cause) {
      setDetailMessage(null);
      setError(cause instanceof Error ? cause.message : "详情图读取失败，请稍后重试。");
    } finally {
      setCapturingDetails(false);
    }
  }

  const pageHost = (() => {
    try { return page?.url ? new URL(page.url).hostname : "当前标签页不可读取"; }
    catch { return "当前标签页不可读取"; }
  })();
  const pageGuidance = !page
    ? "正在读取当前页面…"
    : productKey(page.url)
      ? "淘宝/天猫商品页已就绪。可用下方按钮读取详情图文字。"
      : isSupportedProductUrl(page.url)
        ? "京东访问当前暂停；淘宝/天猫商品页可使用详情图读取。"
      : isSupportedMarketplaceUrl(page.url)
        ? "请进入商品详情页，再使用页面内的悬浮助手添加。"
        : "悬浮助手只在淘宝、天猫和京东页面显示。";

  return (
    <main className="popup-shell">
      <header className="popup-header">
        <span className="popup-mark">SA</span>
        <div><strong>Shopping Agent</strong><small>商品比较助手</small></div>
        <span className="browser-chip">{detectBrowser() === "edge" ? "EDGE" : "CHROME"}</span>
      </header>

      <section className="page-preview">
        <div className="preview-overline"><span className="preview-dot" /> 当前标签页</div>
        <strong className="page-title">{page?.title || pageHost}</strong>
        <span className="page-host">{pageHost}</span>
      </section>

      <div className="capture-guidance" role="status">{pageGuidance}</div>

      <button
        className="detail-capture-button"
        onClick={() => void captureDetailImages()}
        disabled={capturingDetails || !productKey(page?.url ?? "")}
      >
        {capturingDetails ? "正在读取详情图…" : "读取详情图文字"}
      </button>
      {detailMessage && <div className="detail-capture-message" role="status">{detailMessage}</div>}

      <button className="workspace-button" onClick={() => void openWorkspace()} disabled={opening}>
        {opening ? "正在打开…" : "打开任务工作台"}<span aria-hidden="true">→</span>
      </button>

      <details className="settings-panel">
        <summary><span>本地服务地址</span><span className="settings-chevron">⌄</span></summary>
        <div className="settings-content">
          <label htmlFor="backend-url">后台服务</label>
          <input id="backend-url" value={backend} onChange={(event) => { setBackend(event.target.value); setSaved(false); }} spellCheck={false} />
          <button className="save-button" onClick={() => void saveBackend()} disabled={saving}>{saving ? "保存中…" : "保存地址"}</button>
          <small>仅允许 HTTPS 或本机地址。网页和扩展使用同一个本地任务库。</small>
          {saved && <span className="saved-message">服务地址已保存</span>}
        </div>
      </details>

      {error && <div className="popup-error" role="alert">{error}</div>}
      <footer className="popup-footer"><span className="privacy-lock">▣</span> 详情图将发给已配置的视觉模型；本地不保存原图</footer>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><Popup /></React.StrictMode>,
);
