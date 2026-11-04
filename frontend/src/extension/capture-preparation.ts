import type { components } from "../api/schema.d.ts";

type Snapshot = components["schemas"]["CaptureSnapshotSubmit"];

export interface DetailProgress {
  found: boolean;
  atEnd: boolean;
  loaded: number;
  pending: number;
  positionedImages: number;
  failedImages: number;
  lastImageBottom: number | null;
  imageCutoffReached: boolean;
  visibleLoaded: number;
  visiblePending: number;
  imageBounds: { x: number; y: number; width: number; height: number } | null;
  scrollY: number;
  viewportWidth: number;
  viewportHeight: number;
  signature: string;
}

export interface PreparationHost {
  now(): number;
  check(): void;
  wait(ms: number): Promise<void>;
  inspect(): DetailProgress;
  startAtDetail?(): void;
  captureViewport?(detail: DetailProgress): Promise<void>;
  advance(): void;
  restore(): void;
  status(message: string): void;
}

export interface PreparationResult {
  reason: "settled" | "cutoff_not_found" | "time_limit" | "step_limit" | "read_only";
  steps: number;
  detail: DetailProgress;
}

/** Observe the page until the known cutoff or the caller's optional safety budget. */
export async function prepareDetails(
  host: PreparationHost,
  options: { scroll: boolean; budgetMs?: number; maxSteps?: number; untilCutoff?: boolean } = { scroll: true },
): Promise<PreparationResult> {
  const deadline = options.untilCutoff
    ? Number.POSITIVE_INFINITY
    : host.now() + Math.min(30_000, Math.max(0, options.budgetMs ?? 12_000));
  const maxSteps = options.untilCutoff
    ? Number.POSITIVE_INFINITY
    : Math.min(24, Math.max(0, options.maxSteps ?? 24));
  let steps = 0;
  let previous = "";
  let stableSince = host.now();
  let startedAtDetail = false;
  const capturedPositions = new Set<number>();
  try {
    host.check();
    if (!options.scroll) return { reason: "read_only", steps, detail: host.inspect() };
    while (true) {
      host.check();
      let detail = host.inspect();
      if (detail.found && !startedAtDetail) {
        host.startAtDetail?.();
        startedAtDetail = true;
        detail = host.inspect();
      }
      if (detail.signature !== previous || !detail.atEnd || detail.pending > 0) {
        previous = detail.signature;
        stableSince = host.now();
      }
      if (host.captureViewport && detail.visibleLoaded > 0) {
        const position = Math.round(detail.scrollY);
        if (!capturedPositions.has(position)) {
          const waitStarted = host.now();
          while (detail.visiblePending > 0 && host.now() - waitStarted < 900) {
            await host.wait(Math.min(250, deadline - host.now()));
            detail = host.inspect();
          }
          if (detail.visibleLoaded > 0) {
            await host.captureViewport(detail);
            capturedPositions.add(position);
          }
        }
      }
      // Give late scripts a full second at the end even when every known image loaded.
      if (detail.found && detail.atEnd && !detail.pending && host.now() - stableSince >= 1000) {
        if (detail.positionedImages > 0 && detail.imageCutoffReached) {
          return { reason: "settled", steps, detail };
        }
        if (detail.positionedImages === 0) {
          return { reason: "cutoff_not_found", steps, detail };
        }
      }
      if (host.now() >= deadline) return { reason: "time_limit", steps, detail };
      const imagePosition = detail.lastImageBottom === null
        ? "未识别末图位置"
        : detail.imageCutoffReached
          ? "已到达当前最末图片位置"
          : `末图底边约 ${Math.round(detail.lastImageBottom)} px`;
      host.status(`正在读取详情区：${detail.loaded} 张图片已加载，${detail.pending} 张待加载；${imagePosition}…`);
      const targetReached = detail.atEnd && (detail.positionedImages === 0 || detail.imageCutoffReached);
      if (!targetReached) {
        if (steps >= maxSteps) return { reason: "step_limit", steps, detail };
        host.advance();
        steps += 1;
      }
      await host.wait(Math.min(300, deadline - host.now()));
    }
  } finally {
    // The browser host only restores when still on the same visible, untouched page.
    host.restore();
  }
}

export function addPreparationCoverage(snapshot: Snapshot, result: PreparationResult): Snapshot {
  const { detail, reason, steps } = result;
  const conclusion = reason === "settled"
    ? "已到达已识别详情区域末端和最后一张已定位图片的底部，图片清单在短暂观察期内稳定；不保证页面完整。"
    : reason === "cutoff_not_found"
      ? "已到达识别区域末端，但没有找到可定位的详情图片，不能确认图片截止位置。"
    : reason === "read_only"
      ? "本次仅读取当前已加载内容，没有滚动加载详情。"
      : reason === "step_limit"
        ? "已达到滚动次数上限，后续详情可能尚未加载。"
        : "已达到等待期限，后续详情可能尚未加载。";
  snapshot.coverage = (snapshot.coverage ?? []).filter(entry => entry.field_path !== "page.detail_preparation");
  snapshot.coverage.push({
    field_path: "page.detail_preparation", status: "ambiguous", evidence_ids: [],
    note: `${conclusion} ${detail.found ? "已识别详情区域" : "未识别详情区域"}；滚动 ${steps} 次；发现 ${detail.positionedImages} 张有位置的图片，其中 ${detail.loaded} 张已加载、${detail.pending} 张待加载、${detail.failedImages} 张加载失败；${detail.lastImageBottom === null ? "末图位置未知" : `末图底边约 ${Math.round(detail.lastImageBottom)} px`}。图片文字识别单独处理。`,
  });
  // Even "settled" only describes a finite observation, not total coverage.
  if (snapshot.status === "complete") snapshot.status = "partial";
  return snapshot;
}

export function captureSummary(snapshot: Snapshot): string {
  const fields = snapshot.fields ?? [];
  const missing: string[] = [];
  if (!fields.some(field => field.field_path === "product.brand" && field.status === "observed")) missing.push("品牌");
  if (snapshot.selected_variant.status !== "observed") missing.push("选中规格");
  if (!snapshot.prices?.some(price => price.status === "observed" && price.amount != null)) missing.push("明确价格");
  const specs = fields.filter(field => field.field_path.startsWith("specs.") && field.status === "observed").length;
  if (!specs) missing.push("参数");
  const images = snapshot.images?.filter(image => image.loaded && image.status === "observed").length ?? 0;
  const loading = snapshot.coverage?.find(entry => entry.field_path === "page.detail_preparation")?.note;
  return `${specs} 项参数 · ${images} 张图片来源。${missing.length ? `待核对：${missing.join("、")}。` : "规格与价格条件仍需核对。"}${loading ?? "未执行详情滚动加载，不代表完整读取；图片文字识别另行处理。"}`;
}
