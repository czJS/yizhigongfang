import type { BatchModel, UiTaskState } from "../batchTypes";

export function modeLabel(m: BatchModel["mode"]): string {
  if (m === "lite") return "轻量";
  if (m === "quality") return "质量";
  if (m === "online") return "在线";
  return String(m);
}

export function batchStateLabel(s: BatchModel["state"]): string {
  if (s === "running") return "进行中";
  if (s === "queued") return "排队中";
  if (s === "paused") return "已暂停";
  if (s === "completed") return "已结束";
  if (s === "draft") return "未开始";
  return String(s);
}

export function taskStateLabel(s: UiTaskState): string {
  if (s === "pending") return "待处理";
  if (s === "running") return "处理中";
  if (s === "completed") return "已完成";
  if (s === "failed") return "失败";
  if (s === "paused") return "已暂停";
  if (s === "cancelled") return "已取消";
  return String(s);
}

