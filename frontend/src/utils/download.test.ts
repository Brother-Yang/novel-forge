import { afterEach, expect, it, vi } from "vitest";
import { onUnauthorized } from "../api/client";
import { downloadFile } from "./download";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("用当前会话下载，保留 UTF-8 文件名并释放临时 URL", async () => {
  vi.useFakeTimers();
  const response = new Response("书稿正文", {
    headers: {
      "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent("小说.txt")}`,
    },
  });
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  const createObjectURL = vi.fn().mockReturnValue("blob:manuscript");
  const revokeObjectURL = vi.fn();
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
  const clicks: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement
  ) {
    clicks.push(this.download);
  });

  await downloadFile("/api/books/test/export?format=txt", "fallback.txt");
  expect(fetchMock).toHaveBeenCalledWith("/api/books/test/export?format=txt", {
    credentials: "include",
  });
  expect(clicks).toEqual(["小说.txt"]);
  await vi.advanceTimersByTimeAsync(1000);
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:manuscript");
});

it("未登录时提示错误并触发登录，不保存 JSON 错误为书稿", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response('{"detail":"请先登录"}', { status: 401 }))
  );
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  const expired = vi.fn();
  const unsubscribe = onUnauthorized(expired);
  try {
    await expect(downloadFile("/api/books/test/export?format=txt", "fallback.txt")).rejects.toThrow(
      "请先登录"
    );
    expect(click).not.toHaveBeenCalled();
    expect(expired).toHaveBeenCalledTimes(1);
  } finally {
    unsubscribe();
  }
});
