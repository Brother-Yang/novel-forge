import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntdApp, ConfigProvider } from "antd";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { BookTree } from "../api/types";
import CatalogTree from "./CatalogTree";
import { useAppStore } from "../store";

vi.mock("../api", () => ({
  api: {
    getTree: vi.fn(),
    deleteVolume: vi.fn(),
    deleteChapter: vi.fn(),
    createVolume: vi.fn(),
    createChapter: vi.fn(),
    updateVolume: vi.fn(),
    updateChapter: vi.fn(),
  },
}));

const chapter = {
  id: "chapter-1",
  title: "第一章",
  order_index: 0,
  status: "done",
  word_count: 2000,
};
const tree: BookTree = {
  volumes: [{ id: "volume-1", title: "第一卷", order_index: 0, chapters: [chapter] }],
  loose_chapters: [],
};

function Harness() {
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <ConfigProvider theme={{ token: { motion: false } }}>
      <AntdApp>
        <CatalogTree bookId="book-1" selectedChapter={selected} onSelectChapter={setSelected} />
        <output aria-label="当前章节">{selected ?? "未选择"}</output>
      </AntdApp>
    </ConfigProvider>
  );
}

async function openDeleteMenu(index = 0) {
  const user = userEvent.setup();
  await screen.findByText("第一章");
  await user.click(screen.getAllByRole("img", { name: "more" })[index]);
  await user.click(await screen.findByRole("menuitem", { name: "删除" }));
  return user;
}

describe("CatalogTree 删除保护", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.getTree).mockResolvedValue(tree);
    vi.mocked(api.deleteVolume).mockResolvedValue({ ok: true });
    vi.mocked(api.deleteChapter).mockResolvedValue({ ok: true });
  });

  it("删除卷前说明章节会保留，取消时不发删除请求", async () => {
    render(<Harness />);
    const user = await openDeleteMenu();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/章节及正文会保留并移到“未归卷”/)).toBeInTheDocument();
    expect(api.deleteVolume).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: /取消|取 消/ }));
    expect(api.deleteVolume).not.toHaveBeenCalled();
    expect(screen.getByText("第一章")).toBeInTheDocument();
  });

  it("确认删除卷后重新加载目录并保留当前章节选择", async () => {
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(await screen.findByText("第一章"));
    vi.mocked(api.getTree).mockResolvedValue({ volumes: [], loose_chapters: [chapter] });
    await openDeleteMenu();
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "删除卷" }));
    await waitFor(() => expect(api.deleteVolume).toHaveBeenCalledWith("volume-1"));
    await waitFor(() => expect(screen.queryByText("第一卷")).not.toBeInTheDocument());
    expect(screen.getByText("第一章")).toBeInTheDocument();
    expect(screen.getByLabelText("当前章节")).toHaveTextContent("chapter-1");
  });

  it("删除失败会提示错误并保留确认框，允许重试", async () => {
    vi.mocked(api.deleteVolume).mockRejectedValueOnce(new Error("删除失败，请重试"));
    render(<Harness />);
    const user = await openDeleteMenu();
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "删除卷" }));
    expect(await screen.findByText("删除失败，请重试")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("第一章")).toBeInTheDocument();
    const retry = within(dialog).getByRole("button", { name: /删除卷$/ });
    await waitFor(() => expect(retry).not.toHaveClass("ant-btn-loading"));
    await user.click(retry);
    await waitFor(() => expect(api.deleteVolume).toHaveBeenCalledTimes(2));
  });

  it("选择章节后从目录菜单删除该章，确认后清除当前选择", async () => {
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(await screen.findByText("第一章"));
    await openDeleteMenu(1);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/正文和摘要/)).toBeInTheDocument();
    expect(api.deleteChapter).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "删除章节" }));
    await waitFor(() => expect(api.deleteChapter).toHaveBeenCalledWith("chapter-1"));
    expect(screen.getByLabelText("当前章节")).toHaveTextContent("未选择");
  });

  it("加载失败有重试入口，成功重试后显示目录", async () => {
    vi.mocked(api.getTree).mockRejectedValueOnce(new Error("目录暂时不可用"));
    render(<Harness />);
    const user = userEvent.setup();
    expect(await screen.findByText("目录暂时不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新增卷" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "新增章节" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /重试|重 试/ }));
    expect(await screen.findByText("第一章")).toBeInTheDocument();
    expect(screen.queryByText("目录暂时不可用")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新增卷" })).toBeEnabled();
  });

  it("删除中间卷后新增卷使用现有最大排序索引加一", async () => {
    vi.mocked(api.getTree).mockResolvedValue({
      ...tree,
      volumes: [
        ...tree.volumes,
        { id: "volume-3", title: "第 3 卷", order_index: 2, chapters: [] },
      ],
    });
    render(<Harness />);
    const user = userEvent.setup();
    await screen.findByText("第 3 卷");
    await user.click(screen.getByRole("button", { name: "新增卷" }));
    expect(api.createVolume).toHaveBeenCalledWith({
      book_id: "book-1",
      title: "第 4 卷",
      order_index: 3,
    });
  });

  it("删除前的旧加载响应不能把已删除卷重新显示", async () => {
    render(<Harness />);
    await screen.findByText("第一章");
    let resolveOld!: (value: BookTree) => void;
    vi.mocked(api.getTree).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveOld = resolve;
      })
    );
    act(() => useAppStore.getState().refreshTree());
    await waitFor(() => expect(api.getTree).toHaveBeenCalledTimes(2));
    vi.mocked(api.getTree).mockResolvedValue({ volumes: [], loose_chapters: [chapter] });
    const user = await openDeleteMenu();
    await user.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "删除卷" })
    );
    await waitFor(() => expect(screen.queryByText("第一卷")).not.toBeInTheDocument());
    await act(async () => resolveOld(tree));
    expect(screen.queryByText("第一卷")).not.toBeInTheDocument();
    expect(screen.getByText("第一章")).toBeInTheDocument();
  });

  it("新增与重命名失败均可见，重命名弹窗保留原输入以便重试", async () => {
    vi.mocked(api.createVolume).mockRejectedValue(new Error("新增卷失败"));
    vi.mocked(api.updateVolume).mockRejectedValueOnce(new Error("重命名失败"));
    render(<Harness />);
    const user = userEvent.setup();
    await screen.findByText("第一章");
    await user.click(screen.getByRole("button", { name: "新增卷" }));
    expect(await screen.findByText("新增卷失败")).toBeInTheDocument();
    await user.click(screen.getAllByRole("img", { name: "more" })[0]);
    await user.click(await screen.findByRole("menuitem", { name: "重命名" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByDisplayValue("第一卷");
    await user.clear(input);
    await user.type(input, "修改后的卷名");
    await user.click(within(dialog).getByRole("button", { name: /保 存|保存/ }));
    expect(await screen.findByText("重命名失败")).toBeInTheDocument();
    expect(input).toHaveValue("修改后的卷名");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
