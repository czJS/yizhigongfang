// @vitest-environment jsdom

import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CleanupArtifactsModal } from "./CleanupArtifactsModal";

function renderModal() {
  const onCancel = vi.fn();
  const onConfirm = vi.fn();

  function Wrapper() {
    const [includeDiagnostics, setIncludeDiagnostics] = React.useState(true);
    const [includeResume, setIncludeResume] = React.useState(false);
    const [includeReview, setIncludeReview] = React.useState(false);

    return (
      <CleanupArtifactsModal
        open
        includeDiagnostics={includeDiagnostics}
        includeResume={includeResume}
        includeReview={includeReview}
        setIncludeDiagnostics={setIncludeDiagnostics}
        setIncludeResume={setIncludeResume}
        setIncludeReview={setIncludeReview}
        onCancel={onCancel}
        onConfirm={onConfirm}
      />
    );
  }

  const rendered = render(<Wrapper />);
  return { ...rendered, onCancel, onConfirm };
}

describe("CleanupArtifactsModal", () => {
  beforeEach(() => {
    cleanup();
  });

  it("toggles cleanup scopes and disables confirm when nothing is selected", async () => {
    const user = userEvent.setup();
    const { onConfirm } = renderModal();

    const diagnostics = screen.getByRole("checkbox", { name: "清理诊断/日志文件（建议）" });
    const resume = screen.getByRole("checkbox", { name: "清理断点续跑文件（可能无法继续从上次继续）" });
    const review = screen.getByRole("checkbox", { name: "清理审校文件（将丢失审校稿）" });

    await user.click(resume);
    await user.click(review);
    await user.click(diagnostics);

    expect(resume).toBeChecked();
    expect(review).toBeChecked();
    expect(diagnostics).not.toBeChecked();

    await user.click(resume);
    await user.click(review);

    expect(screen.getByRole("button", { name: "开始清理" })).toBeDisabled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("submits and cancels from the modal footer", async () => {
    const user = userEvent.setup();
    const { onCancel, onConfirm } = renderModal();

    await user.click(screen.getAllByRole("button", { name: "开始清理" }).slice(-1)[0]);
    await user.click(screen.getAllByRole("button", { name: /取\s*消/ }).slice(-1)[0]);

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
