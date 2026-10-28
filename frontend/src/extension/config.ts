export const BACKEND_STORAGE_KEY = "shoppingAgentBackendUrl";
export const LAST_TASK_STORAGE_KEY = "shoppingAgentLastTaskId";
export const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";

export function validateBackendUrl(value: string): string {
  const parsed = new URL(value.trim());
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname);
  if (!loopback || !["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("商品信息只会发送到本机服务地址，例如 http://127.0.0.1:8000。");
  }
  if (parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) {
    throw new Error("只填写服务根地址，例如 http://127.0.0.1:8000。");
  }
  return parsed.origin;
}
