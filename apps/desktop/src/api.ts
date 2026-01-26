import axios from "axios";
import type { AppConfig, HardwareInfo, TaskStatus, LogResponse, Artifact, QualityReport, GlossaryDoc } from "./types";

export const apiBase = import.meta.env.VITE_API_BASE || "http://127.0.0.1:5175";

export const client = axios.create({
  baseURL: apiBase,
  timeout: 15000,
});

function extractApiError(err: any): string {
  // Axios error shape: err.response.data may contain {error, log}
  const data = err?.response?.data;
  if (typeof data === "string" && data.trim()) return data;
  if (data && typeof data === "object") {
    if (typeof data.error === "string" && data.error) return data.error;
    if (typeof data.message === "string" && data.message) return data.message;
    if (typeof data.log === "string" && data.log) return data.log;
  }
  return err?.message || "请求失败";
}

export async function getHealth(): Promise<string> {
  const { data } = await client.get("/api/health");
  return data?.status || "unknown";
}

export async function getConfig(): Promise<AppConfig> {
  const { data } = await client.get("/api/config");
  return data;
}

export async function probeVideo(path: string): Promise<{ width: number; height: number; duration_s: number | null }> {
  try {
    const { data } = await client.post("/api/video/probe", { path });
    return data;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function fetchVideoFrame(path: string, t = 0, maxWidth = 960): Promise<Blob> {
  try {
    const resp = await client.post("/api/video/frame", { path, t, max_width: maxWidth }, { responseType: "blob" });
    return resp.data as Blob;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function getHardware(): Promise<HardwareInfo> {
  const { data } = await client.get("/api/hardware");
  return data;
}

export async function uploadFile(file: File): Promise<string> {
  const fd = new FormData();
  fd.append("file", file);
  const { data } = await client.post<{ path: string }>("/api/upload", fd, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data.path;
}

export async function startTask(payload: { video: string; params: Record<string, any>; preset?: string; mode?: string }): Promise<string> {
  const { data } = await client.post<{ task_id: string }>("/api/tasks/start", payload);
  return data.task_id;
}

export async function getStatus(taskId: string): Promise<TaskStatus> {
  const { data } = await client.get<TaskStatus>(`/api/tasks/${taskId}/status`);
  return data;
}

export async function cancelTask(taskId: string): Promise<void> {
  await client.post(`/api/tasks/${taskId}/cancel`);
}

export async function getLog(taskId: string, offset = 0): Promise<LogResponse> {
  const { data } = await client.get<LogResponse>(`/api/tasks/${taskId}/log`, { params: { offset } });
  return data;
}

export async function getArtifacts(taskId: string): Promise<Artifact[]> {
  const { data } = await client.get<{ files: Artifact[] }>(`/api/tasks/${taskId}/artifacts`);
  return data.files || [];
}

export async function cleanupTaskArtifacts(
  taskId: string,
  opts?: { include_resume?: boolean; include_review?: boolean; include_diagnostics?: boolean }
): Promise<{ removed: string[]; missing: string[]; errors: string[] }> {
  try {
    const { data } = await client.post(`/api/tasks/${taskId}/cleanup`, opts || {});
    return data;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function downloadTaskFileText(taskId: string, path: string): Promise<string> {
  try {
    const resp = await client.get(`/api/tasks/${taskId}/download`, { params: { path }, responseType: "text" });
    // axios may parse JSON automatically only when responseType is json; here we want raw text
    return typeof resp.data === "string" ? resp.data : JSON.stringify(resp.data ?? "");
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function downloadTaskFileBytes(taskId: string, path: string): Promise<Uint8Array> {
  try {
    const resp = await client.get<ArrayBuffer>(`/api/tasks/${taskId}/download`, {
      params: { path },
      responseType: "arraybuffer",
    });
    return new Uint8Array(resp.data as any);
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function getQualityReport(taskId: string, opts?: { regen?: boolean }): Promise<QualityReport> {
  const { data } = await client.get<QualityReport>(`/api/tasks/${taskId}/quality_report`, {
    params: opts?.regen ? { regen: 1 } : undefined,
  });
  return data;
}

export async function resumeTask(taskId: string, payload: { resume_from: "asr" | "mt" | "tts" | "mux" }): Promise<string> {
  const { data } = await client.post<{ task_id: string }>(`/api/tasks/${taskId}/resume`, payload);
  return data.task_id;
}

export async function getTerminology(taskId: string): Promise<{ name: string; content: string }> {
  try {
    const { data } = await client.get(`/api/tasks/${taskId}/terminology`);
    return data;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function putTerminology(taskId: string, content: string): Promise<{ status: string; path: string }> {
  try {
    const { data } = await client.put(`/api/tasks/${taskId}/terminology`, { content });
    return data;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function resumeTask2(
  taskId: string,
  payload: { resume_from: "asr" | "mt" | "tts" | "mux"; params?: Record<string, any>; preset?: string }
): Promise<string> {
  try {
    const { data } = await client.post<{ task_id: string }>(`/api/tasks/${taskId}/resume`, payload);
    return data.task_id;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function getGlossary(): Promise<GlossaryDoc> {
  const { data } = await client.get<GlossaryDoc>("/api/glossary");
  return data;
}

export async function putGlossary(doc: Partial<GlossaryDoc>): Promise<GlossaryDoc> {
  const { data } = await client.put<GlossaryDoc>("/api/glossary", doc);
  return data;
}

export async function uploadGlossaryFile(file: File): Promise<GlossaryDoc> {
  const fd = new FormData();
  fd.append("file", file);
  const { data } = await client.post<GlossaryDoc>("/api/glossary/upload", fd, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function getEngSrt(taskId: string, which: "base" | "review" = "base"): Promise<{ name: string; content: string }> {
  const { data } = await client.get(`/api/tasks/${taskId}/review/eng_srt`, { params: { which } });
  return data;
}

export async function putEngReviewSrt(taskId: string, content: string): Promise<{ status: string; path: string }> {
  const { data } = await client.put(`/api/tasks/${taskId}/review/eng_srt`, { content });
  return data;
}

export async function uploadEngReviewSrt(taskId: string, file: File): Promise<{ status: string; path: string }> {
  const fd = new FormData();
  fd.append("file", file);
  const { data } = await client.post(`/api/tasks/${taskId}/review/upload_eng_srt`, fd, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function getEngSrtDiff(taskId: string, lang: "eng" | "chs" = "eng"): Promise<string> {
  try {
    const { data } = await client.get(`/api/tasks/${taskId}/review/diff`, { params: { lang } });
    return data.diff || "";
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function applyReview(
  taskId: string,
  payload: { action: "mux" | "embed" | "mux_embed"; use?: "review" | "base"; params?: Record<string, any> },
): Promise<void> {
  try {
    await client.post(`/api/tasks/${taskId}/review/apply`, payload);
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function getChsSrt(taskId: string): Promise<{ name: string; content: string }> {
  try {
    const { data } = await client.get(`/api/tasks/${taskId}/review/chs_srt`, { params: { which: "base" } });
    return data;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function getChsSrt2(taskId: string, which: "base" | "review" = "base"): Promise<{ name: string; content: string }> {
  try {
    const { data } = await client.get(`/api/tasks/${taskId}/review/chs_srt`, { params: { which } });
    return data;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function putChsReviewSrt(taskId: string, content: string): Promise<{ status: string; path: string }> {
  try {
    const { data } = await client.put(`/api/tasks/${taskId}/review/chs_srt`, { content });
    return data;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function uploadChsReviewSrt(taskId: string, file: File): Promise<{ status: string; path: string }> {
  try {
    const fd = new FormData();
    fd.append("file", file);
    const { data } = await client.post(`/api/tasks/${taskId}/review/upload_chs_srt`, fd, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return data;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

export async function runReview(taskId: string, lang: "chs" | "eng"): Promise<{ task_id: string; resume_from: string; lang: string }> {
  try {
    const { data } = await client.post(`/api/tasks/${taskId}/review/run`, { lang });
    return data;
  } catch (err: any) {
    throw new Error(extractApiError(err));
  }
}

