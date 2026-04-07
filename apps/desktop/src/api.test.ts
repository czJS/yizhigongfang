// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

let requestInterceptor: ((config: any) => any) | undefined;

const client = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  interceptors: {
    request: {
      use: vi.fn((fn) => {
        requestInterceptor = fn;
      }),
    },
  },
};

vi.mock("axios", async () => {
  const actual = await vi.importActual<typeof import("axios")>("axios");
  return {
    ...actual,
    default: {
      create: vi.fn(() => client),
    },
  };
});

describe("desktop api client", () => {
  beforeEach(() => {
    vi.resetModules();
    requestInterceptor = undefined;
    localStorage.clear();
    (window as any).bridge = { apiToken: "local-api-token" };
  });

  it("attaches both local backend token and cloud auth token", async () => {
    localStorage.setItem("ygf_auth_token", "cloud-auth-token");

    await import("./api");
    const config = requestInterceptor?.({ headers: {} });
    const headers = config?.headers;

    expect(headers?.get("X-YGF-Token")).toBe("local-api-token");
    expect(headers?.get("X-YGF-Cloud-Token")).toBe("cloud-auth-token");
  });
});
