import axios from "axios";
import { create } from "zustand";

export type TaskState = "running" | "completed" | "failed" | "cancelled";

export interface TaskStatus {
  id: string;
  video: string;
  state: TaskState;
  stage?: number;
  stage_name?: string;
  progress?: number;
  message?: string;
  started_at?: number;
  ended_at?: number | null;
  work_dir?: string;
}

export interface Artifact {
  name: string;
  path: string;
  size: number;
}

export interface ErrorLog {
  time: number;
  message: string;
}

interface HardwareInfo {
  cpu_cores: number;
  memory_gb: number;
  gpu_name?: string | null;
  gpu_vram_gb?: number | null;
  tier: string;
  presets: Record<string, any>;
}

interface StoreState {
  apiBase: string;
  hardware?: HardwareInfo;
  presets: Record<string, any>;
  currentTask?: TaskStatus;
  log: string;
  logOffset: number;
  artifacts: Artifact[];
  loading: boolean;
  errors: ErrorLog[];
  addError: (msg: string) => void;
  setLoading: (v: boolean) => void;
  fetchHardware: () => Promise<void>;
  startTask: (video: string, params: Record<string, any>, preset?: string) => Promise<string>;
  refreshStatus: (taskId: string) => Promise<void>;
  refreshLog: (taskId: string) => Promise<void>;
  refreshArtifacts: (taskId: string) => Promise<void>;
  cancelTask: (taskId: string) => Promise<void>;
}

const apiBase = window.bridge?.apiBase || "http://127.0.0.1:5175";

export const useAppStore = create<StoreState>((set, get) => ({
  apiBase,
  presets: {},
  log: "",
  logOffset: 0,
  artifacts: [],
  loading: false,
  errors: [],
  addError: (message: string) =>
    set((state) => ({
      errors: [{ time: Date.now(), message }, ...state.errors].slice(0, 50),
    })),
  setLoading: (v) => set({ loading: v }),
  async fetchHardware() {
    const base = get().apiBase;
    const res = await axios.get<HardwareInfo>(`${base}/api/hardware`);
    set({ hardware: res.data });
  },
  async startTask(video, params, preset) {
    const base = get().apiBase;
    try {
      const res = await axios.post<{ task_id: string }>(`${base}/api/tasks/start`, {
        video,
        params,
        preset,
      });
      set({ currentTask: { id: res.data.task_id, video, state: "running" }, log: "", logOffset: 0 });
      return res.data.task_id;
    } catch (err: any) {
      const addError = get().addError;
      if (axios.isAxiosError(err)) {
        const status = err.response?.status;
        if (!err.response) {
          addError(`无法连接后端 ${base}，请确认已在项目根目录运行: python -m backend.app`);
          throw err;
        }
        if (status === 404) {
          addError(`后端接口 404: ${base}/api/tasks/start，请确认后端已启动且端口为 5175`);
          throw err;
        }
      }
      addError(err?.message || "启动任务失败");
      throw err;
    }
  },
  async refreshStatus(taskId) {
    const base = get().apiBase;
    const res = await axios.get<TaskStatus>(`${base}/api/tasks/${taskId}/status`);
    set({ currentTask: res.data });
  },
  async refreshLog(taskId) {
    const base = get().apiBase;
    const offset = get().logOffset;
    const res = await axios.get<{ content: string; next_offset: number }>(
      `${base}/api/tasks/${taskId}/log`,
      { params: { offset } },
    );
    set((state) => ({
      log: state.log + res.data.content,
      logOffset: res.data.next_offset,
    }));
  },
  async refreshArtifacts(taskId) {
    const base = get().apiBase;
    const res = await axios.get<{ files: Artifact[] }>(`${base}/api/tasks/${taskId}/artifacts`);
    set({ artifacts: res.data.files });
  },
  async cancelTask(taskId) {
    const base = get().apiBase;
    await axios.post(`${base}/api/tasks/${taskId}/cancel`);
    set((state) => {
      if (state.currentTask && state.currentTask.id === taskId) {
        return { currentTask: { ...state.currentTask, state: "cancelled" as TaskState } };
      }
      return state;
    });
  },
}));


