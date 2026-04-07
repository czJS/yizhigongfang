// @vitest-environment jsdom

import React from "react";
import { renderHook, act, waitFor } from "@testing-library/react";
import { Modal, message } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BatchModel } from "../batchTypes";
import { useWizardBatchCreation } from "./useWizardBatchCreation";

const uploadFileMock = vi.fn();

vi.mock("../api", () => ({
  uploadFile: (...args: any[]) => uploadFileMock(...args),
}));

function buildHook(overrides: Record<string, any> = {}) {
  const setActiveBatchId = vi.fn();
  const setRoute = vi.fn();
  const startQueue = vi.fn();

  const hook = renderHook(() => {
    const [wizardTasks, setWizardTasks] = React.useState<any[]>(overrides.initialWizardTasks || []);
    const [wizardUploading, setWizardUploading] = React.useState(false);
    const [batches, setBatches] = React.useState<BatchModel[]>([]);
    const [wizardStep, setWizardStep] = React.useState(1);
    const [batchName, setBatchName] = React.useState("批次-001");
    const savedSubtitleSettingsRef = React.useRef<any>(overrides.savedSubtitleSettingsRef || null);
    const savedSubtitleSettings = overrides.savedSubtitleSettings || null;

    const result = useWizardBatchCreation({
      wizardTasks,
      setWizardTasks,
      setWizardUploading,
      mode: "lite",
      outputDir: overrides.outputDir ?? "/tmp/out",
      form: {
        getFieldsValue: vi.fn(() => overrides.formValues || {}),
      } as any,
      reviewEnabled: overrides.reviewEnabled ?? true,
      currentBatchRulesetOverride: vi.fn(() => overrides.rulesetOverride || null),
      savedSubtitleSettingsRef,
      savedSubtitleSettings,
      regionPickerRect: overrides.regionPickerRect || { x: 0.1, y: 0.7, w: 0.8, h: 0.2 },
      filterLiteFastParams: overrides.filterLiteFastParams || ((p: Record<string, any>) => p),
      preset: overrides.preset || "normal",
      batchName,
      setBatches,
      setActiveBatchId,
      setWizardStep,
      setBatchName,
      setRoute,
      startQueue,
    });

    return {
      ...result,
      wizardTasks,
      wizardUploading,
      batches,
      wizardStep,
      batchName,
    };
  });

  return { ...hook, setActiveBatchId, setRoute, startQueue };
}

describe("useWizardBatchCreation", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
    uploadFileMock.mockReset();
  });

  it("uploads files and keeps local paths for batch tasks", async () => {
    uploadFileMock.mockResolvedValue("/tmp/uploads/demo.mp4");
    vi.spyOn(message, "success").mockImplementation(() => undefined as any);
    const { result } = buildHook();
    const file = new File(["demo"], "demo.mp4", { type: "video/mp4" });
    Object.defineProperty(file, "path", { value: "/local/demo.mp4" });
    Object.defineProperty(file, "__ygfDisplayName", { value: "clips/demo.mp4", configurable: true });
    const onSuccess = vi.fn();

    await act(async () => {
      await result.current.handleAddUpload({
        file,
        onSuccess,
        onError: vi.fn(),
      } as any);
    });

    await waitFor(() => expect(result.current.wizardTasks).toHaveLength(1));
    expect(uploadFileMock).toHaveBeenCalledWith(file);
    expect(result.current.wizardTasks[0]).toMatchObject({
      inputName: "clips/demo.mp4",
      inputPath: "/tmp/uploads/demo.mp4",
      localPath: "/local/demo.mp4",
    });
    expect(onSuccess).toHaveBeenCalled();
    expect(result.current.wizardUploading).toBe(false);
  });

  it("limits uploads to ten files and warns for overflow", async () => {
    uploadFileMock.mockImplementation(async (file: File) => `/tmp/uploads/${file.name}`);
    const warningSpy = vi.spyOn(message, "warning").mockImplementation(() => undefined as any);
    const { result } = buildHook();

    for (let i = 0; i < 11; i++) {
      const file = new File([`f${i}`], `demo-${i}.mp4`, { type: "video/mp4" });
      await act(async () => {
        await result.current.handleAddUpload({
          file,
          onSuccess: vi.fn(),
          onError: vi.fn(),
        } as any);
      });
    }

    await waitFor(() => expect(result.current.wizardTasks).toHaveLength(10));
    expect(result.current.wizardTasks[9].inputName).toBe("demo-9.mp4");
    expect(warningSpy).toHaveBeenCalled();
  });

  it("warns when output dir is missing and writes hard subtitle params into lite batch", async () => {
    vi.useFakeTimers();
    const warningSpy = vi.spyOn(message, "warning").mockImplementation(() => undefined as any);
    const confirmSpy = vi.spyOn(Modal, "confirm").mockImplementation(() => undefined as any);
    const filterLiteFastParams = vi.fn((p: Record<string, any>) => p);
    const { result, setRoute, startQueue, setActiveBatchId } = buildHook({
      outputDir: "",
      initialWizardTasks: [{ inputName: "demo.mp4", inputPath: "/tmp/uploads/demo.mp4", localPath: "/local/demo.mp4", overrides: {} }],
      formValues: { whispercpp_threads: 4, erase_subtitle_method: "blur", erase_subtitle_blur_radius: 9 },
      savedSubtitleSettingsRef: {
        source: "has",
        rect: { x: 0.2, y: 0.6, w: 0.5, h: 0.18 },
        values: { erase_subtitle_method: "blur", erase_subtitle_blur_radius: 9 },
        fontSize: 28,
      },
      filterLiteFastParams,
    });

    await act(async () => {
      await result.current.createBatchAndGo(true);
    });

    act(() => {
      vi.runAllTimers();
    });

    expect(warningSpy).toHaveBeenCalled();
    expect(filterLiteFastParams).toHaveBeenCalled();
    expect(result.current.batches).toHaveLength(1);
    expect(result.current.batches[0].params).toMatchObject({
      review_enabled: true,
      erase_subtitle_enable: true,
      erase_subtitle_method: "blur",
      erase_subtitle_coord_mode: "ratio",
      erase_subtitle_x: 0.2,
      erase_subtitle_y: 0.6,
      erase_subtitle_w: 0.5,
      erase_subtitle_h: 0.18,
      erase_subtitle_blur_radius: 9,
      sub_place_enable: true,
      sub_place_coord_mode: "ratio",
      sub_place_x: 0.2,
      sub_place_y: 0.6,
      sub_place_w: 0.5,
      sub_place_h: 0.18,
      sub_font_size: 28,
      whispercpp_threads: 4,
    });
    expect(setActiveBatchId).toHaveBeenCalledWith(result.current.batches[0].id);
    expect(confirmSpy).toHaveBeenCalled();
    expect(setRoute).toHaveBeenCalledWith("wizard");
    expect(startQueue).toHaveBeenCalledWith(result.current.batches[0].id, { navigate: false });
  });
});
