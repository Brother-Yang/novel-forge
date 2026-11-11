import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider, Dropdown, Modal, Popconfirm } from "antd";
import { afterEach, expect, it, vi } from "vitest";
import { http } from "../api/client";
import { useBook } from "../hooks/useBook";
import { useAppStore } from "../store";
import AuthGate from "./AuthGate";
import AuthSignOut from "./AuthSignOut";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  useAppStore.getState().clearChapterDrafts();
});

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

it("会话失效隐藏受控弹窗、下拉菜单和气泡确认，重登保留表单", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(json({ enabled: true, authenticated: true }))
    .mockResolvedValueOnce(json({ detail: "临时会话过期" }, 401))
    .mockResolvedValueOnce(json({ ok: true }))
    .mockResolvedValueOnce(json({ enabled: true, authenticated: true }));
  vi.stubGlobal("fetch", fetchMock);
  const onConfirm = vi.fn();
  const user = userEvent.setup();
  render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <AuthGate>
        <Dropdown open menu={{ items: [{ key: "test", label: "合成私密菜单项" }] }}>
          <button>打开菜单</button>
        </Dropdown>
        <Popconfirm open title="删除合成私密书名？" onConfirm={onConfirm}>
          <button>删除</button>
        </Popconfirm>
        <Modal open title="合成私密书稿" footer={null}>
          <input aria-label="新书名称" defaultValue="尚未提交的名称" />
        </Modal>
      </AuthGate>
    </ConfigProvider>
  );
  const form = await screen.findByLabelText("新书名称");
  const popup = await screen.findByText("删除合成私密书名？");
  const menu = await screen.findByText("合成私密菜单项");
  await waitFor(() => {
    expect(form).toBeVisible();
    expect(popup).toBeVisible();
    expect(menu).toBeVisible();
  });

  await act(async () => {
    await http.get("/books").catch(() => undefined);
  });
  expect(await screen.findByLabelText("访问密码")).toBeVisible();
  expect(form).not.toBeVisible();
  expect(popup).not.toBeVisible();
  expect(menu).not.toBeVisible();

  await user.type(screen.getByLabelText("访问密码"), "synthetic");
  await user.click(screen.getByRole("button", { name: /登\s*录$/ }));
  await waitFor(() => expect(form).toBeVisible());
  expect(form).toHaveValue("尚未提交的名称");
  expect(onConfirm).not.toHaveBeenCalled();
});

it("存在未保存章节时提示离开窗口，草稿清除后不再提示", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValueOnce(json({ enabled: false, authenticated: true }))
  );
  render(
    <AuthGate>
      <p>已进入工作区</p>
    </AuthGate>
  );
  await screen.findByText("已进入工作区");
  const leave = () => window.dispatchEvent(new Event("beforeunload", { cancelable: true }));
  expect(leave()).toBe(true);
  act(() => {
    useAppStore.getState().setChapterDraft("book:chapter", {
      body: "本窗口保留的草稿",
      base: { body: "服务器已保存的正文", version: "original" },
    });
  });
  expect(leave()).toBe(false);
  act(() => useAppStore.getState().clearChapterDrafts());
  expect(leave()).toBe(true);
});

it("有本地草稿时可取消退出，确认退出才清除草稿", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(json({ enabled: true, authenticated: true }))
    .mockResolvedValueOnce(json({ ok: true }));
  const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("confirm", confirm);
  useAppStore.getState().setChapterDraft("book:chapter", {
    body: "本地草稿",
    base: { body: "原文", version: "original" },
  });
  const user = userEvent.setup();
  render(
    <AuthGate>
      <AuthSignOut />
    </AuthGate>
  );
  const logout = await screen.findByRole("button", { name: "退出登录" });
  await user.click(logout);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(useAppStore.getState().chapterDrafts["book:chapter"]?.body).toBe("本地草稿");
  await user.click(logout);
  expect(await screen.findByLabelText("访问密码")).toBeVisible();
  expect(fetchMock.mock.calls[1][0]).toBe("/api/auth/logout");
  expect(useAppStore.getState().chapterDrafts).toEqual({});
});
