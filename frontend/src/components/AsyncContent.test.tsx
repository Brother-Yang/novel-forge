import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import AsyncContent from "./AsyncContent";

afterEach(() => vi.restoreAllMocks());

describe("按需内容加载", () => {
  it("模块加载期间显示状态，完成后透传参数", async () => {
    let resolve!: (module: { default: (props: { text: string }) => JSX.Element }) => void;
    const load = vi.fn(
      () =>
        new Promise<{ default: (props: { text: string }) => JSX.Element }>((done) => {
          resolve = done;
        })
    );
    render(<AsyncContent load={load} componentProps={{ text: "已就绪" }} label="编辑器" />);
    expect(screen.getByRole("status", { name: "编辑器加载中" })).toBeInTheDocument();
    await act(async () => resolve({ default: ({ text }) => <div>{text}</div> }));
    expect(await screen.findByText("已就绪")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("下载模块失败后可重试，重新执行加载而非使用失败缓存", async () => {
    // 只拦截此用例故意制造、随后被 ErrorBoundary 恢复的错误。
    const failure = new Error("网络中断");
    const ignoreExpectedError = (event: ErrorEvent) => {
      if (event.error === failure) event.preventDefault();
    };
    const originalError = console.error;
    vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
      if (args.includes(failure) || String(args[0]).includes("The above error occurred")) return;
      originalError(...args);
    });
    window.addEventListener("error", ignoreExpectedError);
    const load = vi
      .fn()
      .mockRejectedValueOnce(failure)
      .mockResolvedValueOnce({ default: () => <div>编辑器已恢复</div> });
    try {
      render(<AsyncContent load={load} componentProps={{}} label="编辑器" />);
      expect(await screen.findByText("编辑器加载失败")).toBeInTheDocument();
      await userEvent.setup().click(screen.getByRole("button", { name: "重试加载" }));
      expect(await screen.findByText("编辑器已恢复")).toBeInTheDocument();
      expect(load).toHaveBeenCalledTimes(2);
    } finally {
      window.removeEventListener("error", ignoreExpectedError);
    }
  });
});
