import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  if (typeof document !== "undefined") cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  if (typeof window !== "undefined") {
    window.localStorage.clear();
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
  }
});
