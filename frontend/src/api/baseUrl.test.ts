import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

it.each([
  { value: undefined, base: "/api" },
  { value: "", base: "/api" },
  { value: " \t ", base: "/api" },
  { value: "/proxy/api///", base: "/proxy/api" },
  { value: " https://api.example.com/api/ ", base: "https://api.example.com/api" },
  { value: "/", base: "" },
])("REST、SSE 和导出共用归一化后的 API 地址 $value", async ({ value, base }) => {
  vi.stubEnv("VITE_API_BASE_URL", value);
  const { http, sseFetch } = await import("./client");
  const { api, sseEndpoints } = await import("./index");
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response('{"authenticated":true}'))
    .mockResolvedValueOnce(
      new Response("event: done\ndata:\n\n", { headers: { "Content-Type": "text/event-stream" } })
    );
  vi.stubGlobal("fetch", fetch);

  await expect(http.get("/auth/session")).resolves.toEqual({ authenticated: true });
  expect(fetch).toHaveBeenNthCalledWith(
    1,
    `${base}/auth/session`,
    expect.objectContaining({ credentials: "include" })
  );
  const onEvent = vi.fn();
  await sseFetch(sseEndpoints.chapter, { chapter_id: "test" }, { onEvent });
  expect(fetch).toHaveBeenNthCalledWith(
    2,
    `${base}/generate/chapter`,
    expect.objectContaining({ credentials: "include", method: "POST" })
  );
  expect(onEvent).toHaveBeenCalledWith("done", "");
  expect(api.exportUrl("book", "epub")).toBe(`${base}/books/book/export?format=epub`);
});
