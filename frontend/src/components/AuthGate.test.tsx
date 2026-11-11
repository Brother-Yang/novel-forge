import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { afterEach, expect, it, vi } from "vitest";
import { http } from "../api/client";
import { useBook } from "../hooks/useBook";
import AuthGate from "./AuthGate";
import AuthSignOut from "./AuthSignOut";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

it("未经认证不挂载工作台，错误密码可重试，退出登录移除工作台", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(json({ enabled: true, authenticated: false }))
    .mockResolvedValueOnce(json({ detail: "访问密码不正确" }, 401))
    .mockResolvedValueOnce(json({ ok: true }))
    .mockResolvedValueOnce(json({ enabled: true, authenticated: true }))
    .mockResolvedValueOnce(json({ ok: true }));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();

  render(
    <AntdApp>
      <AuthGate>
        <p>受保护的工作台</p>
        <AuthSignOut />
      </AuthGate>
    </AntdApp>
  );

  const password = await screen.findByLabelText("访问密码");
  expect(screen.queryByText("受保护的工作台")).not.toBeInTheDocument();
  await user.type(password, "wrong");
  await user.click(screen.getByRole("button", { name: /^登\s*录$/ }));
  expect(await screen.findByText(/访问密码不正确/)).toBeVisible();

  await user.clear(password);
  await user.type(password, "test-input");
  await user.click(screen.getByRole("button", { name: /^登\s*录$/ }));
  expect(await screen.findByText("受保护的工作台")).toBeVisible();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/auth/login",
    expect.objectContaining({
      method: "POST",
      credentials: "include",
      body: JSON.stringify({ password: "test-input" }),
    })
  );

  await user.click(screen.getByRole("button", { name: "退出登录" }));
  expect(await screen.findByLabelText("访问密码")).toBeVisible();
  expect(screen.queryByText("受保护的工作台")).not.toBeInTheDocument();
});

it("会话过期隐藏工作台，重新登录后保留未保存的草稿", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(json({ enabled: true, authenticated: true }))
      .mockResolvedValueOnce(json({ detail: "会话已过期" }, 401))
      .mockResolvedValueOnce(json({ ok: true }))
      .mockResolvedValueOnce(json({ enabled: true, authenticated: true }))
  );
  const user = userEvent.setup();
  render(
    <AuthGate>
      <textarea aria-label="正文草稿" />
      <button onClick={() => void http.get("/books").catch(() => undefined)}>刷新</button>
    </AuthGate>
  );

  const draft = await screen.findByLabelText("正文草稿");
  await user.type(draft, "未保存的草稿");
  await user.click(screen.getByRole("button", { name: "刷新" }));
  expect(await screen.findByText(/登录已失效/)).toBeVisible();
  expect(draft).not.toBeVisible();

  await user.type(screen.getByLabelText("访问密码"), "test-input");
  await user.click(screen.getByRole("button", { name: /^登\s*录$/ }));
  expect(await screen.findByRole("textbox", { name: "正文草稿" })).toHaveValue("未保存的草稿");
});

it("本地免登录工作台在会话检查失败后可重试", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockRejectedValueOnce(new Error("服务暂不可用"))
      .mockResolvedValueOnce(json({ enabled: false, authenticated: true }))
  );
  render(
    <AuthGate>
      <p>工作台</p>
      <AuthSignOut />
    </AuthGate>
  );

  expect(await screen.findByText("服务暂不可用")).toBeVisible();
  await userEvent.setup().click(screen.getByRole("button", { name: /^重\s*试$/ }));
  expect(await screen.findByText("工作台")).toBeVisible();
  expect(screen.queryByRole("button", { name: "退出登录" })).not.toBeInTheDocument();
});

it("首次读取书籍遇到会话过期，重新登录后自动重读书籍", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(json({ enabled: true, authenticated: true }))
    .mockResolvedValueOnce(json({ detail: "临时会话过期" }, 401))
    .mockResolvedValueOnce(json({ ok: true }))
    .mockResolvedValueOnce(json({ enabled: true, authenticated: true }))
    .mockResolvedValueOnce(json({ id: "test-book", title: "恢复读取的合成书" }));
  vi.stubGlobal("fetch", fetchMock);

  function BookProbe() {
    const { book, error } = useBook("test-book", false);
    return <p>{book?.title ?? error ?? "读取中"}</p>;
  }

  const user = userEvent.setup();
  render(
    <AuthGate>
      <BookProbe />
    </AuthGate>
  );
  await screen.findByText(/登录已失效/);
  await user.type(screen.getByLabelText("访问密码"), "synthetic");
  await user.click(screen.getByRole("button", { name: /登\s*录$/ }));
  expect(await screen.findByText("恢复读取的合成书")).toBeVisible();
  expect(fetchMock.mock.calls.filter(([url]) => url === "/api/books/test-book")).toHaveLength(2);
});

it("会话失效关闭工作区的确认弹窗，重新登录不重放旧删除", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(json({ enabled: true, authenticated: true }))
    .mockResolvedValueOnce(json({ detail: "临时会话过期" }, 401))
    .mockResolvedValueOnce(json({ ok: true }))
    .mockResolvedValueOnce(json({ enabled: true, authenticated: true }));
  vi.stubGlobal("fetch", fetchMock);

  function DeleteProbe() {
    const { modal } = AntdApp.useApp();
    return (
      <>
        <textarea aria-label="合成草稿" defaultValue="继续保留的草稿" />
        <button
          onClick={async () => {
            await modal.confirm({
              title: "删除合成私密卷名",
              okText: "删除卷",
              onOk: () => http.del("/volumes/synthetic"),
            });
          }}
        >
          打开删除确认
        </button>
      </>
    );
  }

  const user = userEvent.setup();
  render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <AntdApp>
        <AuthGate>
          <DeleteProbe />
        </AuthGate>
      </AntdApp>
    </ConfigProvider>
  );
  await user.click(await screen.findByRole("button", { name: "打开删除确认" }));
  const confirmation = await screen.findByRole("dialog");
  expect(confirmation).toHaveTextContent("合成私密卷名");
  await user.click(within(confirmation).getByRole("button", { name: "删除卷" }));
  await screen.findByText(/登录已失效/);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await user.type(screen.getByLabelText("访问密码"), "synthetic");
  await user.click(screen.getByRole("button", { name: /登\s*录$/ }));
  expect(await screen.findByLabelText("合成草稿")).toHaveValue("继续保留的草稿");
  expect(screen.queryByText("删除合成私密卷名")).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([url]) => url === "/api/volumes/synthetic")).toHaveLength(1);
});
