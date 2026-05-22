/** POE2 BD Agent — API 客户端 */

import type {
  BuildCard,
  BuildListItem,
  BuildListResponse,
  GenerateRequest,
} from "../types/build";

const API_BASE = "http://localhost:8000";

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  /** 生成 BD */
  generate: (req: GenerateRequest) =>
    request<BuildCard>("/api/builds/generate", {
      method: "POST",
      body: JSON.stringify(req),
    }),

  /** 获取 BD 详情 */
  getBuild: (id: string) => request<BuildCard>(`/api/builds/${id}`),

  /** 列出历史 BD */
  listBuilds: (limit = 20, offset = 0) =>
    request<BuildListResponse>(`/api/builds/?limit=${limit}&offset=${offset}`),

  /** 验证 BD 草案 */
  validate: (build: unknown) =>
    request<Record<string, unknown>>("/api/builds/validate", {
      method: "POST",
      body: JSON.stringify({ build }),
    }),

  /** 格式化 BD */
  format: (build: unknown, format: "markdown" | "json" | "summary") =>
    request<{ format: string; content: string | object }>("/api/builds/format", {
      method: "POST",
      body: JSON.stringify({ build, format }),
    }),

  /** 健康检查 */
  health: () => request<{ status: string }>("/health"),
};
