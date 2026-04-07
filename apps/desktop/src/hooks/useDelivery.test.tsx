// @vitest-environment jsdom

import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useDelivery } from "./useDelivery";

vi.mock("../api", () => ({
  downloadTaskFileBytes: vi.fn(),
  getArtifacts: vi.fn(),
}));

describe("useDelivery", () => {
  beforeEach(() => {
    (window as any).bridge = {
      getDefaultOutputsRoot: vi.fn().mockResolvedValue("C:/app/user_data/outputs"),
    };
  });

  it("prefers runtime outputs root from main process", async () => {
    const { result } = renderHook(() =>
      useDelivery({
        batchesRef: { current: [] } as any,
        updateActiveBatchById: vi.fn(),
        openPath: vi.fn(),
      })
    );

    await expect(result.current.getDefaultOutputsRoot()).resolves.toBe("C:/app/user_data/outputs");
    expect((window as any).bridge.getDefaultOutputsRoot).toHaveBeenCalledTimes(1);
  });
});
