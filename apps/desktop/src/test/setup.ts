import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (!(globalThis as any).ResizeObserver) {
  (globalThis as any).ResizeObserver = ResizeObserverMock;
}

if (!(globalThis as any).matchMedia) {
  Object.defineProperty(globalThis, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

if (!(globalThis as any).IntersectionObserver) {
  (globalThis as any).IntersectionObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

if ((globalThis as any).HTMLElement && !HTMLElement.prototype.scrollIntoView) {
  HTMLElement.prototype.scrollIntoView = vi.fn();
}

if (typeof window !== "undefined" && !(window as any).scrollTo) {
  (window as any).scrollTo = vi.fn();
}

if (typeof window !== "undefined" && typeof window.getComputedStyle === "function") {
  const originalGetComputedStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = ((elt: Element, pseudoElt?: string | null) => originalGetComputedStyle(elt, pseudoElt || null)) as typeof window.getComputedStyle;
}
