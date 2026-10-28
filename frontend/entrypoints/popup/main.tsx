import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { browser } from "wxt/browser";
import { BACKEND_STORAGE_KEY, DEFAULT_BACKEND_URL, validateBackendUrl } from "../../src/extension/config";
import { isSupportedMarketplaceUrl, isSupportedProductUrl } from "../../src/extension/page-rules";
import "./popup.css";

interface ActivePage {
  title: string;
  url: string;
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

  const pageHost = (() => {
    try { return page?.url ? new URL(page.url).hostname : "当前标签页不可读取"; }
    catch { return "当前标签页不可读取"; }
  })();
  const pageGuidance = !page
    ? "正在读取当前页面…"
    : isSupportedProductUrl(page.url)
      ? "商品页已就绪。使用页面右下角的悬浮助手，单击即可加入。"
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
      <footer className="popup-footer"><span className="privacy-lock">▣</span> 由你单击后提交商品页标题和链接</footer>
    </main>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><Popup /></React.StrictMode>,
);
