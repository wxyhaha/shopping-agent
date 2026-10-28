import type { components } from "./schema";

type Task = components["schemas"]["TaskRead"];
type TaskSummary = components["schemas"]["TaskSummary"];
type CreateTaskRequest = components["schemas"]["CreateTaskRequest"];
type ItemInput = components["schemas"]["ItemInput"];
type ProductItem = components["schemas"]["ItemRead"];
type UpdateTaskTitleRequest = components["schemas"]["UpdateTaskTitleRequest"];
type UpdateItemTitleRequest = components["schemas"]["UpdateItemTitleRequest"];
type CaptureSnapshot = components["schemas"]["CaptureSnapshotRead"];
type CaptureSnapshotSubmit = components["schemas"]["CaptureSnapshotSubmit"];
type ResearchRun = components["schemas"]["ResearchRunRead"];
type BrowserPageConnection = components["schemas"]["BrowserPageConnection"];

const API_ROOT = "/api/v1";

type BrowserLogin = components["schemas"]["BrowserLoginRead"];
function loginPath(taskId: string, itemId: string): string {
  return `/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(itemId)}/browser-login`;
}
export function getBrowserLogin(taskId: string, itemId: string): Promise<BrowserLogin | null> {
  return request(loginPath(taskId, itemId));
}
export function openBrowserLogin(taskId: string, itemId: string): Promise<BrowserLogin> {
  return request(loginPath(taskId, itemId), { method: "POST" });
}
export function controlBrowserLogin(taskId: string, itemId: string, id: string, action: "save" | "cancel"): Promise<BrowserLogin> {
  return request(`${loginPath(taskId, itemId)}/${encodeURIComponent(id)}/${action}`, { method: "POST" });
}

export function getCollectionStatus(): Promise<components["schemas"]["CollectionAvailability"]> {
  return request("/collection/status");
}

export function getBrowserConnections(taskId: string): Promise<BrowserPageConnection[]> {
  return request(`/browser-captures/status/${encodeURIComponent(taskId)}`);
}

export function listResearch(taskId: string): Promise<ResearchRun[]> {
  return request(`/tasks/${encodeURIComponent(taskId)}/research?limit=5`);
}

export function startResearch(taskId: string, goal: string, key: string): Promise<ResearchRun> {
  return request(`/tasks/${encodeURIComponent(taskId)}/research`, {
    method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify({ goal }),
  });
}

export function resumeResearch(taskId: string, runId: string, answer?: string): Promise<ResearchRun> {
  return request(`/tasks/${encodeURIComponent(taskId)}/research/${encodeURIComponent(runId)}/resume`, {
    method: "POST", body: JSON.stringify({ answer: answer || null }),
  });
}

export function cancelResearch(taskId: string, runId: string): Promise<ResearchRun> {
  return request(`/tasks/${encodeURIComponent(taskId)}/research/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
  });
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
      cache: "no-store",
    });
  } catch {
    throw new ApiError("无法连接本地服务。请确认后台正在运行后重试。", 0);
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : "请求未能完成，请稍后重试。";
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function createIdempotencyKey(): string {
  return crypto.randomUUID();
}

export async function getReadiness(): Promise<{ status: string }> {
  try {
    const response = await fetch("/health/ready", { cache: "no-store", headers: { Accept: "application/json" } });
    if (!response.ok) throw new ApiError("本地服务尚未就绪。", response.status);
    return (await response.json()) as { status: string };
  } catch (cause) {
    if (cause instanceof ApiError) throw cause;
    throw new ApiError("无法连接本地服务。", 0);
  }
}

export function listTasks(): Promise<TaskSummary[]> {
  return request<TaskSummary[]>("/tasks?limit=100");
}

export function getTask(taskId: string): Promise<Task> {
  return request<Task>(`/tasks/${encodeURIComponent(taskId)}`);
}

export function removeTask(taskId: string): Promise<void> {
  return request<void>(`/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
}

export function createTask(payload: CreateTaskRequest, idempotencyKey: string): Promise<Task> {
  return request<Task>("/tasks", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  });
}

export function addItem(
  taskId: string,
  payload: ItemInput,
  idempotencyKey: string,
): Promise<ProductItem> {
  return request<ProductItem>(`/tasks/${encodeURIComponent(taskId)}/items`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  });
}

export function updateTaskTitle(taskId: string, payload: UpdateTaskTitleRequest): Promise<Task> {
  return request<Task>(`/tasks/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function updateItemTitle(
  taskId: string,
  itemId: string,
  payload: UpdateItemTitleRequest,
): Promise<ProductItem> {
  return request<ProductItem>(
    `/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(itemId)}`,
    { method: "PATCH", body: JSON.stringify(payload) },
  );
}

export function removeItem(taskId: string, itemId: string): Promise<void> {
  return request<void>(
    `/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(itemId)}`,
    { method: "DELETE" },
  );
}

export function createCaptureSnapshot(
  taskId: string,
  itemId: string,
  payload: CaptureSnapshotSubmit,
  idempotencyKey: string,
): Promise<CaptureSnapshot> {
  return request<CaptureSnapshot>(
    `/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(itemId)}/captures`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(payload),
    },
  );
}

export function listCaptureSnapshots(
  taskId: string,
  itemId: string,
  limit = 5,
): Promise<CaptureSnapshot[]> {
  const boundedLimit = Math.max(1, Math.min(20, Math.trunc(limit)));
  return request<CaptureSnapshot[]>(
    `/tasks/${encodeURIComponent(taskId)}/items/${encodeURIComponent(itemId)}/captures?limit=${boundedLimit}`,
  );
}
