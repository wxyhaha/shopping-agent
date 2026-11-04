import React, { useEffect, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { browser } from "wxt/browser";
import { BACKEND_STORAGE_KEY, DEFAULT_BACKEND_URL, validateBackendUrl } from "../../src/extension/config";
import { isSupportedMarketplaceUrl, isSupportedProductUrl } from "../../src/extension/page-rules";
import "./popup.css";

interface ActivePage {
  title: string;
  url: string;
}

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
  const [detailError, setDetailError] = useState<string | null>(null);
  const autoStartRequested = useRef(false);

  useEffect(() => {
    void browser.storage.local.get(BACKEND_STORAGE_KEY).then((value) => {
      const stored = value[BACKEND_STORAGE_KEY];
      if (typeof stored === "string") setBackend(stored);
    });
    void browser.tabs.query({ active: true, currentWindow: true }).then((tabs) => {
      const active = tabs[0];
      if (!active?.url) return;
      setPage({ title: active.title ?? "", url: active.url });
      // Opening the toolbar popup is the explicit user gesture that grants activeTab.
      // Start the floating-panel workflow now so the user does not need a second click.
      if (active.id !== undefined && productKey(active.url) && !autoStartRequested.current) {
        autoStartRequested.current = true;
        void captureDetailImages(active);
      }
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

  async function captureDetailImages(activeTab?: { id?: number; url?: string }) {
    setDetailMessage("正在把详情图读取交给右下角浮窗…");
    setDetailError(null);
    setError(null);
    setCapturingDetails(true);
    try {
      const active = activeTab ?? (await browser.tabs.query({ active: true, currentWindow: true }))[0];
      if (!active?.id || !active.url || !productKey(active.url)) {
        throw new Error("请在淘宝或天猫商品详情页使用此功能；京东当前暂停。");
      }
      const response = await browser.tabs.sendMessage(active.id, {
        type: "shopping-agent:start-detail-reading",
        pageUrl: active.url,
      }) as { ok?: boolean; started?: boolean; error?: string };
      if (!response?.ok || !response.started) throw new Error(response?.error || "详情图读取未能启动，请刷新商品页后重试。");
      setDetailMessage("已启动一趟采集：同次滚动会保存商品基础快照并识别详情图文字。进度和取消都在右下角浮窗管理，请保持商品页可见。");
    } catch (cause) {
      setDetailMessage(null);
      setDetailError(cause instanceof Error ? cause.message : "详情图读取失败，请稍后重试。");
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
      ? "本次工具栏点击会自动启动详情图读取；进度和取消操作都在右下角浮窗。"
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

      {detailMessage && <div className="detail-capture-message" role="status">{detailMessage}</div>}
      {detailError && <div className="popup-error" role="alert">
        {detailError}
        <button className="retry-capture-button" onClick={() => void captureDetailImages()} disabled={capturingDetails}>
          {capturingDetails ? "正在启动…" : "重试详情图读取"}
        </button>
      </div>}

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
