// @vitest-environment jsdom

import React from "react";
import { Form, message } from "antd";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { AdvancedProvider } from "../app/contexts/AdvancedContext";
import { AdvancedScreen } from "./AdvancedSettingsScreen";

function buildContext(overrides: Record<string, any> = {}) {
  return {
    config: { defaults: {}, runtime: {} },
    mode: "lite",
    advancedShowAll: false,
    setAdvancedShowAll: vi.fn(),
    toggleMeta: {},
    paramMeta: {},
    textMeta: {},
    stageOfToggle: vi.fn(() => "其他"),
    uiPrefs: { deliveryIncludeOptionals: false, showTaskLogs: false },
    setUiPrefs: vi.fn(),
    saveUiPrefs: vi.fn(),
    devToolsEnabled: false,
    ...overrides,
  };
}

function renderScreen(overrides: Record<string, any> = {}) {
  const ctx = buildContext(overrides);
  let exposedForm: any;

  function Wrapper() {
    const [form] = Form.useForm();
    exposedForm = form;
    React.useEffect(() => {
      form.setFieldsValue(ctx.config?.defaults || {});
    }, [form]);
    return (
      <AdvancedProvider value={{ ...ctx, form }}>
        <AdvancedScreen />
      </AdvancedProvider>
    );
  }

  const rendered = render(<Wrapper />);
  return { ...rendered, ctx, form: exposedForm };
}

describe("AdvancedScreen", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows lite-mode guidance and utility toggles", async () => {
    const user = userEvent.setup();
    const successSpy = vi.spyOn(message, "success").mockImplementation(() => undefined as any);
    const { ctx } = renderScreen();

    expect(screen.getByText("轻量模式只开放 5 个用户配置项，其余策略已固定收敛。日志与中间产物开关放在“其他”。")).toBeInTheDocument();

    const switches = screen.getAllByRole("switch");
    await user.click(switches[0]);
    await user.click(switches[1]);

    expect(ctx.setUiPrefs).toHaveBeenCalledWith(expect.objectContaining({ deliveryIncludeOptionals: true }));
    expect(ctx.saveUiPrefs).toHaveBeenCalledWith(expect.objectContaining({ deliveryIncludeOptionals: true }));
    expect(ctx.setUiPrefs).toHaveBeenCalledWith(expect.objectContaining({ showTaskLogs: true }));
    expect(ctx.saveUiPrefs).toHaveBeenCalledWith(expect.objectContaining({ showTaskLogs: true }));
    expect(successSpy).toHaveBeenCalled();
  });

  it("edits lite user parameters and saves them as defaults", () => {
    const successSpy = vi.spyOn(message, "success").mockImplementation(() => undefined as any);
    const { ctx } = renderScreen({
      config: {
        defaults: {
          skip_tts: false,
          whispercpp_threads: 4,
          min_sub_duration: 1.2,
          tts_split_len: 120,
          tts_speed_max: 1.1,
        },
        runtime: {},
      },
      toggleMeta: {
        skip_tts: { label: "跳过配音", desc: "只出字幕" },
      },
      paramMeta: {
        whispercpp_threads: { label: "识别线程数", desc: "控制 whispercpp 线程数" },
        min_sub_duration: { label: "最短字幕时长", desc: "控制字幕最短时长" },
        tts_split_len: { label: "TTS 切句长度", desc: "控制切句长度" },
        tts_speed_max: { label: "TTS 最大语速", desc: "控制最大语速" },
      },
      stageOfToggle: vi.fn(() => "其他"),
    });

    const switches = screen.getAllByRole("switch");
    fireEvent.click(switches[switches.length - 1]);
    const spinbuttons = screen.getAllByRole("spinbutton");
    fireEvent.change(spinbuttons[0], { target: { value: "8" } });
    fireEvent.change(spinbuttons[1], { target: { value: "2.5" } });
    fireEvent.change(spinbuttons[2], { target: { value: "180" } });
    fireEvent.change(spinbuttons[3], { target: { value: "1.4" } });
    fireEvent.click(screen.getAllByRole("button", { name: "保存为默认" }).slice(-1)[0]);

    expect(ctx.saveUiPrefs).toHaveBeenCalledWith(
      expect.objectContaining({
        defaultToggles: expect.objectContaining({ skip_tts: true }),
        defaultParams: expect.objectContaining({
          whispercpp_threads: 8,
          min_sub_duration: 2.5,
          tts_split_len: 180,
          tts_speed_max: 1.4,
        }),
      }),
    );
    expect(successSpy).toHaveBeenCalled();
  }, 20000);

  it("restores backend defaults into the form", () => {
    const infoSpy = vi.spyOn(message, "info").mockImplementation(() => undefined as any);
    const { form } = renderScreen({
      config: {
        defaults: {
          skip_tts: false,
          whispercpp_threads: 4,
          min_sub_duration: 1.2,
          tts_split_len: 120,
          tts_speed_max: 1.1,
        },
        runtime: {},
      },
      toggleMeta: {
        skip_tts: { label: "跳过配音", desc: "只出字幕" },
      },
      paramMeta: {
        whispercpp_threads: { label: "识别线程数", desc: "控制 whispercpp 线程数" },
        min_sub_duration: { label: "最短字幕时长", desc: "控制字幕最短时长" },
        tts_split_len: { label: "TTS 切句长度", desc: "控制切句长度" },
        tts_speed_max: { label: "TTS 最大语速", desc: "控制最大语速" },
      },
    });

    form.setFieldsValue({
      skip_tts: true,
      whispercpp_threads: 12,
      min_sub_duration: 3.2,
      tts_split_len: 240,
      tts_speed_max: 1.8,
    });

    fireEvent.click(screen.getAllByRole("button", { name: "恢复默认" }).slice(-1)[0]);

    expect(form.getFieldValue("skip_tts")).toBe(false);
    expect(form.getFieldValue("whispercpp_threads")).toBe(4);
    expect(form.getFieldValue("min_sub_duration")).toBe(1.2);
    expect(form.getFieldValue("tts_split_len")).toBe(120);
    expect(form.getFieldValue("tts_speed_max")).toBe(1.1);
    expect(infoSpy).toHaveBeenCalled();
  }, 20000);
});
