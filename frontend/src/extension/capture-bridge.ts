import type { components } from "../api/schema";

type Capture = components["schemas"]["CaptureSnapshotSubmit"];
type Lease = components["schemas"]["BrowserCaptureLease"];

export interface CaptureBridgeHost {
  pageUrl(): string;
  send(message: Record<string, unknown>): Promise<unknown>;
  capture(pageUrl: string): Capture | Promise<Capture>;
  status(message: string): void;
}

function response(value: unknown): { ok: boolean; job?: Lease | null; error?: string } {
  if (!value || typeof value !== "object") throw new Error("扩展没有返回有效响应。");
  return value as { ok: boolean; job?: Lease | null; error?: string };
}

export async function pollCaptureBridge(host: CaptureBridgeHost): Promise<"idle" | "captured" | "failed"> {
  const pageUrl = host.pageUrl();
  const claim = response(await host.send({ type: "shopping-agent:claim-capture", url: pageUrl }));
  if (!claim.ok) throw new Error(claim.error || "无法读取采集请求。");
  const job = claim.job;
  if (!job) return "idle";
  const shared = { jobId: job.id, leaseToken: job.lease_token, url: pageUrl };
  const fail = async (errorCode: "page_changed" | "capture_failed") => {
    await host.send({ type: "shopping-agent:fail-capture", ...shared, errorCode });
    host.status(errorCode === "page_changed" ? "页面已变化，此次补采集未提交。" : "读取页面失败，请在工作台查看研究状态。");
    return "failed" as const;
  };
  if (host.pageUrl() !== pageUrl) return fail("page_changed");
  host.status(`研究助手正在重新读取此商品：${job.reason}`);
  let capture: Capture;
  try {
    // The host may expand a collapsed panel first, so this is awaited.
    capture = await host.capture(pageUrl);
  } catch {
    return fail("capture_failed");
  }
  if (host.pageUrl() !== pageUrl || capture.page_url !== pageUrl) return fail("page_changed");
  const message = { type: "shopping-agent:complete-capture", ...shared, capture };
  // Replay exactly the same payload and lease on a lost response. Never re-read the DOM
  // with the same request ID: backend idempotency would then see different capture data.
  let error = "补采集结果未能保存。";
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const result = response(await host.send(message));
      if (result.ok) {
        host.status("已按研究助手请求保存新快照，助手将自动继续。");
        return "captured";
      }
      error = result.error || error;
    } catch {
      error = "补采集结果未收到确认，稍后会重试。";
    }
  }
  host.status(error);
  return "failed";
}
