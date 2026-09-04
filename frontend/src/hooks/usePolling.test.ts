import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePolling } from "./usePolling";

describe("usePolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("挂载时立即执行一次,之后按间隔重复执行", () => {
    const fn = vi.fn();
    renderHook(() => usePolling(fn, 1000));
    expect(fn).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(3000);
    expect(fn).toHaveBeenCalledTimes(4);
  });

  it("enabled=false 时不执行", () => {
    const fn = vi.fn();
    renderHook(() => usePolling(fn, 1000, false));
    vi.advanceTimersByTime(3000);
    expect(fn).not.toHaveBeenCalled();
  });

  it("卸载后停止轮询", () => {
    const fn = vi.fn();
    const { unmount } = renderHook(() => usePolling(fn, 1000));
    unmount();
    vi.advanceTimersByTime(3000);
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
