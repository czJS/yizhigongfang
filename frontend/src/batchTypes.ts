export type UiTaskState = "pending" | "running" | "paused" | "completed" | "failed" | "cancelled";

export interface BatchTask {
  index: number; // 1-based
  inputName: string; // original filename for display
  inputPath: string; // backend-accessible path (after upload)
  // Electron desktop: optional local absolute path for preview / region picking
  localPath?: string;
  taskId?: string; // backend task id
  state: UiTaskState;
  progress?: number;
  stageName?: string;
  message?: string;
  startedAt?: number;
  endedAt?: number | null;
  workDir?: string;
  failureReason?: string;
  artifacts?: { name: string; path: string; size: number }[];
  qualityPassed?: boolean;
  qualityErrors?: string[];
  qualityWarnings?: string[];
  // delivery save result
  deliveredDir?: string;
  deliveredFiles?: { label: string; filename: string }[];

  // per-task params override (takes precedence over batch params)
  paramsOverride?: Record<string, any>;
}

export interface BatchModel {
  id: string;
  name: string;
  createdAt: number;
  mode: "lite" | "quality" | "online";
  preset?: string;
  params: Record<string, any>;
  outputDir: string; // user-chosen output directory (host filesystem)
  // draft: 未开始；queued: 已加入队列等待；running: 运行中；paused: 暂停；completed: 已结束
  state: "draft" | "queued" | "running" | "paused" | "completed";
  currentTaskIndex?: number; // index in tasks array (0-based)
  tasks: BatchTask[];
}


