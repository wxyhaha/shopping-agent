import { collectCurrentProductSnapshot } from "./capture-adapters";
import { addPreparationCoverage, prepareDetails } from "./capture-preparation";
import { expandSpecPanel } from "./spec-panel";
import { inspectVariants } from "./variant-controller";

const DETAIL_SELECTORS = ["#J_DivItemDesc", "#description", "#detailContent", "[class*='descV8']", "[class*='detail-content']", "#detail"];
const SCREENSHOT_INTERVAL_MS = 650;

export function isSamePageDocumentAddress(left: string, right: string): boolean {
  try {
    const leftUrl = new URL(left);
    const rightUrl = new URL(right);
    leftUrl.hash = "";
    rightUrl.hash = "";
    return leftUrl.href === rightUrl.href;
  } catch {
    return false;
  }
}

export interface DetailScreenshotSegment {
  position: number;
  scroll_y: number;
  image_base64: string;
}

export interface ProductScreenshotCapture {
  screenshots: DetailScreenshotSegment[];
  reason: "settled" | "cutoff_not_found" | "time_limit" | "step_limit" | "read_only";
}

export interface PageCaptureOptions {
  scroll: boolean;
  signal?: AbortSignal;
  status?: (message: string) => void;
  budgetMs?: number;
  maxSteps?: number;
  untilCutoff?: boolean;
}

/** Read a user-opened page; no marketplace fetches, navigation, or SKU changes. */
export async function captureOpenedProduct(pageUrl: string, options: PageCaptureOptions) {
  return (await captureProduct(pageUrl, options)).snapshot;
}

/** Read the same page while the extension action has temporary permission to capture it. */
export async function captureOpenedProductWithScreenshots(
  pageUrl: string,
  options: PageCaptureOptions & { captureVisibleTab: () => Promise<string> },
): Promise<{ snapshot: Awaited<ReturnType<typeof collectCurrentProductSnapshot>>; result: ProductScreenshotCapture }> {
  const captured = await captureProduct(pageUrl, options);
  return {
    snapshot: captured.snapshot,
    result: {
      screenshots: captured.screenshots,
      reason: captured.reason,
    },
  };
}

async function cropVisibleDetailArea(
  screenshotUrl: string,
  detail: ReturnType<typeof inspectDetailProgress>,
): Promise<string> {
  if (!detail.imageBounds || !screenshotUrl.startsWith("data:image/")) {
    throw new Error("没有取得当前详情图区域截图，请从扩展工具栏重新开始。");
  }
  const image = document.createElement("img");
  image.src = screenshotUrl;
  await image.decode();
  const scaleX = image.naturalWidth / detail.viewportWidth;
  const scaleY = image.naturalHeight / detail.viewportHeight;
  const left = Math.max(0, Math.floor(detail.imageBounds.x * scaleX));
  const top = Math.max(0, Math.floor(detail.imageBounds.y * scaleY));
  const right = Math.min(image.naturalWidth, Math.ceil((detail.imageBounds.x + detail.imageBounds.width) * scaleX));
  const bottom = Math.min(image.naturalHeight, Math.ceil((detail.imageBounds.y + detail.imageBounds.height) * scaleY));
  const sourceWidth = right - left;
  const sourceHeight = bottom - top;
  if (sourceWidth < 1 || sourceHeight < 1) throw new Error("详情图截图区域无效，请重新读取。");

  let shrink = Math.min(1, 1280 / sourceWidth, 1600 / sourceHeight);
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(sourceWidth * shrink));
    canvas.height = Math.max(1, Math.round(sourceHeight * shrink));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("无法处理详情图截图，请稍后重试。");
    context.drawImage(image, left, top, sourceWidth, sourceHeight, 0, 0, canvas.width, canvas.height);
    for (const quality of [0.78, 0.66, 0.54]) {
      const base64 = canvas.toDataURL("image/jpeg", quality).split(",", 2)[1] ?? "";
      if (base64.length <= 650_000) return base64;
    }
    shrink *= 0.78;
  }
  throw new Error("详情图截图超过安全大小上限，请减少页面缩放后重试。");
}

function inspectDetailProgress(
  root: HTMLElement | null | undefined,
  candidates: HTMLImageElement[],
  view: Window,
) {
  const observed = candidates.map((image) => {
    const rect = image.getBoundingClientRect();
    const style = getComputedStyle(image);
    const source = image.currentSrc || image.getAttribute("src") || image.getAttribute("data-src") ||
      image.getAttribute("data-original") || image.getAttribute("srcset") || "";
    return { image, rect, style, source };
  });
  const positioned = observed.filter(({ rect, style }) =>
    style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0);
  const visible = positioned.filter(({ rect }) =>
    rect.right > 0 && rect.bottom > 0 && rect.left < view.innerWidth && rect.top < view.innerHeight);
  const ready = visible.filter(({ image }) => image.complete && image.naturalWidth > 0);
  const rects = ready.map(({ rect }) => rect);
  const bounds = rects.length ? {
    x: Math.max(0, Math.min(...rects.map((rect) => rect.left))),
    y: Math.max(0, Math.min(...rects.map((rect) => rect.top))),
    width: Math.min(view.innerWidth, Math.max(...rects.map((rect) => rect.right))) - Math.max(0, Math.min(...rects.map((rect) => rect.left))),
    height: Math.min(view.innerHeight, Math.max(...rects.map((rect) => rect.bottom))) - Math.max(0, Math.min(...rects.map((rect) => rect.top))),
  } : null;
  const rootRect = root?.getBoundingClientRect();
  const bottom = rootRect ? rootRect.bottom : document.documentElement.scrollHeight - view.scrollY;
  const lastImageBottom = positioned.length
    ? Math.max(...positioned.map(({ rect }) => rect.bottom + view.scrollY))
    : null;
  const imageCutoffReached = lastImageBottom !== null && lastImageBottom <= view.scrollY + view.innerHeight + 8;
  const documentBottom = rootRect
    ? rootRect.bottom + view.scrollY
    : document.documentElement.scrollHeight;
  return {
    found: Boolean(root), atEnd: bottom <= view.innerHeight + 8,
    loaded: candidates.filter((image) => image.complete && image.naturalWidth > 0).length,
    pending: candidates.filter((image) => !image.complete).length,
    positionedImages: positioned.length,
    failedImages: candidates.filter((image) => image.complete && image.naturalWidth === 0 && Boolean(
      image.currentSrc || image.getAttribute("src") || image.getAttribute("data-src") || image.getAttribute("data-original"),
    )).length,
    lastImageBottom,
    imageCutoffReached,
    visibleLoaded: ready.length,
    visiblePending: visible.filter(({ image }) => !image.complete).length,
    imageBounds: bounds && bounds.width > 0 && bounds.height > 0 ? bounds : null,
    scrollY: view.scrollY,
    viewportWidth: view.innerWidth,
    viewportHeight: view.innerHeight,
    signature: JSON.stringify([
      Boolean(root), Math.round(documentBottom), Math.round(lastImageBottom ?? -1),
      observed.map(({ image, rect, style, source }) => [
        source, image.complete, image.naturalWidth, Math.round(rect.bottom + view.scrollY),
        Math.round(rect.width), Math.round(rect.height), style.display, style.visibility,
      ]),
    ]),
  };
}

async function captureProduct(
  pageUrl: string,
  options: PageCaptureOptions & { captureVisibleTab?: () => Promise<string> },
) {
  const view = document.defaultView!;
  const origin = { x: view.scrollX, y: view.scrollY };
  const status = options.status ?? (() => {});
  const selection = () => JSON.stringify(inspectVariants().groups.map(group => [
    group.label, group.options.filter(option => option.selected).map(option => option.label),
  ]));
  const initialSelection = selection();
  const screenshots: DetailScreenshotSegment[] = [];
  let lastScreenshotAt = 0;
  let interrupted = false;
  let originRestored = false;
  const restoreOrigin = () => {
    if (originRestored || !options.scroll || (interrupted && !options.signal?.aborted) ||
        !isSamePageDocumentAddress(location.href, pageUrl) || document.visibilityState !== "visible") return;
    view.scrollTo({ left: origin.x, top: origin.y, behavior: "instant" });
    originRestored = true;
  };
  const interrupt = (event: Event) => {
    // Closed-shadow UI events are retargeted to the extension's host. Double clicks on
    // its disabled submit button must not be mistaken for a new marketplace interaction.
    const assistantHost = document.querySelector("shopping-agent-product-assistant");
    if (assistantHost && event.composedPath().includes(assistantHost)) return;
    interrupted = true;
  };
  const interruptKey = (event: KeyboardEvent) => {
    if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) interrupt(event);
  };
  const check = () => {
    if (options.signal?.aborted) throw new Error("采集已取消，未提交商品快照。");
    if (!isSamePageDocumentAddress(location.href, pageUrl)) throw new Error("商品页已变化，本次快照未提交，请在当前商品页重试。");
    if (interrupted) throw new Error("检测到页面操作，采集已停止，请选好规格后重新读取。");
    if (options.scroll && document.visibilityState !== "visible") throw new Error("商品页已切到后台，采集已停止，请回到此页重新读取。");
    if (selection() !== initialSelection) throw new Error("选中规格在采集期间发生变化，请等待价格更新后重新读取。");
  };
  const wait = async (ms: number) => {
    const end = performance.now() + ms;
    do {
      check();
      await new Promise(resolve => setTimeout(resolve, Math.min(100, Math.max(0, end - performance.now()))));
    } while (performance.now() < end);
    check();
  };
  const container = () => DETAIL_SELECTORS.map(selector => document.querySelector<HTMLElement>(selector))
    .find(node => node && node.getClientRects().length && getComputedStyle(node).visibility !== "hidden");
  const images = () => Array.from(container()?.querySelectorAll("img") ?? []);
  const inspect = () => inspectDetailProgress(container(), images(), view);
  const captureViewport = options.captureVisibleTab ? async (detail: ReturnType<typeof inspect>) => {
    if (!detail.imageBounds) return;
    await wait(Math.max(0, SCREENSHOT_INTERVAL_MS - (performance.now() - lastScreenshotAt)));
    check();
    status(`正在截取详情图区域：第 ${screenshots.length + 1} 段…`);
    const overlay = document.querySelector<HTMLElement>("shopping-agent-product-assistant");
    const oldVisibility = overlay?.style.visibility;
    if (overlay) overlay.style.visibility = "hidden";
    try {
      const screenshotUrl = await options.captureVisibleTab!();
      const image_base64 = await cropVisibleDetailArea(screenshotUrl, detail);
      screenshots.push({
        position: screenshots.length + 1,
        scroll_y: Math.max(0, Math.round(detail.scrollY)),
        image_base64,
      });
      lastScreenshotAt = performance.now();
    } finally {
      if (overlay) overlay.style.visibility = oldVisibility ?? "";
    }
  } : undefined;
  const eventOptions = { capture: true, passive: true } as const;
  if (options.scroll) {
    view.addEventListener("wheel", interrupt, eventOptions);
    view.addEventListener("touchstart", interrupt, eventOptions);
    view.addEventListener("pointerdown", interrupt, eventOptions);
    view.addEventListener("keydown", interruptKey, true);
  }
  try {
    check();
    status("正在展开规格参数…");
    // Background bridge reads stay passive; expanding a page can move the user's view.
    const expanded = options.scroll ? await expandSpecPanel(document) : null;
    check();
    status(expanded ? `已展开“${expanded}”，正在加载详情…` : "正在检查详情区…");
    const preparation = await prepareDetails({
      now: () => performance.now(), check, wait, inspect, status,
      startAtDetail: () => {
        const detailRoot = container();
        if (detailRoot) {
          view.scrollTo({
            left: view.scrollX,
            top: Math.max(0, detailRoot.getBoundingClientRect().top + view.scrollY),
            behavior: "instant",
          });
        }
      },
      ...(captureViewport ? { captureViewport } : {}),
      advance: () => view.scrollTo({ top: view.scrollY + Math.max(200, view.innerHeight * 0.75), behavior: "instant" }),
      restore: restoreOrigin,
    }, options);
    check();
    status("正在核对选中规格和价格是否稳定…");
    const first = collectCurrentProductSnapshot(document, pageUrl, document.title);
    await wait(500);
    const snapshot = collectCurrentProductSnapshot(document, pageUrl, document.title);
    check();
    const stamp = (value: typeof snapshot) => JSON.stringify([
      value.selected_variant.status, value.selected_variant.label,
      value.prices?.map(price => [price.raw_text, price.amount, price.kind]),
    ]);
    if (stamp(first) !== stamp(snapshot)) throw new Error("规格或价格仍在更新，本次快照未提交，请稍后重新读取。");
    return {
      snapshot: addPreparationCoverage(snapshot, preparation),
      screenshots,
      reason: preparation.reason,
    };
  } finally {
    // Also restore when cancellation or an error happens before prepareDetails starts.
    restoreOrigin();
    view.removeEventListener("wheel", interrupt, true);
    view.removeEventListener("touchstart", interrupt, true);
    view.removeEventListener("pointerdown", interrupt, true);
    view.removeEventListener("keydown", interruptKey, true);
  }
}
