// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { message } from "antd";
import { ModeSelectProvider, type ModeSelectContextValue } from "../app/contexts/ModeSelectContext";
import { ModeSelectScreen } from "./ModeSelectScreen";

function buildContext(overrides: Partial<ModeSelectContextValue> = {}): ModeSelectContextValue {
  return {
    availableModes: ["lite", "quality", "online"],
    mode: "quality",
    config: { ui: {} } as any,
    uiPrefs: {},
    setMode: vi.fn(),
    setUiPrefs: vi.fn(),
    saveUiPrefs: vi.fn(),
    ...overrides,
  };
}

function renderScreen(ctx: ModeSelectContextValue) {
  return render(
    <ModeSelectProvider value={ctx}>
      <ModeSelectScreen />
    </ModeSelectProvider>,
  );
}

describe("ModeSelectScreen", () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("switches to lite mode and persists default mode", async () => {
    const user = userEvent.setup();
    const successSpy = vi.spyOn(message, "success").mockImplementation(() => undefined as any);
    const ctx = buildContext();

    renderScreen(ctx);

    await user.click(screen.getAllByText("轻量模式")[0]);

    expect(ctx.setMode).toHaveBeenCalledWith("lite");
    expect(ctx.setUiPrefs).toHaveBeenCalledWith(expect.objectContaining({ defaultMode: "lite" }));
    expect(ctx.saveUiPrefs).toHaveBeenCalledWith(expect.objectContaining({ defaultMode: "lite" }));
    expect(successSpy).toHaveBeenCalled();
  });

  it("shows quality teaser without switching runtime mode", async () => {
    const user = userEvent.setup();
    vi.spyOn(message, "info").mockImplementation(() => undefined as any);
    const ctx = buildContext({
      mode: "lite",
      config: { ui: { quality_teaser_only: true } } as any,
    });

    renderScreen(ctx);

    await user.click(screen.getAllByText("质量模式")[0]);

    expect(ctx.setMode).not.toHaveBeenCalled();
    expect(ctx.saveUiPrefs).not.toHaveBeenCalled();
  });

  it("disables online mode when product config turns it off", async () => {
    const user = userEvent.setup();
    const ctx = buildContext({
      config: { ui: { online_disabled: true } } as any,
    });

    renderScreen(ctx);

    expect(screen.getByText("暂未开放")).toBeInTheDocument();
    await user.click(screen.getAllByText("在线模式")[0]);

    expect(ctx.setMode).not.toHaveBeenCalled();
  });

  it("disables lite and online cards in quality-only builds", async () => {
    const user = userEvent.setup();
    const ctx = buildContext({
      config: { ui: { quality_only: true } } as any,
    });

    renderScreen(ctx);

    expect(screen.getAllByText("不可用")).toHaveLength(2);
    await user.click(screen.getAllByText("轻量模式")[0]);
    await user.click(screen.getAllByText("在线模式")[0]);

    expect(ctx.setMode).not.toHaveBeenCalled();
  });

  it("keeps online mode unavailable when available_modes_detail marks it unsupported", async () => {
    const user = userEvent.setup();
    const ctx = buildContext({
      availableModes: ["lite", "quality"],
      config: {
        ui: {},
        available_modes_detail: {
          online: {
            reasons: ["缺少在线密钥", "当前机器未联网"],
          },
        },
      } as any,
    });

    renderScreen(ctx);

    const unavailableTags = screen.getAllByText("不可用");
    expect(unavailableTags.length).toBeGreaterThan(0);
    expect(unavailableTags[0].getAttribute("aria-describedby")).toBeTruthy();
    await user.click(screen.getAllByText("在线模式")[0]);

    expect(ctx.setMode).not.toHaveBeenCalledWith("online");
  });
});
