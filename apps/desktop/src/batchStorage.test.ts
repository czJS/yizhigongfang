import { beforeEach, describe, expect, it } from "vitest";
import {
  loadActiveBatchId,
  loadBatches,
  loadUiPrefs,
  saveActiveBatchId,
  saveBatches,
  saveUiPrefs,
} from "./batchStorage";

type StorageMap = Record<string, string>;

function createLocalStorage(seed: StorageMap = {}) {
  const store: StorageMap = { ...seed };
  return {
    getItem(key: string) {
      return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
    },
    setItem(key: string, value: string) {
      store[key] = String(value);
    },
    removeItem(key: string) {
      delete store[key];
    },
    clear() {
      for (const key of Object.keys(store)) delete store[key];
    },
    dump() {
      return { ...store };
    },
  };
}

describe("batchStorage", () => {
  beforeEach(() => {
    const localStorage = createLocalStorage();
    Object.defineProperty(globalThis, "localStorage", {
      value: localStorage,
      configurable: true,
      writable: true,
    });
  });

  it("loads current batches key and saves back to v1", () => {
    const list = [{ id: "b1" }];
    localStorage.setItem("dubbing_gui_batches_v1", JSON.stringify(list));

    expect(loadBatches()).toEqual(list);

    saveBatches([{ id: "b2" } as any]);
    expect(JSON.parse(localStorage.getItem("dubbing_gui_batches_v1") || "[]")).toEqual([{ id: "b2" }]);
  });

  it("migrates legacy batch keys when v1 key is absent", () => {
    localStorage.setItem("dubbing_gui_batches", JSON.stringify([{ id: "legacy" }]));

    expect(loadBatches()).toEqual([{ id: "legacy" }]);
    expect(JSON.parse(localStorage.getItem("dubbing_gui_batches_v1") || "[]")).toEqual([{ id: "legacy" }]);
  });

  it("returns empty list when stored batches are invalid", () => {
    localStorage.setItem("dubbing_gui_batches_v1", "{bad json");
    expect(loadBatches()).toEqual([]);
  });

  it("loads and migrates active batch id", () => {
    localStorage.setItem("dubbing_gui_active_batch", "legacy-id");

    expect(loadActiveBatchId()).toBe("legacy-id");
    expect(localStorage.getItem("dubbing_gui_active_batch_v1")).toBe("legacy-id");

    saveActiveBatchId("new-id");
    expect(localStorage.getItem("dubbing_gui_active_batch_v1")).toBe("new-id");
  });

  it("round-trips UI prefs and tolerates bad JSON", () => {
    saveUiPrefs({
      defaultMode: "quality",
      deliveryIncludeOptionals: true,
      showTaskLogs: false,
    });

    expect(loadUiPrefs()).toEqual({
      defaultMode: "quality",
      deliveryIncludeOptionals: true,
      showTaskLogs: false,
    });

    localStorage.setItem("dubbing_gui_prefs_v1", "{broken");
    expect(loadUiPrefs()).toEqual({});
  });
});
