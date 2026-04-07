// @vitest-environment jsdom

import React from "react";
import { Form } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WizardProvider } from "../app/contexts/WizardContext";
import { WizardScreen } from "./WizardScreen";

const dropContainsDirectoryMock = vi.fn(() => false);
const extractDroppedVideoFilesMock = vi.fn(async () => []);

vi.mock("../uploadInputHelpers", () => ({
  dropContainsDirectory: (...args: any[]) => dropContainsDirectoryMock(...args),
  extractDroppedVideoFiles: (...args: any[]) => extractDroppedVideoFilesMock(...args),
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<any>("antd");
  const triggerUpload = (customRequest: any, fileName: string) => {
    customRequest?.({
      file: new File(["demo"], fileName, { type: "video/mp4" }),
      onSuccess: vi.fn(),
      onError: vi.fn(),
    });
  };
  const Upload = ((props: any) => (
    <div data-testid="mock-upload" onClick={() => triggerUpload(props.customRequest, "picked.mp4")}>
      {props.children}
    </div>
  )) as any;
  Upload.Dragger = (props: any) => (
    <div
      role="button"
      aria-label="拖拽上传"
      onClick={() => triggerUpload(props.customRequest, "dragged.mp4")}
      onDrop={(event) => props.onDrop?.(event)}
    >
      {props.children}
    </div>
  );
  const Slider = ({ value, onChange, min = 0, max = 100 }: any) => (
    <input
      role="slider"
      type="range"
      min={min}
      max={max}
      value={value}
      onChange={(e) => onChange?.(Number(e.target.value))}
    />
  );
  return {
    ...actual,
    Upload,
    Slider,
    message: {
      warning: vi.fn(),
      error: vi.fn(),
    },
  };
});

function buildBaseContext() {
  return {
    wizardStep: 0,
    setWizardStep: vi.fn(),
    mode: "lite",
    wizardUploading: false,
    handleAddUpload: vi.fn(),
    wizardTasks: [],
    removeTask: vi.fn(),
    moveTask: vi.fn(),
    setRoute: vi.fn(),
    reviewEnabled: false,
    setReviewEnabled: vi.fn(),
    batchName: "批次-001",
    setBatchName: vi.fn(),
    outputDir: "/tmp/out",
    chooseOutputDir: vi.fn(),
    openPath: vi.fn(),
    regionPickerRect: { x: 0, y: 0, w: 0.5, h: 0.1 },
    setRegionRectSafe: vi.fn(),
    regionPickerSampleFontSize: 24,
    setFinalSubtitleFontSize: vi.fn(),
    regionPickerFrameRef: { current: null },
    regionPickerVideoPath: "",
    regionPickerVideoRef: { current: null },
    setRegionPickerVideoReady: vi.fn(),
    setRegionPickerVideoError: vi.fn(),
    setRegionPickerVideoInfo: vi.fn(),
    regionPickerVideoBox: { x: 0, y: 0, w: 1280, h: 720 },
    regionPickerSampleText: "sample",
    regionPickerVideoScale: 1,
    saveSubtitleSettings: vi.fn(),
    applySavedSubtitleSettings: vi.fn(),
    createBatchAndGo: vi.fn(),
  };
}

function renderWizard(overrides: Record<string, any> = {}) {
  const ctx = { ...buildBaseContext(), ...overrides };
  let exposedForm: any;

  function Wrapper() {
    const [form] = Form.useForm();
    exposedForm = form;
    return (
      <WizardProvider value={{ ...ctx, form }}>
        <WizardScreen />
      </WizardProvider>
    );
  }

  const rendered = render(<Wrapper />);
  if (overrides.formValues) {
    exposedForm?.setFieldsValue(overrides.formValues);
  }
  return { ...rendered, ctx, form: exposedForm };
}

function getButtonByText(label: string): HTMLButtonElement {
  const button = screen
    .getAllByRole("button")
    .find((el) => el.textContent?.replace(/\s+/g, "") === label.replace(/\s+/g, ""));
  if (!button) throw new Error(`button not found: ${label}`);
  return button as HTMLButtonElement;
}

describe("WizardScreen", () => {
  beforeEach(() => {
    cleanup();
    dropContainsDirectoryMock.mockReset();
    dropContainsDirectoryMock.mockReturnValue(false);
    extractDroppedVideoFilesMock.mockReset();
    extractDroppedVideoFilesMock.mockResolvedValue([]);
  });

  it("supports task list reordering and removal on upload step", async () => {
    const user = userEvent.setup();
    const { ctx } = renderWizard({
      wizardTasks: [{ inputName: "a.mp4" }, { inputName: "b.mp4" }],
    });

    await user.click(getButtonByText("下移"));
    await user.click(getButtonByText("移除"));

    expect(ctx.moveTask).toHaveBeenCalledWith(0, 1);
    expect(ctx.removeTask).toHaveBeenCalledWith(0);
  });

  it("triggers upload from dragger entrypoint", async () => {
    const user = userEvent.setup();
    const { ctx } = renderWizard();

    await user.click(screen.getByRole("button", { name: "拖拽上传" }));

    expect(ctx.handleAddUpload).toHaveBeenCalledTimes(1);
    expect(ctx.handleAddUpload.mock.calls[0][0].file.name).toBe("dragged.mp4");
  });

  it("expands dropped folders into video files", async () => {
    const { ctx } = renderWizard();
    const droppedA = new File(["a"], "a.mp4", { type: "video/mp4" });
    const droppedB = new File(["b"], "b.mov", { type: "video/quicktime" });
    dropContainsDirectoryMock.mockReturnValue(true);
    extractDroppedVideoFilesMock.mockResolvedValue([droppedA, droppedB]);

    fireEvent.drop(screen.getByRole("button", { name: "拖拽上传" }), {
      dataTransfer: { items: [] },
    });

    expect(extractDroppedVideoFilesMock).toHaveBeenCalled();
    await waitFor(() => expect(ctx.handleAddUpload).toHaveBeenCalledTimes(2));
    expect(ctx.handleAddUpload.mock.calls[0][0].file).toBe(droppedA);
    expect(ctx.handleAddUpload.mock.calls[1][0].file).toBe(droppedB);
  });

  it("does not show rules center description on delivery settings", () => {
    renderWizard({ wizardStep: 1 });

    expect(screen.queryByText("轻量模式已接入规则中心")).not.toBeInTheDocument();
    expect(screen.queryByText("当前模式会自动使用规则中心")).not.toBeInTheDocument();
  });

  it("advances from upload step when tasks are present", async () => {
    const user = userEvent.setup();
    const { ctx } = renderWizard({
      wizardTasks: [{ inputName: "demo.mp4" }],
    });

    const nextButtons = screen.getAllByRole("button", { name: "下一步" });
    await user.click(nextButtons[nextButtons.length - 1]);

    expect(ctx.setWizardStep).toHaveBeenCalledWith(1);
  });

  it("handles delivery settings actions and proceeds to confirmation", async () => {
    const user = userEvent.setup();
    const { ctx } = renderWizard({ wizardStep: 1, reviewEnabled: false, regionPickerVideoPath: "file:///demo.mp4" });

    await user.click(screen.getAllByRole("switch")[0]);
    await user.click(getButtonByText("选择文件夹"));
    await user.click(getButtonByText("打开"));
    await user.click(getButtonByText("下一步"));

    expect(ctx.setReviewEnabled).toHaveBeenCalledWith(true);
    expect(ctx.chooseOutputDir).toHaveBeenCalled();
    expect(ctx.openPath).toHaveBeenCalledWith("/tmp/out");
    expect(ctx.saveSubtitleSettings).toHaveBeenCalledWith({ silent: true });
    expect(ctx.applySavedSubtitleSettings).toHaveBeenCalled();
    expect(ctx.setWizardStep).toHaveBeenCalledWith(2);
  });

  it("edits batch name on delivery step", () => {
    const { ctx } = renderWizard({ wizardStep: 1, batchName: "批次-001" });

    fireEvent.change(screen.getByDisplayValue("批次-001"), { target: { value: "批次-20260401" } });

    expect(ctx.setBatchName).toHaveBeenCalled();
    expect(ctx.setBatchName.mock.calls.at(-1)?.[0]).toBe("批次-20260401");
  });

  it("updates hard subtitle controls and preview callbacks", async () => {
    const user = userEvent.setup();
    const { ctx, form } = renderWizard({
      wizardStep: 1,
      regionPickerVideoPath: "file:///demo.mp4",
      formValues: { erase_subtitle_method: "auto" },
    });

    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByText("柔化覆盖"));

    expect(form.getFieldValue("erase_subtitle_method")).toBe("blur");

    const sliders = screen.getAllByRole("slider");
    expect(sliders).toHaveLength(4);

    fireEvent.change(sliders[0], { target: { value: "0.2" } });
    fireEvent.change(sliders[1], { target: { value: "32" } });
    fireEvent.change(sliders[2], { target: { value: "0.8" } });
    fireEvent.change(sliders[3], { target: { value: "0.3" } });

    expect(ctx.setRegionRectSafe).toHaveBeenCalledWith({ y: 0.2 });
    expect(ctx.setFinalSubtitleFontSize).toHaveBeenCalledWith(32);
    expect(ctx.setRegionRectSafe).toHaveBeenCalledWith({ w: 0.8 });
    expect(ctx.setRegionRectSafe).toHaveBeenCalledWith({ h: 0.3 });

    const video = document.querySelector("video") as HTMLVideoElement;
    Object.defineProperty(video, "duration", { value: 12.5, configurable: true });
    Object.defineProperty(video, "videoWidth", { value: 1280, configurable: true });
    Object.defineProperty(video, "videoHeight", { value: 720, configurable: true });
    fireEvent.loadedMetadata(video);
    expect(ctx.setRegionPickerVideoReady).toHaveBeenCalledWith(true);
    expect(ctx.setRegionPickerVideoError).toHaveBeenCalledWith("");
    expect(ctx.setRegionPickerVideoInfo).toHaveBeenCalled();

    Object.defineProperty(video, "error", { value: { code: 4, message: "decode error" }, configurable: true });
    fireEvent.error(video);
    expect(ctx.setRegionPickerVideoReady).toHaveBeenCalledWith(false);
    expect(ctx.setRegionPickerVideoError).toHaveBeenCalled();
  });

  it("shows confirmation summary for the current batch", () => {
    renderWizard({
      wizardStep: 2,
      mode: "quality",
      batchName: "批次-总结",
      wizardTasks: [{ inputName: "a.mp4" }, { inputName: "b.mp4" }],
      outputDir: "",
    });

    expect(screen.getByText("批次-总结")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("未选择（可继续，稍后手动下载交付物）")).toBeInTheDocument();
    expect(screen.getByText("质量")).toBeInTheDocument();
  });

  it("starts processing from confirmation step", async () => {
    const user = userEvent.setup();
    const { ctx } = renderWizard({
      wizardStep: 2,
      wizardTasks: [{ inputName: "demo.mp4" }],
    });

    await user.click(getButtonByText("开始处理"));

    expect(ctx.createBatchAndGo).toHaveBeenCalledWith(true);
  });
});
