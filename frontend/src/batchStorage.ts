import type { BatchModel } from "./batchTypes";

const KEY = "dubbing_gui_batches_v1";
const LEGACY_KEYS = ["dubbing_gui_batches", "dubbing_gui_batches_v0"];
const ACTIVE_KEY = "dubbing_gui_active_batch_v1";
const LEGACY_ACTIVE_KEYS = ["dubbing_gui_active_batch", "dubbing_gui_active_batch_v0"];
const PREFS_KEY = "dubbing_gui_prefs_v1";

export interface UiPrefs {
  defaultMode?: "lite" | "quality" | "online";
  defaultPreset?: string;
  // Persisted boolean toggles for advanced settings (and a few basic switches).
  // Keys match backend config.defaults boolean fields.
  defaultToggles?: Record<string, boolean>;
  // Persisted scalar params for advanced settings.
  // Keys match backend config.defaults non-boolean scalar fields we choose to expose.
  defaultParams?: Record<string, number | string>;
  // Runtime switch to show/hide developer-only UI (for testing).
  devToolsEnabled?: boolean;
}

export function loadBatches(): BatchModel[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const data = JSON.parse(raw);
      return Array.isArray(data) ? (data as BatchModel[]) : [];
    }
    for (const k of LEGACY_KEYS) {
      const legacy = localStorage.getItem(k);
      if (!legacy) continue;
      const data = JSON.parse(legacy);
      if (Array.isArray(data)) {
        localStorage.setItem(KEY, JSON.stringify(data));
        return data as BatchModel[];
      }
    }
    return [];
  } catch {
    return [];
  }
}

export function saveBatches(list: BatchModel[]) {
  localStorage.setItem(KEY, JSON.stringify(list));
}

export function loadActiveBatchId(): string {
  const current = localStorage.getItem(ACTIVE_KEY);
  if (current) return current;
  for (const k of LEGACY_ACTIVE_KEYS) {
    const legacy = localStorage.getItem(k);
    if (legacy) {
      localStorage.setItem(ACTIVE_KEY, legacy);
      return legacy;
    }
  }
  return "";
}

export function saveActiveBatchId(id: string) {
  localStorage.setItem(ACTIVE_KEY, id || "");
}

export function loadUiPrefs(): UiPrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return {};
    const data = JSON.parse(raw);
    return (data && typeof data === "object") ? (data as UiPrefs) : {};
  } catch {
    return {};
  }
}

export function saveUiPrefs(prefs: UiPrefs) {
  localStorage.setItem(PREFS_KEY, JSON.stringify(prefs || {}));
}


