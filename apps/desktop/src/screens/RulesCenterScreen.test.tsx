// @vitest-environment jsdom

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RulesCenterProvider } from "../app/contexts/RulesCenterContext";
import { RulesCenterScreen } from "./RulesCenterScreen";

function buildContext(overrides: Record<string, any> = {}) {
  return {
    rulesError: "",
    rulesLoading: false,
    onOpenRules: vi.fn(),
    onSaveGlobalRules: vi.fn(),
    globalReplaceRows: [],
    onAddGlobalReplaceRow: vi.fn(),
    onRemoveGlobalReplaceRow: vi.fn(),
    onUpdateGlobalReplaceRow: vi.fn(),
    ...overrides,
  };
}

describe("RulesCenterScreen", () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("loads rules on mount and saves silently on unmount", () => {
    const ctx = buildContext();
    const { unmount } = render(
      <RulesCenterProvider value={ctx}>
        <RulesCenterScreen />
      </RulesCenterProvider>,
    );

    expect(screen.getByText("全局规则：默认对轻量 / 质量的新任务生效。")).toBeInTheDocument();
    expect(ctx.onOpenRules).toHaveBeenCalledTimes(1);

    unmount();

    expect(ctx.onSaveGlobalRules).toHaveBeenCalledWith(undefined, { silent: true });
  });

  it("supports real editor add, edit and remove interactions", async () => {
    const user = userEvent.setup();
    const ctx = buildContext({
      globalReplaceRows: [
        { id: "asr-1", stage: "asr", src: "错字", tgt: "正字" },
        { id: "en-1", stage: "en", src: "color", tgt: "colour" },
      ],
    });
    render(
      <RulesCenterProvider value={ctx}>
        <RulesCenterScreen />
      </RulesCenterProvider>,
    );

    const addButtons = screen.getAllByRole("button", { name: "添加一条" });
    await user.click(addButtons[0]);
    await user.click(addButtons[1]);

    fireEvent.change(screen.getByDisplayValue("错字"), { target: { value: "错词" } });
    fireEvent.change(screen.getByDisplayValue("正字"), { target: { value: "正词" } });
    fireEvent.change(screen.getByDisplayValue("color"), { target: { value: "favourite color" } });
    fireEvent.change(screen.getByDisplayValue("colour"), { target: { value: "favourite colour" } });

    const deleteButtons = screen
      .getAllByRole("button")
      .filter((button) => button.textContent?.replace(/\s+/g, "") === "删除");
    await user.click(deleteButtons[0]);
    await user.click(deleteButtons[1]);

    expect(ctx.onAddGlobalReplaceRow).toHaveBeenCalledWith("asr");
    expect(ctx.onAddGlobalReplaceRow).toHaveBeenCalledWith("en");
    expect(ctx.onUpdateGlobalReplaceRow).toHaveBeenCalledWith("asr-1", { src: "错词" });
    expect(ctx.onUpdateGlobalReplaceRow).toHaveBeenCalledWith("asr-1", { tgt: "正词" });
    expect(ctx.onUpdateGlobalReplaceRow).toHaveBeenCalledWith("en-1", { src: "favourite color" });
    expect(ctx.onUpdateGlobalReplaceRow).toHaveBeenCalledWith("en-1", { tgt: "favourite colour" });
    expect(ctx.onRemoveGlobalReplaceRow).toHaveBeenCalledWith("asr-1");
    expect(ctx.onRemoveGlobalReplaceRow).toHaveBeenCalledWith("en-1");
  });
});
